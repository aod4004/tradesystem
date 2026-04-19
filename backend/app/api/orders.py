import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.auth import get_current_user
from app.db.database import get_db, AsyncSessionLocal
from app.db.models import Order, BuySignal, User
from app.db.user_config import (
    get_total_investment, get_trading_config, list_trading_users,
)
from app.core.kiwoom_client import get_or_create_user_client
from app.strategy.executor import calc_buy_qty
from app.strategy.guards import check_buy_guards
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
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """수동 주문 — 현재 로그인 유저의 키움 키로 전송. 키 미등록 시 409."""
    cfg = await get_trading_config(db, user.id)
    if cfg is None or not cfg.kiwoom_app_key or not cfg.kiwoom_secret_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="keys_not_configured",
        )
    client = get_or_create_user_client(user.id, cfg)

    # 매수 주문이면 리스크 가드 검사 — 위반 시 422 로 차단 사유 반환.
    if req.order_type == "buy":
        deny = await check_buy_guards(
            db=db, user_id=user.id, cfg=cfg,
            stock_code=req.stock_code,
            price=req.price, qty=req.quantity, client=client,
        )
        if deny is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": f"guard_{deny.reason_code}", "message": deny.message},
            )

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
        .order_by(BuySignal.created_at.desc())
    )).scalars().all()
    total_invest = await get_total_investment(db, user.id)
    out = []
    for s in signals:
        qty = calc_buy_qty(s.target_order_price, total_invest)
        amount = qty * s.target_order_price
        ratio = (amount / total_invest * 100) if total_invest else 0
        out.append({
            "id": s.id,
            "stock_code": s.stock_code,
            "stock_name": s.stock_name or "",
            "source": s.source,
            "trigger_round": s.trigger_round,
            "target_order_price": s.target_order_price,
            "quantity": qty,
            "amount": amount,
            "investment_ratio": round(ratio, 2),
            "signal_date": s.signal_date.isoformat(),
            "is_excluded": s.is_excluded,
        })
    return out


class PendingSignalExcludeRequest(BaseModel):
    is_excluded: bool


@router.patch("/pending-signals/{signal_id}")
async def update_pending_signal(
    signal_id: int,
    req: PendingSignalExcludeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """내일 매수 예정 신호를 제외/복구. 이미 실행된 신호는 변경 불가."""
    signal = (await db.execute(
        select(BuySignal).where(
            BuySignal.id == signal_id,
            BuySignal.user_id == user.id,
        )
    )).scalar_one_or_none()
    if signal is None:
        raise HTTPException(status_code=404, detail="signal not found")
    if signal.is_executed:
        raise HTTPException(status_code=409, detail="already executed")
    signal.is_excluded = req.is_excluded
    await db.commit()
    return {"id": signal.id, "is_excluded": signal.is_excluded}


# ---------------------------------------------------------------------- #
#  수동 스크리닝 — 백그라운드 태스크 + 상태 폴링
# ---------------------------------------------------------------------- #
#
# `/run-screening` POST 가 스크리닝 종료까지 블로킹하면 프록시/프런트가 수 분간
# 기다려야 한다. 대신 태스크를 띄워 즉시 반환하고, 프런트는 `/status` 를 폴링.
# admin 1명이 한 번에 돌리는 구조라 모듈 전역 상태 1개면 충분.

_screening_state: dict = {
    "status": "idle",          # idle | running | completed | error
    "started_at": None,        # ISO8601
    "finished_at": None,
    "total": 0,                # 평가 대상 종목 수 (스크리너가 채움)
    "processed": 0,
    "selected": 0,             # 현재까지 통과한 종목 수
    "signal_count": None,      # 스크리닝 후 신호 감지 결과
    "user_count": None,
    "error": None,
}
_screening_task: asyncio.Task | None = None


def _snapshot_state() -> dict:
    return dict(_screening_state)


async def _run_screening_job() -> None:
    try:
        async with AsyncSessionLocal() as db:
            stocks = await run_screening(db, progress=_screening_state)
            _screening_state["selected"] = len(stocks)

            trading_users = await list_trading_users(db)
            signal_total = 0
            for u in trading_users:
                cfg = await get_trading_config(db, u.id)
                if cfg is None or not cfg.kiwoom_app_key or not cfg.kiwoom_secret_key:
                    continue
                client = get_or_create_user_client(u.id, cfg)
                try:
                    signals = await detect_buy_signals(db, u.id, client)
                    signal_total += len(signals)
                except Exception as e:
                    print(f"[orders] 유저 {u.id} 신호 감지 실패: {e}")

            _screening_state["signal_count"] = signal_total
            _screening_state["user_count"] = len(trading_users)
            _screening_state["status"] = "completed"
    except Exception as e:
        _screening_state["status"] = "error"
        _screening_state["error"] = str(e)
        print(f"[orders] 스크리닝 잡 실패: {e}")
    finally:
        _screening_state["finished_at"] = datetime.utcnow().isoformat()


@router.post("/run-screening")
async def trigger_screening(
    user: User = Depends(get_current_user),
):
    """수동 스크리닝 시작 (admin 전용). 즉시 반환하고 백그라운드로 실행."""
    global _screening_task
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin only")

    if _screening_state["status"] == "running":
        return _snapshot_state()

    _screening_state.update({
        "status": "running",
        "started_at": datetime.utcnow().isoformat(),
        "finished_at": None,
        "total": 0,
        "processed": 0,
        "selected": 0,
        "signal_count": None,
        "user_count": None,
        "error": None,
    })
    _screening_task = asyncio.create_task(_run_screening_job())
    return _snapshot_state()


@router.get("/run-screening/status")
async def get_screening_status(user: User = Depends(get_current_user)):
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin only")
    return _snapshot_state()
