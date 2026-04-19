"""
종목 스크리닝 모듈

선정 기준:
1. 시가총액 1조원 이하
2. 순이익 흑자
3. 주당 가격 2000원 이상
4. 최근 1년 저점 대비 2배 이상 상승 후 고점의 50% 미만 하락

키움 ka10001 한 번 호출로 현재가·시총·재무·외인비율을 모두 얻고,
ka10081 로 1년 일봉을 받아 고/저점을 계산한다.
"""
import asyncio
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update

from app.config import settings
from app.core.kiwoom_client import get_kiwoom_client, to_int, to_float
from app.db.models import ScreenedStock


async def run_screening(db: AsyncSession) -> list[ScreenedStock]:
    """전체 종목 스크리닝 실행 (장 마감 후 1회)"""
    client = get_kiwoom_client()

    # 기존 스크리닝 종목 비활성화
    await db.execute(update(ScreenedStock).values(is_active=False))
    await db.commit()

    # 코스피(0) + 코스닥(10) 전체 종목
    stock_rows: list[tuple[str, str, str]] = []   # (code, name, market)
    skipped = 0
    for mrkt_tp, market_name in [("0", "KOSPI"), ("10", "KOSDAQ")]:
        try:
            items = await client.get_stock_list(mrkt_tp)
            for it in items:
                code = (it.get("code") or "").strip()
                name = (it.get("name") or "").strip()
                if not code:
                    continue
                if _is_skip_state(it):
                    skipped += 1
                    continue
                # 사전 필터: 전일종가(lastPrice)가 최소 주가 미만이면 상세조회 스킵
                last_price = to_int(it.get("lastPrice"))
                if 0 < last_price < settings.MIN_STOCK_PRICE:
                    skipped += 1
                    continue
                stock_rows.append((code, name, market_name))
        except Exception as e:
            print(f"[screener] 종목 목록 조회 오류 ({market_name}): {e}")

    print(f"[screener] 평가 대상: {len(stock_rows)}개 (사전 필터로 {skipped}개 제외)")

    results: list[ScreenedStock] = []
    semaphore = asyncio.Semaphore(3)

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

    await asyncio.gather(*(check_stock(*r) for r in stock_rows))
    await db.commit()
    print(f"[screener] 완료 — 선정 {len(results)}개")
    return results


def _is_skip_state(item: dict) -> bool:
    """투자유의·관리·정리매매 등 스크리닝 배제"""
    state = item.get("state") or ""
    warn = item.get("orderWarning") or "0"
    if "관리종목" in state or "정리매매" in state:
        return True
    if warn in {"2", "3", "4", "5"}:
        return True
    return False


async def _evaluate_stock(client, code: str, name: str, market: str) -> ScreenedStock | None:
    # 1) 기본정보 — 현재가, 상장주식수, 재무, 외인
    info = await client.get_stock_info(code)

    current_price = to_int(info.get("cur_prc"))
    # 조건 1: 주가 2000원 이상
    if current_price < settings.MIN_STOCK_PRICE:
        return None

    # 시가총액 = 상장주식수 × 현재가 (단위 독립, mac 필드 의존 제거)
    listed_shares = to_int(info.get("flo_stk"))
    market_cap = listed_shares * current_price if listed_shares > 0 else 0
    # 조건 2: 시가총액 1조 이하 (0원은 정보 부족으로 제외)
    if market_cap == 0 or market_cap > settings.MAX_MARKET_CAP:
        return None

    net_income = to_float(info.get("cup_nga"))       # 당기순이익
    operating_income = to_float(info.get("bus_pro"))  # 영업이익
    foreign_ratio = to_float(info.get("for_exh_rt"))  # 외인소진률

    # 조건 3: 순이익 흑자
    if net_income <= 0:
        return None

    # 2) 1년 일봉 — 고점/저점 산출
    candles = await client.get_daily_chart(code)
    closes: list[int] = [to_int(c.get("cur_prc")) for c in candles]
    closes = [p for p in closes if p > 0]
    if len(closes) < 20:
        return None

    high_1y = max(closes)
    low_1y = min(closes)
    if low_1y <= 0:
        return None

    rise_from_low = high_1y / low_1y              # 저점 대비 상승 배수
    drop_ratio = current_price / high_1y          # 고점 대비 현재가 비율

    # 조건 4
    if rise_from_low < settings.LOW_RISE_THRESHOLD:
        return None
    if drop_ratio >= settings.HIGH_DROP_THRESHOLD:
        return None

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
        drop_from_high=round((1 - drop_ratio) * 100, 2),
        rise_from_low=round(rise_from_low, 2),
        screened_at=datetime.utcnow(),
        is_active=True,
    )
