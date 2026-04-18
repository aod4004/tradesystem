"""
매수/매도 신호 감지 모듈

매수 신호: 각 차수별 조건 확인 후 BuySignal 생성
매도 신호: 실시간 가격 기반 조건 확인
"""
from datetime import datetime, date
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.core.kiwoom_client import get_kiwoom_client
from app.db.models import ScreenedStock, Position, BuySignal, PositionStatus


async def detect_buy_signals(db: AsyncSession) -> list[BuySignal]:
    """
    장 마감 후 실행 — 다음날 매수 예정 신호 생성
    양봉 여부: 전일 종가 > 전일 시가
    """
    client = get_kiwoom_client()
    signals: list[BuySignal] = []

    # 스크리닝된 활성 종목 조회
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
    """단일 종목 매수 신호 판단"""
    chart = await client.get_daily_chart(stock.code)
    candles = chart.get("output2", [])
    if len(candles) < 2:
        return None

    # 최신 봉이 인덱스 0
    today_candle = candles[0]
    prev_open = int(today_candle.get("stck_oprc", 0))
    prev_close = int(today_candle.get("stck_clpr", 0))
    is_bullish = prev_close > prev_open  # 양봉 여부

    if not is_bullish:
        return None

    # 현재 포지션 조회
    pos_stmt = select(Position).where(
        Position.stock_code == stock.code,
        Position.status == PositionStatus.ACTIVE,
    )
    position = (await db.execute(pos_stmt)).scalar_one_or_none()

    # 이미 실행된 신호 중복 방지
    today = datetime.utcnow().date()
    dup_stmt = select(BuySignal).where(
        BuySignal.stock_code == stock.code,
        BuySignal.signal_date >= datetime.combine(today, datetime.min.time()),
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
        # n차 매수 조건: (n-1)차 매수가의 90% 이하
        trigger_price = position.avg_buy_price * 0.90

    current_price = stock.current_price
    if current_price >= trigger_price:
        return None

    # 전날 종가를 매수 주문 가격으로 설정
    order_price = prev_close

    return BuySignal(
        stock_code=stock.code,
        signal_date=datetime.utcnow(),
        trigger_round=next_round,
        trigger_price=trigger_price,
        target_order_price=order_price,
        prev_close=prev_close,
        prev_open=prev_open,
        is_executed=False,
    )


def check_sell_signal(
    current_price: int,
    avg_buy_price: float,
    sell_rounds_done: int,
    ma20: float | None,
) -> int | None:
    """
    실시간 가격 기반 매도 신호 확인
    반환값: 실행할 매도 차수 (None이면 매도 신호 없음)
    """
    if sell_rounds_done >= 5:
        return None

    gain_rate = (current_price - avg_buy_price) / avg_buy_price

    # 1~4차: 수익률 기준 (5%, 10%, 15%, 20%)
    thresholds = settings.SELL_RATIOS  # [0.05, 0.10, 0.15, 0.20]
    for i, threshold in enumerate(thresholds):
        round_no = i + 1
        if sell_rounds_done < round_no and gain_rate >= threshold:
            return round_no

    # 5차: 20일 이동평균선 터치
    if sell_rounds_done < 5 and ma20 is not None:
        # 현재가가 MA20 이상으로 올라온 경우 매도
        if current_price >= ma20:
            return 5

    return None


def calculate_ma20(closes: list[int]) -> float | None:
    """20일 이동평균 계산 (closes: 최신순 정렬)"""
    if len(closes) < settings.MA_PERIOD:
        return None
    return float(np.mean(closes[:settings.MA_PERIOD]))


def check_extra_buy_signal(
    current_price: int,
    position: "Position",
) -> bool:
    """
    추가 매수 신호 확인
    조건: 3회 이상 매도 완료 + 그 시점 저점의 90% 이하로 하락
    """
    if position.sell_rounds_done < settings.EXTRA_BUY_MIN_SELL_ROUNDS:
        return False
    if position.extra_buy_low is None:
        return False
    return current_price <= position.extra_buy_low * settings.EXTRA_BUY_DROP_THRESHOLD
