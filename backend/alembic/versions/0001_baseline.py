"""baseline — 기존 스키마 캡처 (create_all 사용)

이 migration 은 init_db() 와 동일한 create_all 을 그대로 실행한다.
- 빈 DB: 전체 테이블 생성 (이전 init_db 와 동일 결과)
- 기존 DB: create_all 은 존재하는 테이블을 건드리지 않으므로 no-op → 안전 채택

이후 모든 스키마 변경은 별도 revision 으로 작성한다.

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


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
