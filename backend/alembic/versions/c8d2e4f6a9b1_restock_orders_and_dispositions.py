"""restock orders, corrections and dispositions

Revision ID: c8d2e4f6a9b1
Revises: b7f3c0a1d2e4
Create Date: 2026-11-28 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8d2e4f6a9b1"
down_revision: str | None = "b7f3c0a1d2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "restock_orders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_no", sa.String(length=40), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("operator_id", sa.Integer(), nullable=False),
        sa.Column("operator_name", sa.String(length=64), nullable=False),
        sa.Column("operator_role", sa.String(length=16), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("business_at", sa.DATETIME(), nullable=False),
        sa.Column("posted_at", sa.DATETIME(), nullable=True),
        sa.Column("remark", sa.String(length=255), nullable=True),
        sa.Column("items_signature", sa.Text(), nullable=False),
        sa.Column("receipt_json", sa.Text(), nullable=True),
        sa.Column("errors_json", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.CheckConstraint(
            "status IN ('processing','submitted','rejected')",
            name="ck_restock_orders_status",
        ),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_no", name="uq_restock_orders_order_no"),
        sa.UniqueConstraint("request_id", name="uq_restock_orders_request_id"),
    )
    op.create_table(
        "restock_lines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("consumable_id", sa.Integer(), nullable=False),
        sa.Column("consumable_name", sa.String(length=128), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("stock_before", sa.Integer(), nullable=False),
        sa.Column("stock_after", sa.Integer(), nullable=False),
        sa.Column("corrected_quantity", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.ForeignKeyConstraint(["consumable_id"], ["consumables.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["restock_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "line_no", name="uq_restock_lines_order_line"),
    )
    op.create_table(
        "restock_corrections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("operator_id", sa.Integer(), nullable=False),
        sa.Column("operator_name", sa.String(length=64), nullable=False),
        sa.Column("operator_role", sa.String(length=16), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("business_at", sa.DATETIME(), nullable=False),
        sa.Column("posted_at", sa.DATETIME(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("remark", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["restock_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_id", name="uq_restock_corrections_request_id"
        ),
    )
    op.create_table(
        "restock_dispositions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("consumable_id", sa.Integer(), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("handler_id", sa.Integer(), nullable=True),
        sa.Column("handler_name", sa.String(length=64), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("order_no", sa.String(length=40), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','awaiting','partial','resolved')",
            name="ck_restock_dispositions_status",
        ),
        sa.ForeignKeyConstraint(["consumable_id"], ["consumables.id"]),
        sa.ForeignKeyConstraint(["handler_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["restock_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_restock_dispositions_consumable_id",
        "restock_dispositions",
        ["consumable_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_restock_dispositions_consumable_id", table_name="restock_dispositions"
    )
    op.drop_table("restock_dispositions")
    op.drop_table("restock_corrections")
    op.drop_table("restock_lines")
    op.drop_table("restock_orders")
