from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db
from app.db.models import Order, BuySignal
from app.core.kiwoom_client import get_kiwoom_client
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
async def manual_order(req: ManualOrderRequest):
    """수동 주문"""
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
async def get_today_orders(db: AsyncSession = Depends(get_db)):
    orders = (await db.execute(
        select(Order).order_by(Order.created_at.desc()).limit(100)
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
async def get_pending_signals(db: AsyncSession = Depends(get_db)):
    signals = (await db.execute(
        select(BuySignal).where(BuySignal.is_executed == False)
    )).scalars().all()
    return [
        {
            "stock_code": s.stock_code,
            "trigger_round": s.trigger_round,
            "target_order_price": s.target_order_price,
            "signal_date": s.signal_date.isoformat(),
        }
        for s in signals
    ]


@router.post("/run-screening")
async def trigger_screening(db: AsyncSession = Depends(get_db)):
    """수동 스크리닝 실행 (테스트용)"""
    stocks = await run_screening(db)
    signals = await detect_buy_signals(db)
    return {
        "screened_count": len(stocks),
        "signal_count": len(signals),
    }
