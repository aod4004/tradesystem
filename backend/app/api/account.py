import os
from fastapi import APIRouter
from app.core.kiwoom_client import get_kiwoom_client
from app.config import settings

router = APIRouter(prefix="/api/account", tags=["account"])
ACCOUNT_NO = os.getenv("KIWOOM_ACCOUNT_NO", "")


@router.get("/balance")
async def get_balance():
    """계좌 잔고 및 보유 종목 조회"""
    client = get_kiwoom_client()
    data = await client.get_balance(ACCOUNT_NO)
    output1 = data.get("output1", [])   # 보유 종목
    output2 = data.get("output2", {})   # 계좌 요약

    total_eval = int(output2.get("tot_evlu_amt", 0))
    deposit = int(output2.get("prvs_rcdl_excc_amt", 0))
    total_invest = settings.TOTAL_INVESTMENT
    profit_rate = (total_eval - total_invest) / total_invest * 100 if total_invest else 0

    return {
        "total_investment": total_invest,
        "total_eval_amount": total_eval,
        "deposit": deposit,
        "profit_rate": round(profit_rate, 2),
        "holdings": [
            {
                "code": h.get("pdno", ""),
                "name": h.get("prdt_name", ""),
                "quantity": int(h.get("hldg_qty", 0)),
                "avg_price": int(h.get("pchs_avg_pric", 0)),
                "current_price": int(h.get("prpr", 0)),
                "eval_profit_loss": int(h.get("evlu_pfls_amt", 0)),
                "profit_rate": float(h.get("evlu_pfls_rt", 0)),
            }
            for h in output1
        ],
    }
