"""Phase 4.2 — 장 시작 전 사전 승인 모드 헬퍼.

`UserTradingConfig.require_morning_approval` 가 true 면 08:50 스케줄러가
`execute_pending_buy_orders` 를 실행하지 않고 대기 신호를 요약해 카톡으로 보낸다.
유저는 대시보드에서 확인·제외 후 "전체 승인 & 주문" 으로 수동 트리거한다.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BuySignal
from app.strategy.executor import calc_buy_qty


async def summarize_pending_signals(
    db: AsyncSession, user_id: int, total_investment: float,
) -> dict:
    """해당 유저의 미실행 매수 신호를 요약. is_excluded 는 제외하고 합산.

    반환: {"count": N, "total_amount": 원, "items": [{stock_code, stock_name, trigger_round,
                                                       target_order_price, quantity, amount}, ...]}
    """
    signals = (
        await db.execute(
            select(BuySignal).where(
                BuySignal.user_id == user_id,
                BuySignal.is_executed == False,  # noqa: E712
                BuySignal.is_excluded == False,  # noqa: E712
            )
            .order_by(BuySignal.trigger_round.asc(), BuySignal.created_at.asc())
        )
    ).scalars().all()

    items: list[dict] = []
    total_amount = 0.0
    for s in signals:
        qty = calc_buy_qty(s.target_order_price, total_investment)
        if qty <= 0:
            continue
        amount = qty * s.target_order_price
        total_amount += amount
        items.append({
            "id": s.id,
            "stock_code": s.stock_code,
            "stock_name": s.stock_name or "",
            "trigger_round": s.trigger_round,
            "target_order_price": s.target_order_price,
            "quantity": qty,
            "amount": amount,
        })

    return {"count": len(items), "total_amount": total_amount, "items": items}
