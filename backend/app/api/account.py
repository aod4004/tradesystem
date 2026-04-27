from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.core.kiwoom_client import get_or_create_user_client, to_int, to_float
from app.db.database import get_db
from app.db.models import User, UserTradingConfig
from app.db.user_config import get_total_investment
from app.strategy.balance_sync import sync_user_balance, strip_code_prefix

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

    # 키움 가격 필드는 전일대비 방향을 +/- 부호로 포함 — abs() 적용 (ka10001 와 동일 패턴).
    holdings = [
        {
            "code": strip_code_prefix(h.get("stk_cd", "")),
            "name": h.get("stk_nm", ""),
            "quantity": to_int(h.get("rmnd_qty")),
            "avg_price": to_int(h.get("pur_pric")),
            "current_price": abs(to_int(h.get("cur_prc"))),
            "eval_profit_loss": to_int(h.get("evltv_prft")),
            "profit_rate": to_float(h.get("prft_rt")),
        }
        for h in (bal.get("acnt_evlt_remn_indv_tot", []) or [])
    ]

    total_eval = to_int(bal.get("tot_evlt_amt"))
    deposit = to_int(dep.get("entr"))
    order_available = to_int(dep.get("ord_alow_amt"))
    # 총 자산 = 보유 주식 평가금액 + 예수금 (사용자 멘탈 모델 일관성).
    # 키움 prsm_dpst_aset_amt 는 대용금/신용/발행어음 등도 포함해 사용자가 본 두 값의 단순
    # 합과 어긋날 수 있어 명시적으로 단순 합 사용.
    total_asset = total_eval + deposit
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


# ---------------------------------------------------------------------- #
#  잔고 ↔ DB positions 리콘실
# ---------------------------------------------------------------------- #
#
# 매일 08:00 잡(job_balance_sync)이 자동 동기화하지만, 외부 거래 직후 즉시
# 동기화하고 싶을 때 사용하는 수동 트리거 API. 실제 로직은 sync_user_balance
# 에 있고 잡/API 가 공유한다.

@router.post("/reconcile-positions")
async def reconcile_positions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """키움 잔고(kt00018) 기준으로 DB positions 를 즉시 동기화.

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
        return await sync_user_balance(db, user.id, client)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"키움 잔고 조회 실패: {e}",
        )
