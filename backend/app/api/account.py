from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.core.kiwoom_client import get_kiwoom_client, to_int, to_float
from app.db.database import get_db
from app.db.models import User
from app.db.user_config import get_total_investment

router = APIRouter(prefix="/api/account", tags=["account"])


@router.get("/balance")
async def get_balance(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """계좌평가잔고내역 (kt00018) + 예수금(kt00001) 통합.

    주의: 현재는 단일 키움 계정을 공유하므로 계정 숫자는 모든 유저가 동일. 표시되는
    '총 투자금' 만 유저별 설정을 반영. Phase 2.5 에서 계정도 유저별 분리 예정.
    """
    client = get_kiwoom_client()
    bal = await client.get_balance()
    dep = await client.get_deposit()

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

    total_eval = to_int(bal.get("tot_evlt_amt"))          # 보유 종목 평가금액
    deposit = to_int(dep.get("entr"))                      # 예수금
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
    }


def _strip_code_prefix(code: str) -> str:
    """키움 잔고 응답의 종목코드는 'A005930' 형식 — A 접두사 제거"""
    c = (code or "").strip()
    return c[1:] if c.startswith("A") else c
