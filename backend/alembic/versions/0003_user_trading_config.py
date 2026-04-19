"""user_trading_config — 유저별 투자금·키움 키 설정 테이블

Phase 2 에서는 total_investment 만 실제로 사용. kiwoom_app_key/secret/mock 은
스키마만 준비하고 (향후 Phase 2.5 에서 클라이언트 per-user 분리와 함께 활용).

Revision ID: 0003_user_trading_config
Revises: 0002_add_user_id_fk
Create Date: 2026-04-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_user_trading_config"
down_revision: Union[str, None] = "0002_add_user_id_fk"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_trading_config",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("total_investment", sa.Float(), nullable=False, server_default="10000000"),
        sa.Column("kiwoom_app_key", sa.String(length=200), nullable=True),
        sa.Column("kiwoom_secret_key", sa.String(length=200), nullable=True),
        sa.Column(
            "kiwoom_mock",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("user_trading_config")
