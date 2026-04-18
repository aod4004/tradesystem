"""
종목 스크리닝 모듈

선정 기준:
1. 시가총액 1조원 이하
2. 순이익 흑자
3. 주당 가격 2000원 이상
4. 최근 1년 저점 대비 2배 이상 상승 후 고점의 50% 미만 하락
"""
import asyncio
from datetime import datetime
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.config import settings
from app.core.kiwoom_client import get_kiwoom_client
from app.db.models import ScreenedStock


async def run_screening(db: AsyncSession) -> list[ScreenedStock]:
    """전체 종목 스크리닝 실행 (장 마감 후 1회 실행)"""
    client = get_kiwoom_client()

    # 기존 스크리닝 종목 비활성화
    await db.execute(update(ScreenedStock).values(is_active=False))
    await db.commit()

    results: list[ScreenedStock] = []

    # KOSPI + KOSDAQ 전체 종목 조회
    stock_codes: list[tuple[str, str, str]] = []  # (code, name, market)
    for market_code, market_name in [("0", "KOSPI"), ("1", "KOSDAQ")]:
        try:
            data = await client.get_stock_list(market_code)
            for item in data.get("output", []):
                code = item.get("iscd", "")
                name = item.get("hts_kor_isnm", "")
                if code:
                    stock_codes.append((code, name, market_name))
        except Exception as e:
            print(f"[screener] 종목 목록 조회 오류 ({market_name}): {e}")

    # 배치 처리 (Rate limit 고려: 0.2초 간격)
    semaphore = asyncio.Semaphore(3)  # 동시 3개 요청

    async def check_stock(code: str, name: str, market: str):
        async with semaphore:
            try:
                stock = await _evaluate_stock(client, code, name, market)
                if stock:
                    results.append(stock)
                    db.add(stock)
                await asyncio.sleep(0.2)
            except Exception as e:
                print(f"[screener] {code} {name} 평가 오류: {e}")

    tasks = [check_stock(c, n, m) for c, n, m in stock_codes]
    await asyncio.gather(*tasks)

    await db.commit()
    print(f"[screener] 스크리닝 완료: {len(results)}개 종목 선정")
    return results


async def _evaluate_stock(client, code: str, name: str, market: str) -> ScreenedStock | None:
    """단일 종목 조건 평가"""
    # 1. 기본 정보 조회 (주가, 시가총액)
    info = await client.get_stock_info(code)
    output = info.get("output", {})

    current_price = int(output.get("stck_prpr", 0))
    market_cap = int(output.get("hts_avls", 0)) * 100_000_000  # 억원 → 원

    # 조건 1: 주가 2000원 이상
    if current_price < settings.MIN_STOCK_PRICE:
        return None

    # 조건 2: 시가총액 1조 이하
    if market_cap > settings.MAX_MARKET_CAP:
        return None

    # 3. 재무 정보 조회 (순이익 흑자 확인)
    fin = await client.get_financial_info(code)
    fin_output = fin.get("output", [{}])
    net_income = float(fin_output[0].get("net_inco", 0)) if fin_output else 0
    operating_income = float(fin_output[0].get("bsop_prti", 0)) if fin_output else 0

    # 조건 3: 순이익 흑자
    if net_income <= 0:
        return None

    # 4. 1년 일봉 데이터로 고점/저점 계산
    chart = await client.get_daily_chart(code)
    candles = chart.get("output2", [])
    if len(candles) < 20:
        return None

    closes = [int(c.get("stck_clpr", 0)) for c in candles if c.get("stck_clpr")]
    if not closes:
        return None

    high_1y = max(closes)
    low_1y = min(closes)

    if low_1y <= 0:
        return None

    rise_from_low = high_1y / low_1y          # 저점 대비 상승 배수
    drop_from_high = current_price / high_1y  # 고점 대비 현재가 비율

    # 조건 4: 저점 대비 2배 이상 상승 후 고점의 50% 미만으로 하락
    if rise_from_low < settings.LOW_RISE_THRESHOLD:
        return None
    if drop_from_high >= settings.HIGH_DROP_THRESHOLD:
        return None

    # 외국인 비율 조회
    try:
        fgn = await client.get_foreign_ratio(code)
        foreign_ratio = float(fgn.get("output", {}).get("frgn_hldn_qty_rt", 0))
    except Exception:
        foreign_ratio = 0.0

    return ScreenedStock(
        code=code,
        name=name,
        market=market,
        current_price=current_price,
        high_1y=high_1y,
        low_1y=low_1y,
        market_cap=market_cap,
        net_income=net_income,
        operating_income=operating_income,
        foreign_ratio=foreign_ratio,
        drop_from_high=round((1 - drop_from_high) * 100, 2),   # 하락률 %
        rise_from_low=round(rise_from_low, 2),
        screened_at=datetime.utcnow(),
        is_active=True,
    )
