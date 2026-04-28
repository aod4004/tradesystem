"""orders.stock_code 인덱스를 unique → non-unique 로 교정

옛 버전 모델에 unique=True 가 잘못 들어가 있던 흔적이 ix_orders_stock_code 에
UNIQUE 제약으로 굳어져 있었다. 모델은 index=True (non-unique) 인데 DB 만 다른 상태.

증상: 같은 종목에 매도 tranche 1~5 주문을 등록할 때 두번째부터
UniqueViolationError("ix_orders_stock_code") 로 실패. 사전 매도 등록·일반 매도 모두 깨짐.

수정: 인덱스를 drop 후 동일 이름으로 non-unique 재생성. 데이터는 건드리지 않음.

Revision ID: 0011_orders_stock_code_nonunique
Revises: 0010_condition_search
Create Date: 2026-04-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_orders_stock_code_nonunique"
down_revision: Union[str, None] = "0010_condition_search"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "orders" not in inspector.get_table_names():
        return
    indexes = {ix["name"]: ix for ix in inspector.get_indexes("orders")}
    existing = indexes.get("ix_orders_stock_code")
    # 이미 non-unique 면 no-op
    if existing is None:
        op.create_index(
            "ix_orders_stock_code", "orders", ["stock_code"], unique=False,
        )
        return
    if existing.get("unique"):
        op.drop_index("ix_orders_stock_code", table_name="orders")
        op.create_index(
            "ix_orders_stock_code", "orders", ["stock_code"], unique=False,
        )


def downgrade() -> None:
    # unique 로 되돌리면 기존 중복행 때문에 실패할 수 있으므로 그대로 둔다 — no-op
    pass
