"""
주문 실행 모듈 — 매수/매도 신호를 실제 키움 API 주문으로 변환
"""
import os
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.core.kiwoom_client import get_kiwoom_client
from app.db.models import (
    Position, Order, BuySignal,
    OrderType, OrderStatus, PositionStatus,
)

ACCOUNT_NO = os.getenv("KIWOOM_ACCOUNT_NO", "")  # .env에 KIWOOM_ACCOUNT_NO 설정 필요


async def execute_pending_buy_orders(db: AsyncSession):
    """
    장 시작 전(08:50) 실행 — 전날 감지된 매수 신호를 주문으로 전송
    매수 가격: 신호 발생 당일 종가 (target_order_price)
    """
    client = get_kiwoom_client()

    stmt = select(BuySignal).where(BuySignal.is_executed == False)
    signals = (await db.execute(stmt)).scalars().all()

    for sig in signals:
        try:
            qty = _calc_buy_qty(sig.target_order_price)
            if qty <= 0:
                continue

            resp = await client.place_order(
                account_no=ACCOUNT_NO,
                stock_code=sig.stock_code,
                order_type="buy",
                quantity=qty,
                price=sig.target_order_price,
            )

            order_no = resp.get("output", {}).get("KRX_FWDG_ORD_ORGNO", "")

            # 주문 기록
            order = Order(
                stock_code=sig.stock_code,
                stock_name="",
                order_type=OrderType.BUY,
                order_round=sig.trigger_round,
                order_price=sig.target_order_price,
                order_qty=qty,
                kiwoom_order_no=order_no,
                status=OrderStatus.SUBMITTED,
            )
            db.add(order)

            sig.is_executed = True
            await db.commit()

            # 포지션 업데이트 (체결 가정 — 실제로는 WebSocket 체결 이벤트로 처리)
            await _update_position_on_buy(db, sig, qty, sig.target_order_price, order)

            print(f"[executor] 매수 주문 전송: {sig.stock_code} {qty}주 @{sig.target_order_price:,}원 ({sig.trigger_round}차)")

        except Exception as e:
            print(f"[executor] 매수 주문 오류 ({sig.stock_code}): {e}")


async def execute_sell_order(
    db: AsyncSession,
    position: Position,
    sell_round: int,
    current_price: int,
) -> bool:
    """매도 주문 실행"""
    client = get_kiwoom_client()

    sell_qty = max(1, round(position.quantity * settings.SELL_QUANTITY_RATIO))

    try:
        resp = await client.place_order(
            account_no=ACCOUNT_NO,
            stock_code=position.stock_code,
            order_type="sell",
            quantity=sell_qty,
            price=current_price,
        )
        order_no = resp.get("output", {}).get("KRX_FWDG_ORD_ORGNO", "")

        order = Order(
            position_id=position.id,
            stock_code=position.stock_code,
            stock_name=position.stock_name,
            order_type=OrderType.SELL,
            order_round=sell_round,
            order_price=current_price,
            order_qty=sell_qty,
            kiwoom_order_no=order_no,
            status=OrderStatus.SUBMITTED,
        )
        db.add(order)

        # 포지션 업데이트
        position.sell_rounds_done = sell_round
        position.quantity -= sell_qty

        # 3차 매도 완료 시 추가 매수 기준 저점 업데이트
        if sell_round == settings.EXTRA_BUY_MIN_SELL_ROUNDS:
            position.extra_buy_low = current_price

        # 5차 매도 완료 → 포지션 청산
        if position.quantity <= 0 or sell_round == 5:
            position.status = PositionStatus.CLOSED
            position.closed_at = datetime.utcnow()

        await db.commit()
        print(f"[executor] 매도 주문 전송: {position.stock_code} {sell_qty}주 @{current_price:,}원 ({sell_round}차)")
        return True

    except Exception as e:
        print(f"[executor] 매도 주문 오류 ({position.stock_code}): {e}")
        return False


async def _update_position_on_buy(
    db: AsyncSession,
    signal: BuySignal,
    qty: int,
    price: int,
    order: Order,
):
    """매수 체결 후 포지션 업데이트"""
    stmt = select(Position).where(
        Position.stock_code == signal.stock_code,
        Position.status == PositionStatus.ACTIVE,
    )
    position = (await db.execute(stmt)).scalar_one_or_none()

    if position is None:
        position = Position(
            stock_code=signal.stock_code,
            stock_name="",
            buy_rounds_done=0,
            sell_rounds_done=0,
            quantity=0,
            avg_buy_price=0,
            total_buy_amount=0,
        )
        db.add(position)

    total_before = position.avg_buy_price * position.quantity
    position.quantity += qty
    position.total_buy_amount += price * qty
    position.avg_buy_price = position.total_buy_amount / position.quantity
    position.buy_rounds_done = signal.trigger_round
    order.position_id = position.id

    await db.commit()


def _calc_buy_qty(price: int) -> int:
    """1회 매수 수량 계산 (총 투자금의 2%)"""
    if price <= 0:
        return 0
    buy_amount = settings.TOTAL_INVESTMENT * settings.BUY_RATIO_PER_ROUND
    return int(buy_amount // price)
