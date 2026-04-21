"""user_trading_config: 키움 조건검색 seq / 이름

영웅문4 에 저장한 조건식(seq=0,1,2,... / name 임의)을 참조해 매일 15:40 스크리닝을
전종목 순회 대신 CNSRREQ 1회 호출로 대체. NULL 이면 기존 전종목 스크리닝 fallback.

Revision ID: 0010_condition_search
Revises: 0009_morning_approval
Create Date: 2026-04-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_condition_search"
down_revision: Union[str, None] = "0009_morning_approval"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user_trading_config" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("user_trading_config")}

    if "condition_seq" not in cols:
        op.add_column(
            "user_trading_config",
            sa.Column("condition_seq", sa.Integer(), nullable=True),
        )
    if "condition_name" not in cols:
        op.add_column(
            "user_trading_config",
            sa.Column("condition_name", sa.String(length=50), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user_trading_config" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("user_trading_config")}
    if "condition_name" in cols:
        op.drop_column("user_trading_config", "condition_name")
    if "condition_seq" in cols:
        op.drop_column("user_trading_config", "condition_seq")
