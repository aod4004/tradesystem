"""
매수/매도 신호 감지 모듈

매수 신호 (장 마감 후): 각 차수별 조건 확인 후 BuySignal 생성
  - 스크리닝 통과 종목 (ScreenedStock is_active=True) + 유저 관심종목 (UserWatchlist)
  - 매수 조건은 양쪽 동일: 양봉 + (1차는 고점 50%↓, 2차+는 전 차수 90%↓)

매도 신호 (실시간): 실시간 체결가 기반 조건 평가
"""
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.core.kiwoom_client import KiwoomClient, to_int
from app.db.models import (
    ScreenedStock, Position, BuySignal, PositionStatus, UserWatchlist,
)


@dataclass
class BuyCandidate:
    """매수 신호 평가용 공통 후보 — 스크리닝/관심종목을 하나로 추상화"""
    code: str
    name: str
    current_price: int
    high_1y: int
    source: str   # 'screening' | 'watchlist'


async def detect_buy_signals(
    db: AsyncSession, user_id: int, client: KiwoomClient,
) -> list[BuySignal]:
    """해당 유저의 다음날 매수 예정 신호 생성 — 스크리닝 + 관심종목 순회.

    client 는 유저의 키움 클라이언트. 일봉/종목정보 조회에 사용.
    """
    signals: list[BuySignal] = []

    # 1) 스크리닝 통과 종목
    screened = (await db.execute(
        select(ScreenedStock).where(ScreenedStock.is_active == True)  # noqa: E712
    )).scalars().all()
    screened_codes = {s.code for s in screened}

    candidates: list[BuyCandidate] = [
        BuyCandidate(
            code=s.code, name=s.name,
            current_price=s.current_price, high_1y=s.high_1y,
            source="screening",
        )
        for s in screened
    ]

    # 2) 유저의 관심종목 — 스크리닝에 이미 포함된 건 중복 추가하지 않음
    watchlist = (await db.execute(
        select(UserWatchlist).where(UserWatchlist.user_id == user_id)
    )).scalars().all()
    for w in watchlist:
        if w.stock_code in screened_codes:
            continue
        try:
            info = await client.get_stock_info(w.stock_code)
        except Exception as e:
            print(f"[signal] {w.stock_code} 관심종목 정보 조회 실패 (user={user_id}): {e}")
            continue
        current_price = abs(to_int(info.get("cur_prc")))
        high_1y = abs(to_int(info.get("250hgst")))
        if current_price <= 0 or high_1y <= 0:
            # 250일 고점 없으면 일봉 차트로 폴백
            try:
                chart = await client.get_daily_chart(w.stock_code)
                closes = [to_int(c.get("cur_prc")) for c in chart if to_int(c.get("cur_prc")) > 0]
                if closes:
                    high_1y = max(closes)
                    current_price = current_price or closes[0]
            except Exception as e:
                print(f"[signal] {w.stock_code} 일봉 폴백 실패: {e}")
                continue
        if current_price <= 0 or high_1y <= 0:
            continue
        candidates.append(BuyCandidate(
            code=w.stock_code, name=w.stock_name,
            current_price=current_price, high_1y=high_1y,
            source="watchlist",
        ))

    # 3) 각 후보별 조건 평가
    for cand in candidates:
        try:
            sig = await _check_buy_signal(client, db, cand, user_id)
            if sig:
                db.add(sig)
                signals.append(sig)
        except Exception as e:
            print(f"[signal] {cand.code} (user={user_id}) 신호 확인 오류: {e}")

    await db.commit()
    return signals


