"""관심종목 테이블 + BuySignal 확장 (stock_name / source / is_excluded) + FK 완화

유저가 직접 추가하는 관심 종목은 ScreenedStock 에 없을 수 있으므로
positions.stock_code / buy_signals.stock_code 의 FK(→ screened_stocks.code)를 드롭.
참조 무결성은 애플리케이션 레이어에서 보장.

Revision ID: 0005_watchlist_exclusion
Revises: 0004_sell_trigger_bitmask
Create Date: 2026-04-19

주의: revision ID 는 alembic_version.version_num VARCHAR(32) 제약에 맞춰 짧게 유지.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_watchlist_exclusion"
down_revision: Union[str, None] = "0004_sell_trigger_bitmask"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector, table: str, column: str) -> bool:
    return column in {c["name"] for c in inspector.get_columns(table)}


def _find_fk(inspector, table: str, target: str) -> str | None:
    for fk in inspector.get_foreign_keys(table):
        if fk.get("referred_table") == target:
            return fk.get("name")
    return None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # 1) FK 드롭 — 관심종목이 ScreenedStock 에 없어도 되게
    for table in ("positions", "buy_signals"):
        if table not in tables:
            continue
        fk_name = _find_fk(inspector, table, "screened_stocks")
        if fk_name:
            op.drop_constraint(fk_name, table, type_="foreignkey")

    # 2) buy_signals 에 컬럼 추가
    if "buy_signals" in tables:
        if not _has_column(inspector, "buy_signals", "stock_name"):
            op.add_column(
                "buy_signals",
                sa.Column("stock_name", sa.String(length=50), nullable=True),
            )
        if not _has_column(inspector, "buy_signals", "source"):
            op.add_column(
                "buy_signals",
                sa.Column(
                    "source", sa.String(length=20),
                    nullable=False, server_default="screening",
                ),
            )
        if not _has_column(inspector, "buy_signals", "is_excluded"):
            op.add_column(
                "buy_signals",
                sa.Column(
                    "is_excluded", sa.Boolean(),
                    nullable=False, server_default=sa.text("false"),
                ),
            )

    # 3) user_watchlist 테이블 생성
    if "user_watchlist" not in tables:
        op.create_table(
            "user_watchlist",
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
            sa.Column("stock_code", sa.String(length=10), primary_key=True),
            sa.Column("stock_name", sa.String(length=50), nullable=False),
            sa.Column(
                "added_at", sa.DateTime(),
                nullable=False, server_default=sa.func.now(),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "user_watchlist" in tables:
        op.drop_table("user_watchlist")

    if "buy_signals" in tables:
        for col in ("is_excluded", "source", "stock_name"):
            if _has_column(inspector, "buy_signals", col):
                op.drop_column("buy_signals", col)

    # FK 복구는 데이터 정합성 이슈 있을 수 있어 생략 (downgrade 는 정보 손실 허용)
