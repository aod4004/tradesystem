"""
MA20 (20일 이동평균) 캐시 갱신

5차 매도 조건(주가가 20일 이동평균선과 만날 때)을 평가하려면
보유 종목의 최신 MA20 값이 실시간 체결 처리 시점에 가용해야 한다.

장 마감 후 일봉 차트를 받아 MA20을 계산하고 kiwoom_ws._ma20_cache 에 주입.
"""
import asyncio
import numpy as np
from sqlalchemy import select

from app.config import settings
from app.core.kiwoom_client import get_kiwoom_client, to_int
from app.db.database import AsyncSessionLocal
from app.db.models import Position, PositionStatus


async def refresh_ma20_for_active_positions() -> int:
    """
    보유 중인 모든 종목의 MA20을 계산해 WebSocket 캐시에 주입.
    반환: 갱신된 종목 수
    """
    # 순환 import 방지 — 런타임 import
    from app.ws.kiwoom_ws import kiwoom_ws

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
                ma = await _compute_ma20(client, code)
                if ma is not None:
                    kiwoom_ws.update_ma20(code, ma)
                    updated += 1
                await asyncio.sleep(0.2)
            except Exception as e:
                print(f"[ma20] {code} 계산 오류: {e}")

    await asyncio.gather(*(work(p.stock_code) for p in positions))
    print(f"[ma20] {updated}/{len(positions)}개 종목 캐시 갱신")
    return updated


async def _compute_ma20(client, stock_code: str) -> float | None:
    candles = await client.get_daily_chart(stock_code)
    closes = [to_int(c.get("cur_prc")) for c in candles[: settings.MA_PERIOD]]
    closes = [c for c in closes if c > 0]
    if len(closes) < settings.MA_PERIOD:
        return None
    return float(np.mean(closes))
