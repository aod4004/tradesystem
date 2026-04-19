from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import joinedload

from app.config import settings
from app.db.database import get_db
from app.db.models import ScreenedStock, Position, Order, BuySignal, PositionStatus, OrderStatus
from app.core.kiwoom_client import get_kiwoom_client
from app.strategy.executor import calc_buy_qty

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
async def get_dashboard(db: AsyncSession = Depends(get_db)):
    """대시보드 종합 데이터"""
    client = get_kiwoom_client()

    # 스크리닝 종목
    screened = (await db.execute(
        select(ScreenedStock).where(ScreenedStock.is_active == True).order_by(ScreenedStock.drop_from_high.desc())
    )).scalars().all()

    # 보유 포지션
    positions = (await db.execute(
        select(Position).where(Position.status == PositionStatus.ACTIVE)
    )).scalars().all()

    # 오늘 대기 중인 매수 신호
    pending_signals = (await db.execute(
        select(BuySignal)
        .where(BuySignal.is_executed == False)
        .options(joinedload(BuySignal.stock))
    )).scalars().all()

    # 오늘 주문 이력
    today_orders = (await db.execute(
        select(Order).order_by(Order.created_at.desc()).limit(50)
    )).scalars().all()

    return {
        "screened_stocks": [_format_screened(s) for s in screened],
        "positions": [_format_position(p) for p in positions],
        "pending_signals": [_format_signal(s) for s in pending_signals],
        "today_orders": [_format_order(o) for o in today_orders],
    }


@router.get("/screened-stocks")
async def get_screened_stocks(db: AsyncSession = Depends(get_db)):
    stocks = (await db.execute(
        select(ScreenedStock).where(ScreenedStock.is_active == True)
        .order_by(ScreenedStock.drop_from_high.desc())
    )).scalars().all()
    return [_format_screened(s) for s in stocks]


@router.get("/positions")
async def get_positions(db: AsyncSession = Depends(get_db)):
    positions = (await db.execute(
        select(Position).where(Position.status == PositionStatus.ACTIVE)
    )).scalars().all()
    return [_format_position(p) for p in positions]


def _format_screened(s: ScreenedStock) -> dict:
    return {
        "code": s.code,
        "name": s.name,
        "market": s.market,
        "current_price": s.current_price,
        "high_1y": s.high_1y,
        "low_1y": s.low_1y,
        "drop_from_high": s.drop_from_high,
        "rise_from_low": s.rise_from_low,
        "market_cap": s.market_cap,
        "net_income": s.net_income,
        "operating_income": s.operating_income,
        "foreign_ratio": s.foreign_ratio,
        "screened_at": s.screened_at.isoformat() if s.screened_at else None,
    }


def _format_position(p: Position) -> dict:
    return {
        "id": p.id,
        "stock_code": p.stock_code,
        "stock_name": p.stock_name,
        "quantity": p.quantity,
        "avg_buy_price": p.avg_buy_price,
        "buy_rounds_done": p.buy_rounds_done,
        "sell_rounds_done": p.sell_rounds_done,
        "status": p.status.value,
    }


def _format_signal(s: BuySignal) -> dict:
    qty = calc_buy_qty(s.target_order_price)
    amount = qty * s.target_order_price
    total_invest = settings.TOTAL_INVESTMENT
    ratio = (amount / total_invest * 100) if total_invest else 0
    return {
        "stock_code": s.stock_code,
        "stock_name": s.stock.name if s.stock else "",
        "trigger_round": s.trigger_round,
        "target_order_price": s.target_order_price,
        "quantity": qty,
        "amount": amount,
        "investment_ratio": round(ratio, 2),
        "signal_date": s.signal_date.isoformat(),
        "is_executed": s.is_executed,
    }


def _format_order(o: Order) -> dict:
    return {
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
