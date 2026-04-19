from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.auth import get_current_user
from app.db.database import get_db
from app.db.models import (
    ScreenedStock, Position, Order, BuySignal,
    PositionStatus, User,
)
from app.db.user_config import get_total_investment
from app.strategy.executor import calc_buy_qty

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
async def get_dashboard(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """대시보드 종합 데이터 — 현재 유저 기준"""
    # 스크리닝 종목은 전 유저 공용(시장 데이터)
    screened = (await db.execute(
        select(ScreenedStock).where(ScreenedStock.is_active == True)  # noqa: E712
        .order_by(ScreenedStock.drop_from_high.desc())
    )).scalars().all()

    # 보유 포지션 (유저 스코프)
    positions = (await db.execute(
        select(Position).where(
            Position.user_id == user.id,
            Position.status == PositionStatus.ACTIVE,
        )
    )).scalars().all()

    # 대기 중인 매수 신호
    pending_signals = (await db.execute(
        select(BuySignal)
        .where(
            BuySignal.user_id == user.id,
            BuySignal.is_executed == False,  # noqa: E712
        )
        .options(joinedload(BuySignal.stock))
    )).scalars().all()

    # 최근 주문 이력
    today_orders = (await db.execute(
        select(Order)
        .where(Order.user_id == user.id)
        .order_by(Order.created_at.desc())
        .limit(50)
    )).scalars().all()

    total_invest = await get_total_investment(db, user.id)

    return {
        "screened_stocks": [_format_screened(s) for s in screened],
        "positions": [_format_position(p) for p in positions],
        "pending_signals": [_format_signal(s, total_invest) for s in pending_signals],
        "today_orders": [_format_order(o) for o in today_orders],
    }


@router.get("/screened-stocks")
async def get_screened_stocks(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stocks = (await db.execute(
        select(ScreenedStock).where(ScreenedStock.is_active == True)  # noqa: E712
        .order_by(ScreenedStock.drop_from_high.desc())
    )).scalars().all()
    return [_format_screened(s) for s in stocks]


@router.get("/positions")
async def get_positions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    positions = (await db.execute(
        select(Position).where(
            Position.user_id == user.id,
            Position.status == PositionStatus.ACTIVE,
        )
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


def _format_signal(s: BuySignal, total_invest: float) -> dict:
    qty = calc_buy_qty(s.target_order_price, total_invest)
    amount = qty * s.target_order_price
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
