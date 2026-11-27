"""stock alerts for low stock follow-up

Revision ID: j5e6f7a8b9c0
Revises: i4d5e6f7a8b9
Create Date: 2026-11-28 23:50:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "j5e6f7a8b9c0"
down_revision: str | Sequence[str] | None = "i4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stock_alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("consumable_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("risk_level", sa.String(length=8), nullable=False),
        sa.Column("current_stock", sa.Integer(), nullable=False),
        sa.Column("safety_stock", sa.Integer(), nullable=False),
        sa.Column("gap", sa.Integer(), nullable=False),
        sa.Column("occurrence", sa.Integer(), nullable=False),
        sa.Column("detection_count", sa.Integer(), nullable=False),
        sa.Column("first_detected_at", sa.DATETIME(), nullable=False),
        sa.Column("last_detected_at", sa.DATETIME(), nullable=False),
        sa.Column("acknowledged_at", sa.DATETIME(), nullable=True),
        sa.Column("acknowledged_by_id", sa.Integer(), nullable=True),
        sa.Column("acknowledged_by_name", sa.String(length=64), nullable=True),
        sa.Column("completed_at", sa.DATETIME(), nullable=True),
        sa.Column("completed_by_id", sa.Integer(), nullable=True),
        sa.Column("completed_by_name", sa.String(length=64), nullable=True),
        sa.Column("completed_reason", sa.String(length=255), nullable=True),
        sa.Column("last_order_no", sa.String(length=40), nullable=True),
        sa.Column("last_source", sa.String(length=24), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.CheckConstraint(
            "status IN ('open','acknowledged','completed')",
            name="ck_stock_alerts_status",
        ),
        sa.CheckConstraint(
            "risk_level IN ('low','out')", name="ck_stock_alerts_risk_level"
        ),
        sa.ForeignKeyConstraint(["acknowledged_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["completed_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["consumable_id"], ["consumables.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "class_id", "consumable_id", name="uq_stock_alerts_class_consumable"
        ),
    )
    op.create_index(
        "ix_stock_alerts_consumable_id", "stock_alerts", ["consumable_id"]
    )
    op.create_index(
        "ix_stock_alerts_class_status", "stock_alerts", ["class_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_stock_alerts_class_status", table_name="stock_alerts")
    op.drop_index("ix_stock_alerts_consumable_id", table_name="stock_alerts")
    op.drop_table("stock_alerts")