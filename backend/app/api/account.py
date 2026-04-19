from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.core.kiwoom_client import get_or_create_user_client, to_int, to_float
from app.db.database import get_db
from app.db.models import User, UserTradingConfig
from app.db.user_config import get_total_investment

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
