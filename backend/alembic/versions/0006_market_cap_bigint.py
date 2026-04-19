"""screened_stocks.market_cap Integer → BigInteger

원 단위 시가총액은 수천억~수조 범위라 int32(최대 21억) 를 넘음.
flo_stk × 1000 보정 이후 DataError (invalid input for query argument, value out of int32 range).

Revision ID: 0006_market_cap_bigint
Revises: 0005_watchlist_exclusion
Create Date: 2026-04-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_market_cap_bigint"
down_revision: Union[str, None] = "0005_watchlist_exclusion"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "screened_stocks" not in inspector.get_table_names():
        return
    cols = {c["name"]: c for c in inspector.get_columns("screened_stocks")}
    col = cols.get("market_cap")
    if col is None:
        return
    # 이미 BIGINT 면 스킵 (재실행 안전)
    col_type = str(col["type"]).upper()
    if "BIGINT" in col_type or "BIGINTEGER" in col_type:
        return
    op.alter_column(
        "screened_stocks", "market_cap",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "screened_stocks" not in inspector.get_table_names():
        return
    cols = {c["name"]: c for c in inspector.get_columns("screened_stocks")}
    col = cols.get("market_cap")
    if col is None:
        return
    col_type = str(col["type"]).upper()
    if "BIGINT" not in col_type and "BIGINTEGER" not in col_type:
        return
    # 값이 int32 를 넘어설 수 있어 다운그레이드 시 절단 위험 — 경고만 찍고 진행
    op.alter_column(
        "screened_stocks", "market_cap",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
    )
