"""
키움 잔고 ↔ DB Position 동기화.

매일 08:00 잡(job_balance_sync)에서 호출. 사전 매도 주문 정비(08:35) 직전에
DB Position 의 quantity / avg_buy_price 를 키움 실잔고로 맞춰, 외부 거래로
인한 어긋남(예: 영웅문에서 매도, 또는 모의투자 잔고 자체가 시스템과 다른 케이스)
때문에 발생하는 800033(매도가능수량 부족) 폭격을 차단한다.

기존 `account.py:reconcile_positions` API 와 동일 로직을 함수로 추출 — 잡과 API
양쪽에서 재사용한다.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.kiwoom_client import KiwoomClient, to_int
from app.db.models import Position, PositionStatus


def strip_code_prefix(code: str) -> str:
    """키움 잔고 응답의 종목코드는 'A005930' 형식 — 'A' 접두사 제거."""
    c = (code or "").strip()
    return c[1:] if c.startswith("A") else c


async def sync_user_balance(
    db: AsyncSession,
    user_id: int,
    client: KiwoomClient,
) -> dict:
    """키움 잔고를 truth 로 DB Position 동기화.

    동작:
      - 잔고에 있고 DB 에 없음 → Position 신규 생성 (buy_rounds_done=1 기본)
      - 잔고/DB 모두 있음 → quantity / avg_buy_price / total_buy_amount 잔고로 덮어쓰기
                            (buy_rounds_done / sell_rounds_done / sold_triggers 는 보존 — 전략 상태)
      - DB 에는 ACTIVE 인데 잔고엔 없음 → CLOSED 처리
      - 새로 생긴 Position 은 WS 실시간 시세 구독 + MA 캐시 주입

    반환: {"created": N, "updated": N, "closed": N, "total_holdings": N}
    """
    # 순환 import 방지 — 런타임 import
    from app.ws.kiwoom_ws import kiwoom_pool
    from app.strategy.ma20 import compute_and_cache_ma

    bal = await client.get_balance()

    holdings: dict[str, dict] = {}
    for h in bal.get("acnt_evlt_remn_indv_tot", []) or []:
        code = strip_code_prefix(h.get("stk_cd", ""))
        qty = to_int(h.get("rmnd_qty"))
        if not code or qty <= 0:
            continue
        holdings[code] = {
            "name": (h.get("stk_nm") or "").strip(),
            "quantity": qty,
            "avg_price": to_int(h.get("pur_pric")),
        }

    existing = (await db.execute(
        select(Position).where(
            Position.user_id == user_id,
            Position.status == PositionStatus.ACTIVE,
        )
    )).scalars().all()
    existing_by_code = {p.stock_code: p for p in existing}

    created = 0
    updated = 0
    closed = 0
    new_codes: list[str] = []

    for code, info in holdings.items():
        if code in existing_by_code:
            pos = existing_by_code[code]
            pos.quantity = info["quantity"]
            pos.avg_buy_price = float(info["avg_price"])
            pos.total_buy_amount = float(info["avg_price"]) * info["quantity"]
            if info["name"] and not pos.stock_name:
                pos.stock_name = info["name"]
            updated += 1
        else:
            pos = Position(
                user_id=user_id,
                stock_code=code,
                stock_name=info["name"] or code,
                buy_rounds_done=1,
                sell_rounds_done=0,
                sold_triggers=0,
                quantity=info["quantity"],
                avg_buy_price=float(info["avg_price"]),
                total_buy_amount=float(info["avg_price"]) * info["quantity"],
                extra_buy_rounds=0,
                status=PositionStatus.ACTIVE,
            )
            db.add(pos)
            created += 1
            new_codes.append(code)

    for code, pos in existing_by_code.items():
        if code not in holdings:
            pos.status = PositionStatus.CLOSED
            pos.closed_at = datetime.utcnow()
            closed += 1

    await db.commit()

    # 새 포지션 — 실시간 시세 구독 + MA 캐시 (best-effort, 실패해도 계속 진행)
    for code in new_codes:
        try:
            await kiwoom_pool.subscribe_price(user_id, code)
        except Exception as e:
            print(f"[balance_sync] user={user_id} {code} 시세 구독 실패: {e}")
        try:
            await compute_and_cache_ma(code, client)
        except Exception as e:
            print(f"[balance_sync] user={user_id} {code} MA 계산 실패: {e}")

    return {
        "created": created,
        "updated": updated,
        "closed": closed,
        "total_holdings": len(holdings),
    }
