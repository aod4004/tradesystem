"""positions / orders / buy_signals 에 user_id FK 추가

기존 row 는 admin 유저로 backfill. admin 이 없으면 첫 번째 유저로,
그것도 없으면 NOT NULL 전환을 보류 (다음 배포에서 fix).

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
    # 1) nullable 로 컬럼 + FK + 인덱스 추가
    for table in _TABLES:
        op.add_column(table, sa.Column("user_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_user_id_users",
            table, "users",
            ["user_id"], ["id"],
        )
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])

    # 2) 기존 row 를 admin 유저(fallback: 최초 유저)로 backfill
    conn = op.get_bind()
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

    # 3) NULL 행이 하나도 없으면 NOT NULL 로 전환
    for table in _TABLES:
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
    for table in reversed(_TABLES):
        op.drop_index(f"ix_{table}_user_id", table_name=table)
        op.drop_constraint(f"fk_{table}_user_id_users", table, type_="foreignkey")
        op.drop_column(table, "user_id")