async def _check_buy_signal(
    client, db: AsyncSession, cand: BuyCandidate, user_id: int
) -> BuySignal | None:
    candles = await client.get_daily_chart(cand.code)
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

    # 유저의 현재 포지션
    pos_stmt = select(Position).where(
        Position.user_id == user_id,
        Position.stock_code == cand.code,
        Position.status == PositionStatus.ACTIVE,
    )
    position = (await db.execute(pos_stmt)).scalar_one_or_none()

    # 오늘 이미 미실행 신호 있으면 중복 방지 (같은 유저 기준)
    today_midnight = datetime.combine(datetime.utcnow().date(), datetime.min.time())
    dup_stmt = select(BuySignal).where(
        BuySignal.user_id == user_id,
        BuySignal.stock_code == cand.code,
        BuySignal.signal_date >= today_midnight,
        BuySignal.is_executed == False,  # noqa: E712
    )
    if (await db.execute(dup_stmt)).scalar_one_or_none():
        return None

    next_round = 1
    trigger_price = cand.high_1y * settings.HIGH_DROP_THRESHOLD

    if position:
        if position.buy_rounds_done >= settings.MAX_BUY_ROUNDS:
            return None
        next_round = position.buy_rounds_done + 1
        trigger_price = position.avg_buy_price * 0.90   # 전 차수 매수가의 90%

    if cand.current_price >= trigger_price:
        return None

    return BuySignal(
        user_id=user_id,
        stock_code=cand.code,
        stock_name=cand.name,
        source=cand.source,
        signal_date=datetime.utcnow(),
        trigger_round=next_round,
        trigger_price=trigger_price,
        target_order_price=today_close,
        prev_close=today_close,
        prev_open=today_open,
        is_executed=False,
        is_excluded=False,
    )


# ---------------------------------------------------------------------- #
#  매도 조건 비트 레이아웃 (Position.sold_triggers 와 공유)
#
#    비트 0~3 : 수익률 조건 (SELL_RATIOS 순서대로)
#    비트 4+i : MA 조건     (SELL_MA_PERIODS 순서대로)
#
#  각 조건은 포지션 수명 동안 1회만 발동한다. 조건이 발동되면 해당 비트가 set되고,
#  같은 가격대로 재진입해도 재발동되지 않는다.
# ---------------------------------------------------------------------- #
def _ma_bit(i: int) -> int:
    return len(settings.SELL_RATIOS) + i


def check_sell_signal(
    current_price: int,
    avg_buy_price: float,
    sell_rounds_done: int,
    sold_triggers: int,
    ma_values: dict[int, float | None],
) -> tuple[int, int] | None:
    """
    실시간 체결가 기반 매도 판정.
    반환: (tranche_no, trigger_bit)  — tranche 는 1~5, trigger_bit 는 조건 식별자.
          발동 조건이 없으면 None.

    - 수익률 조건을 먼저 평가하고, 그 다음 MA 조건을 낮은 주기부터 평가
    - 이미 발동된(sold_triggers set) 조건은 건너뜀
    - 물량 한도(5 tranche)에 걸리면 None 반환
    """
    if sell_rounds_done >= settings.MAX_SELL_TRANCHES:
        return None

    gain_rate = (
        (current_price - avg_buy_price) / avg_buy_price if avg_buy_price > 0 else 0.0
    )

    for i, threshold in enumerate(settings.SELL_RATIOS):
        bit = i
        if sold_triggers & (1 << bit):
            continue
        if gain_rate >= threshold:
            return (sell_rounds_done + 1, bit)

    for i, period in enumerate(settings.SELL_MA_PERIODS):
        bit = _ma_bit(i)
        if sold_triggers & (1 << bit):
            continue
        ma = ma_values.get(period)
        if ma is not None and current_price >= ma:
            return (sell_rounds_done + 1, bit)

    return None


def calculate_ma(closes: list[int], period: int) -> float | None:
    """closes = 최신순 정렬된 종가 리스트. 주기별 MA 계산."""
    if len(closes) < period:
        return None
    return float(np.mean(closes[:period]))


def check_extra_buy_signal(current_price: int, position: "Position") -> bool:
    """3회 이상 매도 완료 후, 해당 시점 저점의 90% 이하 하락 시 추가 매수"""
    if position.sell_rounds_done < settings.EXTRA_BUY_MIN_SELL_ROUNDS:
        return False
    if position.extra_buy_low is None:
        return False
    return current_price <= position.extra_buy_low * settings.EXTRA_BUY_DROP_THRESHOLD
