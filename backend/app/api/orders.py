from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.auth import get_current_user
from app.db.database import get_db
from app.db.models import Order, BuySignal, User
from app.db.user_config import get_total_investment, list_trading_users
from app.core.kiwoom_client import get_kiwoom_client
from app.strategy.executor import calc_buy_qty
from app.strategy.screener import run_screening
from app.strategy.signal import detect_buy_signals

router = APIRouter(prefix="/api/orders", tags=["orders"])


class ManualOrderRequest(BaseModel):
    stock_code: str
    order_type: str     # "buy" | "sell"
    quantity: int
    price: int = 0      # 0이면 시장가
    trade_type: str = "0"   # 0=지정가, 3=시장가


@router.post("/manual")
async def manual_order(
    req: ManualOrderRequest,
    _user: User = Depends(get_current_user),
):
    """수동 주문 — 키움에만 전송하고 로컬 Order 는 WS 체결 이벤트에서 생성됨.

    주의: 현재는 단일 키움 계정(env 기반) 사용. Phase 2.5 에서 유저별 키 분리.
    """
    client = get_kiwoom_client()
    resp = await client.place_order(
        stock_code=req.stock_code,
        order_type=req.order_type,
        quantity=req.quantity,
        price=req.price,
        trade_type=req.trade_type,
    )
    return {"success": True, "response": resp}


@router.get("/today")
async def get_today_orders(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    orders = (await db.execute(
        select(Order)
        .where(Order.user_id == user.id)
        .order_by(Order.created_at.desc())
        .limit(100)
    )).scalars().all()
    return [
        {
            "id": o.id,
            "stock_code": o.stock_code,
            "stock_name": o.stock_name,
            "order_type": o.order_type.value,
            "order_round": o.order_round,
            "order_price": o.order_price,
            "order_qty": o.order_qty,
            "filled_price": o.filled_price,
            "filled_qty": o.filled_qty,
            "status": o.status.value,
            "created_at": o.created_at.isoformat(),
        }
        for o in orders
    ]


@router.get("/pending-signals")
async def get_pending_signals(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    signals = (await db.execute(
        select(BuySignal)
        .where(
            BuySignal.user_id == user.id,
            BuySignal.is_executed == False,  # noqa: E712
        )
        .options(joinedload(BuySignal.stock))
    )).scalars().all()
    total_invest = await get_total_investment(db, user.id)
    out = []
    for s in signals:
        qty = calc_buy_qty(s.target_order_price, total_invest)
        amount = qty * s.target_order_price
        ratio = (amount / total_invest * 100) if total_invest else 0
        out.append({
            "stock_code": s.stock_code,
            "stock_name": s.stock.name if s.stock else "",
            "trigger_round": s.trigger_round,
            "target_order_price": s.target_order_price,
            "quantity": qty,
            "amount": amount,
            "investment_ratio": round(ratio, 2),
            "signal_date": s.signal_date.isoformat(),
        })
    return out


@router.post("/run-screening")
async def trigger_screening(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """수동 스크리닝 실행 (admin 전용).

    스크리닝은 글로벌(시장 데이터 기반)이지만 BuySignal 은 유저별로 생성되므로
    trading_config 를 가진 모든 활성 유저에 대해 신호를 생성한다.
    """
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin only")

    stocks = await run_screening(db)

    signal_total = 0
    trading_users = await list_trading_users(db)
    for u in trading_users:
        signals = await detect_buy_signals(db, u.id)
        signal_total += len(signals)

    return {
        "screened_count": len(stocks),
        "signal_count": signal_total,
        "user_count": len(trading_users),
    }
