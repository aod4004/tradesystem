from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.core.kiwoom_client import get_or_create_user_client, to_int, to_float
from app.db.database import get_db
from app.db.models import Position, PositionStatus, User, UserTradingConfig
from app.db.user_config import get_total_investment
from app.ws.kiwoom_ws import kiwoom_pool

router = APIRouter(prefix="/api/account", tags=["account"])


@router.get("/balance")
async def get_balance(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """계좌평가잔고내역 (kt00018) + 예수금(kt00001) 통합.

    유저 본인의 키움 키로 조회. 키 미등록 시 409 반환 (프론트는 이를 보고 설정 UI 안내).
    """
    cfg = (
        await db.execute(
            select(UserTradingConfig).where(UserTradingConfig.user_id == user.id)
        )
    ).scalar_one_or_none()
    if cfg is None or not cfg.kiwoom_app_key or not cfg.kiwoom_secret_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="keys_not_configured",
        )

    client = get_or_create_user_client(user.id, cfg)
    try:
        bal = await client.get_balance()
        dep = await client.get_deposit()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"키움 API 오류: {e}",
        )

    holdings = [
        {
            "code": _strip_code_prefix(h.get("stk_cd", "")),
            "name": h.get("stk_nm", ""),
            "quantity": to_int(h.get("rmnd_qty")),
            "avg_price": to_int(h.get("pur_pric")),
            "current_price": to_int(h.get("cur_prc")),
            "eval_profit_loss": to_int(h.get("evltv_prft")),
            "profit_rate": to_float(h.get("prft_rt")),
        }
        for h in (bal.get("acnt_evlt_remn_indv_tot", []) or [])
    ]

    total_eval = to_int(bal.get("tot_evlt_amt"))
    deposit = to_int(dep.get("entr"))
    order_available = to_int(dep.get("ord_alow_amt"))
    total_asset = to_int(bal.get("prsm_dpst_aset_amt")) or (total_eval + deposit)
    total_invest = await get_total_investment(db, user.id)
    profit_rate = (
        (total_asset - total_invest) / total_invest * 100
        if total_invest else 0
    )

    return {
        "total_investment": total_invest,
        "total_asset": total_asset,
        "total_eval_amount": total_eval,
        "total_purchase_amount": to_int(bal.get("tot_pur_amt")),
        "total_profit_loss": to_int(bal.get("tot_evlt_pl")),
        "total_profit_rate": to_float(bal.get("tot_prft_rt")),
        "deposit": deposit,
        "order_available": order_available,
        "profit_rate": round(profit_rate, 2),
        "holdings": holdings,
        "mock": cfg.kiwoom_mock,
    }


def _strip_code_prefix(code: str) -> str:
    """키움 잔고 응답의 종목코드는 'A005930' 형식 — A 접두사 제거"""
    c = (code or "").strip()
    return c[1:] if c.startswith("A") else c


# ---------------------------------------------------------------------- #
#  잔고 ↔ DB positions 리콘실 (Phase 4.3 의 포지션 부분 선행)
# ---------------------------------------------------------------------- #
#
# WS LOGIN 버그 등으로 체결 이벤트를 놓쳐 DB positions 가 비어있는 경우,
# 키움 실제 잔고를 기준으로 맞춘다. 매수/매도 주문 체결이 정상 수신되기
# 시작한 이후엔 필요 없지만, 과거 누락 복구용으로 상시 제공.

@router.post("/reconcile-positions")
async def reconcile_positions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """키움 잔고(kt00018) 기준으로 DB positions 를 동기화.

    동작:
     - 잔고에 있고 DB 에 없음 → Position 신규 생성 (buy_rounds_done=1 기본 —
       최소 1차 매수가 있었다고 가정. 유저가 수동 조정 가능한 항목).
     - 잔고에도 있고 DB 에도 있음(ACTIVE) → quantity/avg_buy_price/total_buy_amount
       을 잔고 값으로 덮어쓰기. buy_rounds_done/sell_rounds_done/sold_triggers 는
       유지(전략 상태).
     - DB 에는 ACTIVE 인데 잔고엔 없음 → CLOSED 처리 + closed_at 기록.
     - 새로 생긴 Position 은 WS 실시간 시세 구독 추가.

    반환: {"created": N, "updated": N, "closed": N, "total_holdings": N}
    """
    cfg = (
        await db.execute(
            select(UserTradingConfig).where(UserTradingConfig.user_id == user.id)
        )
    ).scalar_one_or_none()
    if cfg is None or not cfg.kiwoom_app_key or not cfg.kiwoom_secret_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="keys_not_configured",
        )

    client = get_or_create_user_client(user.id, cfg)
    try:
        bal = await client.get_balance()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"키움 잔고 조회 실패: {e}",
        )

    holdings_raw = bal.get("acnt_evlt_remn_indv_tot", []) or []
    holdings: dict[str, dict] = {}
    for h in holdings_raw:
        code = _strip_code_prefix(h.get("stk_cd", ""))
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
            Position.user_id == user.id,
            Position.status == PositionStatus.ACTIVE,
        )
    )).scalars().all()
    existing_by_code = {p.stock_code: p for p in existing}

    created = 0
    updated = 0
    closed = 0
    new_codes_to_subscribe: list[str] = []

    # upsert
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
                user_id=user.id,
                stock_code=code,
                stock_name=info["name"] or code,
                buy_rounds_done=1,   # 잔고에 있으니 최소 1차 매수. 유저가 조정 가능.
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
            new_codes_to_subscribe.append(code)

    # 잔고에 없어진 ACTIVE 포지션은 CLOSED
    for code, pos in existing_by_code.items():
        if code not in holdings:
            pos.status = PositionStatus.CLOSED
            pos.closed_at = datetime.utcnow()
            closed += 1

    await db.commit()

    # 새 포지션에 실시간 가격 구독 추가 (best-effort — WS 미연결이어도 실패 안 함)
    for code in new_codes_to_subscribe:
        try:
            await kiwoom_pool.subscribe_price(user.id, code)
        except Exception as e:
            print(f"[reconcile] user={user.id} {code} 시세 구독 실패: {e}")

    return {
        "created": created,
        "updated": updated,
        "closed": closed,
        "total_holdings": len(holdings),
    }
