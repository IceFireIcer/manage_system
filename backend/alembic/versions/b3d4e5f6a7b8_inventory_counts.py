"""inventory counts and adjustment posting

Revision ID: b3d4e5f6a7b8
Revises: f1a2b3c4d5e6
Create Date: 2026-11-28 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3d4e5f6a7b8"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "inventory_counts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_no", sa.String(length=40), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("count_at", sa.DATETIME(), nullable=False),
        sa.Column("counter_id", sa.Integer(), nullable=False),
        sa.Column("counter_name", sa.String(length=64), nullable=False),
        sa.Column("counter_role", sa.String(length=16), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=False),
        sa.Column("reviewer_name", sa.String(length=64), nullable=False),
        sa.Column("reviewer_role", sa.String(length=16), nullable=False),
        sa.Column("operator_id", sa.Integer(), nullable=False),
        sa.Column("operator_name", sa.String(length=64), nullable=False),
        sa.Column("operator_role", sa.String(length=16), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("remark", sa.String(length=255), nullable=True),
        sa.Column("warning", sa.Text(), nullable=True),
        sa.Column("posted_at", sa.DATETIME(), nullable=True),
        sa.Column("posted_by_id", sa.Integer(), nullable=True),
        sa.Column("posted_by_name", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.CheckConstraint(
            "status IN ('open','posted')",
            name="ck_inventory_counts_status",
        ),
        sa.ForeignKeyConstraint(["counter_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["posted_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_no", name="uq_inventory_counts_order_no"),
        sa.UniqueConstraint("request_id", name="uq_inventory_counts_request_id"),
    )

    op.create_table(
        "inventory_count_lines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("count_id", sa.Integer(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("consumable_id", sa.Integer(), nullable=False),
        sa.Column("consumable_name", sa.String(length=128), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("baseline_stock", sa.Integer(), nullable=False),
        sa.Column("baseline_seq", sa.Integer(), nullable=False),
        sa.Column("counted_qty", sa.Integer(), nullable=True),
        sa.Column("period_delta", sa.Integer(), nullable=False),
        sa.Column("diff", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.ForeignKeyConstraint(["consumable_id"], ["consumables.id"]),
        sa.ForeignKeyConstraint(["count_id"], ["inventory_counts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "count_id", "line_no", name="uq_inventory_count_lines_no"
        ),
    )
    op.create_index(
        "ix_inventory_count_lines_count_id",
        "inventory_count_lines",
        ["count_id"],
    )
    op.create_index(
        "ix_inventory_count_lines_consumable_id",
        "inventory_count_lines",
        ["consumable_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_inventory_count_lines_consumable_id",
        table_name="inventory_count_lines",
    )
    op.drop_index(
        "ix_inventory_count_lines_count_id", table_name="inventory_count_lines"
    )
    op.drop_table("inventory_count_lines")
    op.drop_table("inventory_counts")