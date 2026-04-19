"""positions / orders / buy_signals 에 user_id FK 추가 (idempotent)

기존 row 는 admin 유저로 backfill. admin 이 없으면 첫 번째 유저로,
그것도 없으면 NOT NULL 전환을 보류 (다음 배포에서 fix).

이전 실패/부분 commit 에서 살아남은 상태를 견디기 위해 모든 단계를 inspector 로 체크.

Revision ID: 0002_add_user_id_fk
Revises: 0001_baseline
Create Date: 2026-04-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_add_user_id_fk"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLES = ("positions", "orders", "buy_signals")


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # 1) nullable 로 컬럼 + FK + 인덱스 추가 — 이미 있으면 스킵
    for table in _TABLES:
        cols = {c["name"] for c in inspector.get_columns(table)}
        if "user_id" in cols:
            continue
        op.add_column(table, sa.Column("user_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_user_id_users",
            table, "users",
            ["user_id"], ["id"],
        )
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])

    # inspector 캐시 갱신 (위에서 컬럼 추가했을 수 있음)
    inspector = sa.inspect(conn)

    # 2) 기존 row 를 admin 유저(fallback: 최초 유저)로 backfill
    owner_id = conn.execute(sa.text(
        "SELECT id FROM users WHERE is_admin = true ORDER BY id LIMIT 1"
    )).scalar()
    if owner_id is None:
        owner_id = conn.execute(sa.text(
            "SELECT id FROM users ORDER BY id LIMIT 1"
        )).scalar()

    if owner_id is not None:
        for table in _TABLES:
            conn.execute(
                sa.text(f"UPDATE {table} SET user_id = :uid WHERE user_id IS NULL"),
                {"uid": owner_id},
            )

    # 3) NULL 행이 하나도 없으면 NOT NULL 로 전환 — 이미 NOT NULL 이면 no-op
    for table in _TABLES:
        col = next(
            (c for c in inspector.get_columns(table) if c["name"] == "user_id"),
            None,
        )
        if col is None or col["nullable"] is False:
            continue
        null_count = conn.execute(
            sa.text(f"SELECT COUNT(*) FROM {table} WHERE user_id IS NULL")
        ).scalar() or 0
        if null_count == 0:
            op.alter_column(table, "user_id", existing_type=sa.Integer(), nullable=False)
        else:
            print(
                f"[alembic 0002] 경고: {table} 의 user_id NULL 행 {null_count}건 — "
                f"NOT NULL 전환 보류. admin 생성 후 다시 migrate 하세요."
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    for table in reversed(_TABLES):
        cols = {c["name"] for c in inspector.get_columns(table)}
        if "user_id" not in cols:
            continue
        indexes = {idx["name"] for idx in inspector.get_indexes(table)}
        if f"ix_{table}_user_id" in indexes:
            op.drop_index(f"ix_{table}_user_id", table_name=table)
        fks = {fk["name"] for fk in inspector.get_foreign_keys(table)}
        if f"fk_{table}_user_id_users" in fks:
            op.drop_constraint(f"fk_{table}_user_id_users", table, type_="foreignkey")
        op.drop_column(table, "user_id")
