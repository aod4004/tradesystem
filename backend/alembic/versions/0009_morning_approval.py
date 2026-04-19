"""user_trading_config: require_morning_approval 플래그

Phase 4.2 — 장 시작 전 사전 승인 모드. on 이면 08:50 스케줄러가 매수 주문을
즉시 전송하지 않고 승인 대기로 두고 카톡 요약만 보낸다. 유저가 UI 에서
"전체 승인 & 주문" 을 눌러야 실제 주문이 나간다.

Revision ID: 0009_morning_approval
Revises: 0008_risk_guard_fields
Create Date: 2026-04-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_morning_approval"
down_revision: Union[str, None] = "0008_risk_guard_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user_trading_config" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("user_trading_config")}

    if "require_morning_approval" not in cols:
        op.add_column(
            "user_trading_config",
            sa.Column(
                "require_morning_approval",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user_trading_config" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("user_trading_config")}
    if "require_morning_approval" in cols:
        op.drop_column("user_trading_config", "require_morning_approval")
