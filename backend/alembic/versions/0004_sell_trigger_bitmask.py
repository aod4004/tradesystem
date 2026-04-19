"""매도 조건 7종 확장 — Position.sold_triggers 비트마스크 + Order.sell_trigger_bit

매도 조건이 4개(5/10/15/20% 수익률) + 1개(MA20) → 4개 + 3개(MA20/60/120) 로 늘어나며,
각 조건은 1회만 발동하는 규칙이 명시화됨.

- Position.sold_triggers: 이 포지션에서 이미 발동된 조건의 비트마스크 (int, default 0)
- Order.sell_trigger_bit:  매도 주문 1건이 어떤 조건 때문에 나갔는지 기록 (nullable int)

Revision ID: 0004_sell_trigger_bitmask
Revises: 0003_user_trading_config
Create Date: 2026-04-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_sell_trigger_bitmask"
down_revision: Union[str, None] = "0003_user_trading_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector, table: str, column: str) -> bool:
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "positions" in inspector.get_table_names() and not _has_column(inspector, "positions", "sold_triggers"):
        op.add_column(
            "positions",
            sa.Column("sold_triggers", sa.Integer(), nullable=False, server_default="0"),
        )

    if "orders" in inspector.get_table_names() and not _has_column(inspector, "orders", "sell_trigger_bit"):
        op.add_column(
            "orders",
            sa.Column("sell_trigger_bit", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "orders" in inspector.get_table_names() and _has_column(inspector, "orders", "sell_trigger_bit"):
        op.drop_column("orders", "sell_trigger_bit")

    if "positions" in inspector.get_table_names() and _has_column(inspector, "positions", "sold_triggers"):
        op.drop_column("positions", "sold_triggers")
