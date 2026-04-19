"""
주문 실행 모듈 — 매수/매도 신호를 실제 키움 API 주문으로 변환한다.

원칙: 이 모듈은 Order 레코드만 만들고 Position은 건드리지 않는다.
       Position 갱신은 WebSocket 주문체결(00) 이벤트에서 처리하여
       체결/취소/거부를 신뢰성 있게 반영한다.

중복 주문 가드: 같은 차수에 대해 열려있는(SUBMITTED) 주문이 있으면 재전송하지 않는다.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.core.kiwoom_client import KiwoomClient
from app.core.notifier import notify_user_fire
from app.db.models import (
    Position, Order, BuySignal,
    OrderType, OrderStatus,
)
from app.db.user_config import get_total_investment


async def execute_pending_buy_orders(
    db: AsyncSession, user_id: int, client: KiwoomClient,
):
    """장 시작 전(08:50) — 해당 유저의 전날 감지된 매수 신호를 지정가 주문으로 전송"""
    total_invest = await get_total_investment(db, user_id)
    signals = (
        await db.execute(
            select(BuySignal).where(
                BuySignal.is_executed == False,  # noqa: E712
                BuySignal.user_id == user_id,
            )
        )
    ).scalars().all()

    for sig in signals:
        # 유저가 제외한 신호는 주문하지 않고, 재평가 대상에서 빼기 위해 executed 처리
        if sig.is_excluded:
            sig.is_executed = True
            await db.commit()
            print(f"[executor] {sig.stock_code} {sig.trigger_round}차 — 유저가 제외 — 스킵")
            continue

        try:
            qty = calc_buy_qty(sig.target_order_price, total_invest)
            if qty <= 0:
                continue

            if await _has_open_order(
                db, user_id, sig.stock_code, OrderType.BUY, sig.trigger_round
            ):
                print(f"[executor] {sig.stock_code} {sig.trigger_round}차 매수 주문 이미 진행중 — 스킵")
                sig.is_executed = True
                await db.commit()
                continue

            resp = await client.place_order(
                stock_code=sig.stock_code,
                order_type="buy",
                quantity=qty,
                price=sig.target_order_price,
                trade_type="0",
            )
            order_no = resp.get("ord_no", "")

            order = Order(
                user_id=user_id,
                stock_code=sig.stock_code,
                stock_name=sig.stock_name or "",
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

            print(f"[executor] 매수 주문 전송: {sig.stock_code} {qty}주 @{sig.target_order_price:,}원 ({sig.trigger_round}차, {sig.source}) ord_no={order_no}")
            notify_user_fire(
                user_id,
                f"📝 매수 주문 접수\n{sig.stock_name or sig.stock_code} ({sig.stock_code})\n"
                f"{sig.trigger_round}차 · {qty}주 @ {sig.target_order_price:,}원",
                dedup_key=f"order_buy:{order_no}" if order_no else None,
            )

        except Exception as e:
            print(f"[executor] 매수 주문 오류 ({sig.stock_code}): {e}")
            notify_user_fire(
                user_id,
                f"❌ 매수 주문 실패\n{sig.stock_name or sig.stock_code} ({sig.stock_code}) "
                f"{sig.trigger_round}차\n사유: {e}",
                dedup_key=f"order_buy_fail:{sig.stock_code}:{sig.trigger_round}",
            )


async def execute_sell_order(
    db: AsyncSession,
    position: Position,
    sell_round: int,
    trigger_bit: int,
    current_price: int,
    client: KiwoomClient,
) -> bool:
    """매도 주문 전송 (Position 은 건드리지 않음 — 체결 이벤트에서 갱신).

    trigger_bit: 어떤 매도 조건이 발동됐는지 식별자. 체결 시 Position.sold_triggers 에 set.
    """
    # 같은 조건(bit)으로 이미 떠있는 SUBMITTED 주문이 있으면 중복 전송 방지
    if await _has_open_sell_for_trigger(db, position, trigger_bit):
        return False

    sell_qty = max(1, round(position.quantity * settings.SELL_QUANTITY_RATIO))

    try:
        resp = await client.place_order(
            stock_code=position.stock_code,
            order_type="sell",
            quantity=sell_qty,
            price=current_price,
            trade_type="0",
        )
        order_no = resp.get("ord_no", "")

        order = Order(
            user_id=position.user_id,
            position_id=position.id,
            stock_code=position.stock_code,
            stock_name=position.stock_name,
            order_type=OrderType.SELL,
            order_round=sell_round,
            sell_trigger_bit=trigger_bit,
            order_price=current_price,
            order_qty=sell_qty,
            kiwoom_order_no=order_no,
            status=OrderStatus.SUBMITTED,
        )
        db.add(order)
        await db.commit()

        print(f"[executor] 매도 주문 전송: {position.stock_code} {sell_qty}주 @{current_price:,}원 (tranche {sell_round}, trigger bit {trigger_bit}) ord_no={order_no}")
        gain_rate = (
            (current_price - position.avg_buy_price) / position.avg_buy_price * 100
            if position.avg_buy_price > 0 else 0.0
        )
        notify_user_fire(
            position.user_id,
            f"📤 매도 주문 접수\n{position.stock_name} ({position.stock_code})\n"
            f"tranche {sell_round}/5 · {sell_qty}주 @ {current_price:,}원\n"
            f"평단 {position.avg_buy_price:,.0f}원 · {gain_rate:+.2f}%",
            dedup_key=f"order_sell:{order_no}" if order_no else None,
        )
        return True

    except Exception as e:
        print(f"[executor] 매도 주문 오류 ({position.stock_code}): {e}")
        notify_user_fire(
            position.user_id,
            f"❌ 매도 주문 실패\n{position.stock_name} ({position.stock_code}) "
            f"tranche {sell_round}\n사유: {e}",
            dedup_key=f"order_sell_fail:{position.stock_code}:{trigger_bit}",
        )
        return False


async def _has_open_sell_for_trigger(
    db: AsyncSession, position: Position, trigger_bit: int
) -> bool:
    stmt = select(Order).where(
        Order.user_id == position.user_id,
        Order.position_id == position.id,
        Order.order_type == OrderType.SELL,
        Order.sell_trigger_bit == trigger_bit,
        Order.status == OrderStatus.SUBMITTED,
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def execute_extra_buy_order(
    db: AsyncSession,
    position: Position,
    current_price: int,
    client: KiwoomClient,
) -> bool:
    """
    추가 매수 실행 — 3회 이상 매도 완료 후 저점의 90% 이하 하락 시 발동
    재발동 방지를 위해 즉시 extra_buy_low=None 처리 (실제 포지션 수량 증가는 체결 이벤트에서)
    """
    # 추가매수 차수는 order_round=0 으로 기록(구분용)
    if await _has_open_order(
        db, position.user_id, position.stock_code, OrderType.BUY, 0, position.id
    ):
        return False

    total_invest = await get_total_investment(db, position.user_id)
    qty = calc_buy_qty(current_price, total_invest)
    if qty <= 0:
        return False

    try:
        resp = await client.place_order(
            stock_code=position.stock_code,
            order_type="buy",
            quantity=qty,
            price=current_price,
            trade_type="0",
        )
        order_no = resp.get("ord_no", "")

        order = Order(
            user_id=position.user_id,
            position_id=position.id,
            stock_code=position.stock_code,
            stock_name=position.stock_name,
            order_type=OrderType.BUY,
            order_round=0,          # 0 = 추가매수
            order_price=current_price,
            order_qty=qty,
            kiwoom_order_no=order_no,
            status=OrderStatus.SUBMITTED,
        )
        db.add(order)
        position.extra_buy_low = None   # 즉시 재발동 방지
        await db.commit()

        print(f"[executor] 추가 매수 주문 전송: {position.stock_code} {qty}주 @{current_price:,}원 ord_no={order_no}")
        notify_user_fire(
            position.user_id,
            f"♻️ 추가 매수 주문 접수\n{position.stock_name} ({position.stock_code})\n"
            f"{qty}주 @ {current_price:,}원",
            dedup_key=f"order_extra:{order_no}" if order_no else None,
        )
        return True

    except Exception as e:
        print(f"[executor] 추가 매수 오류 ({position.stock_code}): {e}")
        notify_user_fire(
            position.user_id,
            f"❌ 추가 매수 실패\n{position.stock_name} ({position.stock_code})\n사유: {e}",
            dedup_key=f"order_extra_fail:{position.stock_code}",
        )
        return False


# ---------------------------------------------------------------------- #
#  헬퍼
# ---------------------------------------------------------------------- #
async def _has_open_order(
    db: AsyncSession,
    user_id: int,
    stock_code: str,
    order_type: OrderType,
    order_round: int,
    position_id: int | None = None,
) -> bool:
    """같은 유저/종목/타입/차수의 SUBMITTED 주문 존재 여부"""
    stmt = select(Order).where(
        Order.user_id == user_id,
        Order.stock_code == stock_code,
        Order.order_type == order_type,
        Order.order_round == order_round,
        Order.status == OrderStatus.SUBMITTED,
    )
    if position_id is not None:
        stmt = stmt.where(Order.position_id == position_id)
    return (await db.execute(stmt)).scalar_one_or_none() is not None


def calc_buy_qty(price: int, total_investment: float) -> int:
    """1회 매수 수량 = (총투자금 × 회당 비율) // 지정가"""
    if price <= 0:
        return 0
    buy_amount = total_investment * settings.BUY_RATIO_PER_ROUND
    return int(buy_amount // price)
