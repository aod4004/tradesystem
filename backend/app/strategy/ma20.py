"""
이동평균선 캐시 갱신 (MA20 / MA60 / MA120)

매도 조건 중 "주가가 이동평균선과 만날 때" 를 실시간으로 평가하려면
보유 종목의 최신 MA 값이 WebSocket 체결 처리 시점에 가용해야 한다.

장 시작 전(08:30) 일봉 차트를 받아 settings.SELL_MA_PERIODS 별 MA를 계산하고
kiwoom_pool._ma_cache (글로벌, 모든 유저 WS 공용) 에 주입. 당일 매수된
신규 포지션은 _apply_fill 에서 compute_and_cache_ma 로 즉시 채운다.
"""
import asyncio
import numpy as np
from sqlalchemy import select

from app.config import settings
from app.core.kiwoom_client import KiwoomClient, get_kiwoom_client, to_int
from app.db.database import AsyncSessionLocal
from app.db.models import Position, PositionStatus


async def refresh_ma20_for_active_positions() -> int:
    """
    보유 중인 모든 종목의 MA를 계산해 WebSocket 캐시에 주입.
    반환: 갱신된 종목 수
    """
    # 순환 import 방지 — 런타임 import
    from app.ws.kiwoom_ws import kiwoom_pool

    async with AsyncSessionLocal() as db:
        positions = (
            await db.execute(
                select(Position).where(Position.status == PositionStatus.ACTIVE)
            )
        ).scalars().all()

    if not positions:
        return 0

    client = get_kiwoom_client()
    sem = asyncio.Semaphore(3)
    updated = 0

    async def work(code: str):
        nonlocal updated
        async with sem:
            try:
                mas = await _compute_mas(client, code)
                if mas:
                    kiwoom_pool.update_ma(code, mas)
                    updated += 1
                await asyncio.sleep(0.2)
            except Exception as e:
                print(f"[ma] {code} 계산 오류: {e}")

    await asyncio.gather(*(work(p.stock_code) for p in positions))
    print(f"[ma] {updated}/{len(positions)}개 종목 캐시 갱신")
    return updated


async def compute_and_cache_ma(
    stock_code: str,
    client: KiwoomClient | None = None,
) -> dict[int, float]:
    """단일 종목 MA 계산 후 kiwoom_pool 캐시에 주입. 신규 Position 이 생겼을 때 호출.

    client 를 명시하지 않으면 시스템 키 사용 (MA 는 시장 데이터).
    반환: 계산된 MA dict (비어있을 수 있음).
    """
    from app.ws.kiwoom_ws import kiwoom_pool

    c = client or get_kiwoom_client()
    try:
        mas = await _compute_mas(c, stock_code)
    except Exception as e:
        print(f"[ma] {stock_code} 계산 오류: {e}")
        return {}
    if mas:
        kiwoom_pool.update_ma(stock_code, mas)
    return mas


async def _compute_mas(client, stock_code: str) -> dict[int, float]:
    """주기별 MA를 한 번의 차트 조회로 동시 계산. 봉 수 부족하면 그 주기는 제외."""
    candles = await client.get_daily_chart(stock_code)
    closes = [to_int(c.get("cur_prc")) for c in candles]
    closes = [c for c in closes if c > 0]

    out: dict[int, float] = {}
    for period in settings.SELL_MA_PERIODS:
        if len(closes) >= period:
            out[period] = float(np.mean(closes[:period]))
    return out
