"""
종목 스크리닝 모듈

선정 기준:
1. 시가총액 1조원 이하
2. 순이익 흑자
3. 주당 가격 2000원 이상
4. 최근 1년 저점 대비 2배 이상 상승 후 고점의 50% 미만 하락

키움 ka10001 한 번 호출로 현재가·시총·재무·외인비율을 모두 얻고,
ka10081 로 1년 일봉을 받아 고/저점을 계산한다.

수동 재실행 시 결과가 완전히 재현되도록:
 - 일시적 API 오류는 최대 3회까지 재시도 (지수 백오프)
 - 탈락 사유를 집계해서 최종 요약 로그 출력
"""
import asyncio
import time
from collections import Counter
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete

from app.config import settings
from app.core.kiwoom_client import get_kiwoom_client, to_int, to_float
from app.db.models import ScreenedStock


# 안전 장치: 스크리닝 최대 소요 시간(초). 초과 시 더 이상 새 종목을 평가하지 않음.
SCREENING_MAX_SECONDS = 20 * 60    # 20분

# 동시 요청 수 — 키움 rate limit 에 여유를 둔 값.
SCREENING_CONCURRENCY = 10


async def run_screening(
    db: AsyncSession,
    progress: dict | None = None,
) -> list[ScreenedStock]:
    """전체 종목 스크리닝 실행 (장 마감 후 1회).

    progress 가 주어지면 총/진행/선정 개수를 실시간으로 갱신한다.
    키: total, processed, selected.
    """
    client = get_kiwoom_client()

    # 기존 스크리닝 종목 전량 삭제 후 새로 채움.
    # positions/buy_signals 의 screened_stocks FK 는 0005 에서 드롭됐으므로 안전.
    # is_active=False 로만 두면 같은 code 재스크리닝 시 unique 제약 충돌 (ix_screened_stocks_code).
    await db.execute(delete(ScreenedStock))
    await db.commit()

    # 코스피(0) + 코스닥(10) 전체 종목
    stock_rows: list[tuple[str, str, str]] = []
    pre_filter = Counter()
    for mrkt_tp, market_name in [("0", "KOSPI"), ("10", "KOSDAQ")]:
        try:
            items = await client.get_stock_list(mrkt_tp)
            for it in items:
                code = (it.get("code") or "").strip()
                name = (it.get("name") or "").strip()
                if not code:
                    pre_filter["no_code"] += 1
                    continue
                if _is_skip_state(it):
                    pre_filter["skip_state"] += 1
                    continue
                last_price = to_int(it.get("lastPrice"))
                if 0 < last_price < settings.MIN_STOCK_PRICE:
                    pre_filter["below_min_price"] += 1
                    continue
                stock_rows.append((code, name, market_name))
        except Exception as e:
            print(f"[screener] 종목 목록 조회 오류 ({market_name}): {e}")

    print(
        f"[screener] 평가 대상 {len(stock_rows)}개 / "
        f"사전 제외: {dict(pre_filter)}"
    )

    results: list[ScreenedStock] = []
    eval_stats = Counter()
    semaphore = asyncio.Semaphore(SCREENING_CONCURRENCY)
    started_at = time.monotonic()
    total = len(stock_rows)
    processed = 0

    if progress is not None:
        progress["total"] = total
        progress["processed"] = 0
        progress["selected"] = 0

    async def check_stock(code: str, name: str, market: str):
        nonlocal processed
        if time.monotonic() - started_at > SCREENING_MAX_SECONDS:
            eval_stats["timeout_skipped"] += 1
            return
        async with semaphore:
            reason = await _evaluate_with_retry(client, code, name, market, results, db)
            eval_stats[reason] += 1
            processed += 1
            if progress is not None:
                progress["processed"] = processed
                progress["selected"] = len(results)
            if processed % 200 == 0:
                elapsed = time.monotonic() - started_at
                print(
                    f"[screener] 진행 {processed}/{total} "
                    f"(선정 {len(results)}, {elapsed:.0f}초 경과)"
                )

    await asyncio.gather(*(check_stock(*r) for r in stock_rows))
    await db.commit()

    elapsed = time.monotonic() - started_at
    print(
        f"[screener] 완료 — 선정 {len(results)}개 / {elapsed:.0f}초 / 평가 결과: {dict(eval_stats)}"
    )
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


async def _evaluate_with_retry(
    client, code: str, name: str, market: str,
    results: list[ScreenedStock], db: AsyncSession,
    max_attempts: int = 3,
) -> str:
    """전송 오류는 재시도, 탈락 사유는 즉시 반환"""
    last_err: Exception | None = None
    for attempt in range(max_attempts):
        try:
            stock, reason = await _evaluate_stock(client, code, name, market)
            if stock:
                results.append(stock)
                db.add(stock)
            return reason
        except Exception as e:
            last_err = e
            if attempt < max_attempts - 1:
                await asyncio.sleep(0.5 * (attempt + 1))
    print(f"[screener] {code} {name} 평가 오류(3회 재시도 실패): {last_err}")
    return "error"


async def _evaluate_stock(client, code: str, name: str, market: str):
    """
    반환: (ScreenedStock | None, reason)
    reason: accepted / low_price / cap_unknown / cap_too_large /
            no_profit / chart_insufficient / low_rise / high_drop_small
    """
    info = await client.get_stock_info(code)

    current_price = to_int(info.get("cur_prc"))
    if current_price < settings.MIN_STOCK_PRICE:
        return None, "low_price"

    listed_shares = to_int(info.get("flo_stk"))
    market_cap = listed_shares * current_price if listed_shares > 0 else 0
    if market_cap == 0:
        return None, "cap_unknown"
    if market_cap > settings.MAX_MARKET_CAP:
        return None, "cap_too_large"

    net_income = to_float(info.get("cup_nga"))
    operating_income = to_float(info.get("bus_pro"))
    foreign_ratio = to_float(info.get("for_exh_rt"))

    if net_income <= 0:
        return None, "no_profit"

    # 1년 고/저 — ka10001의 250일 고저점으로 해결(ka10081 호출 절약)
    high_1y = abs(to_int(info.get("250hgst")))
    low_1y = abs(to_int(info.get("250lwst")))

    # 값이 비어있으면 ka10081로 폴백
    if high_1y <= 0 or low_1y <= 0:
        candles = await client.get_daily_chart(code)
        closes = [to_int(c.get("cur_prc")) for c in candles]
        closes = [p for p in closes if p > 0]
        if len(closes) < 20:
            return None, "chart_insufficient"
        high_1y = max(closes)
        low_1y = min(closes)

    if high_1y <= 0 or low_1y <= 0:
        return None, "chart_insufficient"

    rise_from_low = high_1y / low_1y
    drop_ratio = current_price / high_1y

    if rise_from_low < settings.LOW_RISE_THRESHOLD:
        return None, "low_rise"
    if drop_ratio >= settings.HIGH_DROP_THRESHOLD:
        return None, "high_drop_small"

    stock = ScreenedStock(
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
    return stock, "accepted"
