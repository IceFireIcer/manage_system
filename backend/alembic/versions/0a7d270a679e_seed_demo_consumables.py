"""seed demo consumables

Revision ID: 0a7d270a679e
Revises: 631bec3432b3
Create Date: 2026-11-27 09:27:08.968405

"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0a7d270a679e'
down_revision: str | None = '631bec3432b3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEMO_CONSUMABLES = [
    {"name": "焊丝", "spec": "φ1.0mm", "unit": "卷", "location": "实训室一号柜",
     "safety_stock": 5, "current_stock": 20},
    {"name": "砂纸", "spec": "240目", "unit": "张", "location": "实训室一号柜",
     "safety_stock": 20, "current_stock": 50},
    {"name": "记号笔", "spec": "黑色", "unit": "支", "location": "实训室一号柜",
     "safety_stock": 8, "current_stock": 30},
    {"name": "电阻", "spec": "10kΩ 1/4W", "unit": "个", "location": "实训室一号柜",
     "safety_stock": 200, "current_stock": 500},
    {"name": "实训导线", "spec": "1mm² 红", "unit": "根", "location": "实训室一号柜",
     "safety_stock": 40, "current_stock": 100},
]


def _consumables_table() -> sa.Table:
    return sa.table(
        "consumables",
        sa.column("name", sa.String),
        sa.column("spec", sa.String),
        sa.column("unit", sa.String),
        sa.column("location", sa.String),
        sa.column("safety_stock", sa.Integer),
        sa.column("current_stock", sa.Integer),
        sa.column("class_id", sa.Integer),
        sa.column("status", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )


def upgrade() -> None:
    now = datetime.now()
    rows = [{**item, "class_id": 1, "status": "enabled",
             "created_at": now, "updated_at": now} for item in DEMO_CONSUMABLES]
    op.bulk_insert(_consumables_table(), rows)


def downgrade() -> None:
    names = [item["name"] for item in DEMO_CONSUMABLES]
    op.execute(
        sa.delete(_consumables_table()).where(
            _consumables_table().c.name.in_(names)
        )
    )
