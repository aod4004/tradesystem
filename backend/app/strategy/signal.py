"""
매수/매도 신호 감지 모듈

매수 신호 (장 마감 후): 각 차수별 조건 확인 후 BuySignal 생성
매도 신호 (실시간):     실시간 체결가 기반 조건 평가
"""
from datetime import datetime
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.core.kiwoom_client import get_kiwoom_client, to_int
from app.db.models import ScreenedStock, Position, BuySignal, PositionStatus


async def detect_buy_signals(db: AsyncSession) -> list[BuySignal]:
    """
    장 마감 후 실행 — 다음날 매수 예정 신호 생성
    양봉 여부: 당일 종가 > 당일 시가 (일봉 차트 최신 봉)
    """
    client = get_kiwoom_client()
    signals: list[BuySignal] = []

    stmt = select(ScreenedStock).where(ScreenedStock.is_active == True)
    stocks = (await db.execute(stmt)).scalars().all()

    for stock in stocks:
        try:
            sig = await _check_buy_signal(client, db, stock)
            if sig:
                db.add(sig)
                signals.append(sig)
        except Exception as e:
            print(f"[signal] {stock.code} 신호 확인 오류: {e}")

    await db.commit()
    return signals


async def _check_buy_signal(
    client, db: AsyncSession, stock: ScreenedStock
) -> BuySignal | None:
    candles = await client.get_daily_chart(stock.code)
    if len(candles) < 2:
        return None

    # 최신 봉 = candles[0] (당일 종가)
    today = candles[0]
    today_open = to_int(today.get("open_pric"))
    today_close = to_int(today.get("cur_prc"))
    if today_open <= 0 or today_close <= 0:
        return None

    is_bullish = today_close > today_open
    if not is_bullish:
        return None

    # 현재 포지션
    pos_stmt = select(Position).where(
        Position.stock_code == stock.code,
        Position.status == PositionStatus.ACTIVE,
    )
    position = (await db.execute(pos_stmt)).scalar_one_or_none()

    # 오늘 이미 미실행 신호 있으면 중복 방지
    today_midnight = datetime.combine(datetime.utcnow().date(), datetime.min.time())
    dup_stmt = select(BuySignal).where(
        BuySignal.stock_code == stock.code,
        BuySignal.signal_date >= today_midnight,
        BuySignal.is_executed == False,
    )
    if (await db.execute(dup_stmt)).scalar_one_or_none():
        return None

    next_round = 1
    trigger_price = stock.high_1y * settings.HIGH_DROP_THRESHOLD

    if position:
        if position.buy_rounds_done >= settings.MAX_BUY_ROUNDS:
            return None
        next_round = position.buy_rounds_done + 1
        trigger_price = position.avg_buy_price * 0.90   # 전 차수 매수가의 90%

    current_price = stock.current_price
    if current_price >= trigger_price:
        return None

    return BuySignal(
        stock_code=stock.code,
        signal_date=datetime.utcnow(),
        trigger_round=next_round,
        trigger_price=trigger_price,
        target_order_price=today_close,   # 전날(= 신호일) 종가로 다음날 매수
        prev_close=today_close,
        prev_open=today_open,
        is_executed=False,
    )


def check_sell_signal(
    current_price: int,
    avg_buy_price: float,
    sell_rounds_done: int,
    ma20: float | None,
) -> int | None:
    """
    실시간 체결가 기반 매도 차수 판정
    반환: 실행할 차수 (없으면 None)
    """
    if sell_rounds_done >= 5:
        return None

    gain_rate = (current_price - avg_buy_price) / avg_buy_price if avg_buy_price > 0 else 0.0

    for i, threshold in enumerate(settings.SELL_RATIOS):
        round_no = i + 1
        if sell_rounds_done < round_no and gain_rate >= threshold:
            return round_no

    if sell_rounds_done < 5 and ma20 is not None and current_price >= ma20:
        return 5

    return None


def calculate_ma20(closes: list[int]) -> float | None:
    """closes = 최신순 정렬된 종가 리스트"""
    if len(closes) < settings.MA_PERIOD:
        return None
    return float(np.mean(closes[:settings.MA_PERIOD]))


def check_extra_buy_signal(current_price: int, position: "Position") -> bool:
    """3회 이상 매도 완료 후, 해당 시점 저점의 90% 이하 하락 시 추가 매수"""
    if position.sell_rounds_done < settings.EXTRA_BUY_MIN_SELL_ROUNDS:
        return False
    if position.extra_buy_low is None:
        return False
    return current_price <= position.extra_buy_low * settings.EXTRA_BUY_DROP_THRESHOLD
