"""user_trading_config: 런타임 리스크 가드 설정 필드

Phase 4 실거래 가드레일. 유저별로 일일 주문 금액/건수 상한, 종목당 투자금 비율 상한,
가드 전체 on/off 플래그를 저장. 모두 None/default 면 기존 동작과 동일.

Revision ID: 0008_risk_guard_fields
Revises: 0007_kakao_notify_fields
Create Date: 2026-04-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_risk_guard_fields"
down_revision: Union[str, None] = "0007_kakao_notify_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user_trading_config" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("user_trading_config")}

    if "daily_order_amount_limit" not in cols:
        op.add_column(
            "user_trading_config",
            sa.Column("daily_order_amount_limit", sa.Float(), nullable=True),
        )
    if "daily_order_count_limit" not in cols:
        op.add_column(
            "user_trading_config",
            sa.Column("daily_order_count_limit", sa.Integer(), nullable=True),
        )
    if "max_position_ratio" not in cols:
        op.add_column(
            "user_trading_config",
            sa.Column("max_position_ratio", sa.Float(), nullable=True),
        )
    if "risk_guards_enabled" not in cols:
        op.add_column(
            "user_trading_config",
            sa.Column(
                "risk_guards_enabled",
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
        "risk_guards_enabled",
        "max_position_ratio",
        "daily_order_count_limit",
        "daily_order_amount_limit",
    ):
        if name in cols:
            op.drop_column("user_trading_config", name)
