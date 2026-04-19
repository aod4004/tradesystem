"""baseline — Phase 1 시점의 스키마 캡처

Base.metadata.create_all 을 그대로 쓰면 나중에 추가된 테이블(user_trading_config 등)까지
미리 만들어져서 다음 migration 과 충돌한다. Phase 1 기준 6개 테이블만 골라서 create.
- 빈 DB: 6개 테이블 생성
- 기존 DB: create_all 은 존재하는 테이블을 건드리지 않으므로 no-op

이후 스키마 변경은 별도 revision 으로 작성.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-04-19
"""
from typing import Sequence, Union

from alembic import op

from app.db.database import Base
from app.db import models  # noqa: F401 — metadata 등록


revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PHASE1_TABLES = {
    "users",
    "screened_stocks",
    "positions",
    "orders",
    "buy_signals",
    "system_config",
}


def upgrade() -> None:
    bind = op.get_bind()
    tables = [t for t in Base.metadata.sorted_tables if t.name in _PHASE1_TABLES]
    Base.metadata.create_all(bind=bind, tables=tables)


def downgrade() -> None:
    bind = op.get_bind()
    tables = [t for t in Base.metadata.sorted_tables if t.name in _PHASE1_TABLES]
    Base.metadata.drop_all(bind=bind, tables=tables)
