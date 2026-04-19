"""user_trading_config: 카카오 OAuth 토큰 + notifications_enabled

Phase 3 알림 채널 (카카오톡 "나에게 보내기").

Revision ID: 0007_kakao_notify_fields
Revises: 0006_market_cap_bigint
Create Date: 2026-04-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_kakao_notify_fields"
down_revision: Union[str, None] = "0006_market_cap_bigint"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user_trading_config" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("user_trading_config")}

    if "kakao_access_token" not in cols:
        op.add_column(
            "user_trading_config",
            sa.Column("kakao_access_token", sa.String(500), nullable=True),
        )
    if "kakao_refresh_token" not in cols:
        op.add_column(
            "user_trading_config",
            sa.Column("kakao_refresh_token", sa.String(500), nullable=True),
        )
    if "kakao_access_expires_at" not in cols:
        op.add_column(
            "user_trading_config",
            sa.Column("kakao_access_expires_at", sa.DateTime(), nullable=True),
        )
    if "kakao_refresh_expires_at" not in cols:
        op.add_column(
            "user_trading_config",
            sa.Column("kakao_refresh_expires_at", sa.DateTime(), nullable=True),
        )
    if "notifications_enabled" not in cols:
        op.add_column(
            "user_trading_config",
            sa.Column(
                "notifications_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user_trading_config" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("user_trading_config")}
    for name in (
        "notifications_enabled",
        "kakao_refresh_expires_at",
        "kakao_access_expires_at",
        "kakao_refresh_token",
        "kakao_access_token",
    ):
        if name in cols:
            op.drop_column("user_trading_config", name)
