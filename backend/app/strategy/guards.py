"""런타임 리스크 가드 — 매수 주문 전 사고 방지 체크 (Phase 4).

검사 항목 (순서대로, 가장 싼 쿼리부터 DB → API):
  1) 종목당 투자한도 비율   max_position_ratio (default: settings.MAX_POSITION_RATIO)
  2) 일일 매수 건수 한도    daily_order_count_limit (None = 제한 없음)
  3) 일일 매수 금액 한도    daily_order_amount_limit (None = 제한 없음)
  4) 주문가능금액 vs 필요금액  client.get_deposit() 의 ord_alow_amt

가드 전체 on/off = cfg.risk_guards_enabled. False 면 모든 체크 스킵.

매도/취소 주문은 가드 적용 대상이 아니다 (수익 실현/손실 방지를 막으면 안 되므로).
호출부는 `check_buy_guards(...)` 가 `GuardDenied` 를 반환하면 주문을 내지 말고 알림만 전송한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.kiwoom_client import KiwoomClient, to_int
from app.db.models import (
    Order, OrderStatus, OrderType,
    Position, PositionStatus,
    UserTradingConfig,
)
from app.db.user_config import get_total_investment


_KST = ZoneInfo("Asia/Seoul")
_UTC = ZoneInfo("UTC")


@dataclass
class GuardDenied:
    reason_code: str   # 'position_ratio' | 'daily_count' | 'daily_amount' | 'insufficient_cash'
    message: str       # 유저에게 보낼 한국어 사유


def _today_kst_range_utc() -> tuple[datetime, datetime]:
    """KST 기준 오늘 00:00~내일 00:00 을 UTC naive datetime 으로 반환 (DB created_at 은 naive UTC)."""
    now_kst = datetime.now(_KST)
    start_kst = datetime(now_kst.year, now_kst.month, now_kst.day, tzinfo=_KST)
    end_kst = start_kst + timedelta(days=1)
    return (
        start_kst.astimezone(_UTC).replace(tzinfo=None),
        end_kst.astimezone(_UTC).replace(tzinfo=None),
    )


async def check_buy_guards(
    *,
    db: AsyncSession,
    user_id: int,
    cfg: UserTradingConfig | None,
    stock_code: str,
    price: int,
    qty: int,
    client: KiwoomClient | None,
) -> GuardDenied | None:
    """매수 주문 직전 가드. 통과면 None, 차단이면 GuardDenied."""
    if cfg is not None and not cfg.risk_guards_enabled:
        return None

    order_amount = max(0, int(price) * int(qty))
    if order_amount <= 0:
        return None

    # 1) 종목당 비율 ----------------------------------------------------
    ratio = (
        float(cfg.max_position_ratio)
        if (cfg and cfg.max_position_ratio is not None)
        else float(settings.MAX_POSITION_RATIO)
    )
    if ratio > 0:
        total_invest = await get_total_investment(db, user_id)
        cap = total_invest * ratio
        committed = await _committed_for_stock(db, user_id, stock_code)
        if committed + order_amount > cap:
            return GuardDenied(
                "position_ratio",
                f"종목당 투자한도 초과: 기존 {int(committed):,}원 + 신규 {order_amount:,}원 "
                f"> 한도 {int(cap):,}원 (총투자금의 {ratio*100:.1f}%)",
            )

    # 2)/3) 일일 한도 --------------------------------------------------
    if cfg and (cfg.daily_order_count_limit is not None or cfg.daily_order_amount_limit is not None):
        start, end = _today_kst_range_utc()
        if cfg.daily_order_count_limit is not None:
            count_today = await _daily_buy_order_count(db, user_id, start, end)
            if count_today + 1 > cfg.daily_order_count_limit:
                return GuardDenied(
                    "daily_count",
                    f"일일 매수 건수 한도 {cfg.daily_order_count_limit}건 초과 (현재 {count_today}건)",
                )
        if cfg.daily_order_amount_limit is not None:
            amount_today = await _daily_buy_order_amount(db, user_id, start, end)
            if amount_today + order_amount > cfg.daily_order_amount_limit:
                return GuardDenied(
                    "daily_amount",
                    f"일일 매수 금액 한도 {int(cfg.daily_order_amount_limit):,}원 초과 "
                    f"(현재 {int(amount_today):,}원 + 신규 {order_amount:,}원)",
                )

    # 4) 예수금 -------------------------------------------------------
    # API 호출이 제일 비싸므로 마지막에. 조회 실패는 차단까지 가지 않고 스킵 (가용한데 막는 것 방지).
    if client is not None:
        try:
            dep = await client.get_deposit()
            available = to_int(dep.get("ord_alow_amt"))
        except Exception as e:
            print(f"[guards] user={user_id} 예수금 조회 실패 — 가드 스킵: {e}")
            return None
        if available < order_amount:
            return GuardDenied(
                "insufficient_cash",
                f"주문가능금액 {available:,}원 < 필요금액 {order_amount:,}원",
            )

    return None


async def _committed_for_stock(
    db: AsyncSession, user_id: int, stock_code: str,
) -> float:
    """이미 반영된 해당 종목 매입액 = 활성 포지션의 total_buy_amount + SUBMITTED 매수 주문 합.

    SUBMITTED 는 체결 전 in-flight 라 Position.total_buy_amount 에 아직 들어가지 않았다.
    FILLED 는 이미 Position 에 반영돼 있어 중복 집계 안 됨.
    """
    pos_amount = (
        await db.execute(
            select(func.coalesce(func.sum(Position.total_buy_amount), 0.0)).where(
                Position.user_id == user_id,
                Position.stock_code == stock_code,
                Position.status == PositionStatus.ACTIVE,
            )
        )
    ).scalar_one()
    open_buys = (
        await db.execute(
            select(
                func.coalesce(func.sum(Order.order_price * Order.order_qty), 0)
            ).where(
                Order.user_id == user_id,
                Order.stock_code == stock_code,
                Order.order_type == OrderType.BUY,
                Order.status == OrderStatus.SUBMITTED,
            )
        )
    ).scalar_one()
    return float(pos_amount or 0) + float(open_buys or 0)


async def _daily_buy_order_count(
    db: AsyncSession, user_id: int, start: datetime, end: datetime,
) -> int:
    return int(
        (
            await db.execute(
                select(func.count(Order.id)).where(
                    Order.user_id == user_id,
                    Order.order_type == OrderType.BUY,
                    Order.status != OrderStatus.CANCELLED,
                    Order.created_at >= start,
                    Order.created_at < end,
                )
            )
        ).scalar_one()
        or 0
    )


async def _daily_buy_order_amount(
    db: AsyncSession, user_id: int, start: datetime, end: datetime,
) -> float:
    return float(
        (
            await db.execute(
                select(
                    func.coalesce(func.sum(Order.order_price * Order.order_qty), 0)
                ).where(
                    Order.user_id == user_id,
                    Order.order_type == OrderType.BUY,
                    Order.status != OrderStatus.CANCELLED,
                    Order.created_at >= start,
                    Order.created_at < end,
                )
            )
        ).scalar_one()
        or 0
    )
