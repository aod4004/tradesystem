"""유저별 트레이딩 설정 조회 헬퍼.

Phase 2 에서는 total_investment 만 per-user 로 동작하고, 키움 키/모의 플래그는
스키마에만 존재(미사용) — 실제 키움 호출은 여전히 env 의 단일 키를 사용한다.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import User, UserTradingConfig


async def get_trading_config(db: AsyncSession, user_id: int) -> UserTradingConfig | None:
    return (
        await db.execute(
            select(UserTradingConfig).where(UserTradingConfig.user_id == user_id)
        )
    ).scalar_one_or_none()


async def get_total_investment(db: AsyncSession, user_id: int) -> float:
    cfg = await get_trading_config(db, user_id)
    if cfg is None or cfg.total_investment <= 0:
        return float(settings.TOTAL_INVESTMENT)
    return float(cfg.total_investment)


async def list_trading_users(db: AsyncSession) -> list[User]:
    """자동매매 대상 유저 — is_active 이고 trading_config 가 존재하는 유저만."""
    rows = (
        await db.execute(
            select(User)
            .join(UserTradingConfig, UserTradingConfig.user_id == User.id)
            .where(User.is_active == True)  # noqa: E712
            .order_by(User.id)
        )
    ).scalars().all()
    return list(rows)
