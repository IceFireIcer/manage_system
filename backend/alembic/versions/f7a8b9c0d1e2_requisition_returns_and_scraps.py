"""requisition returns and scraps

Revision ID: f7a8b9c0d1e2
Revises: e5f6a7b8c9d0
Create Date: 2026-11-28 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("inventory_events") as batch_op:
        batch_op.drop_constraint("ck_inventory_events_delta", type_="check")
        batch_op.create_check_constraint(
            "ck_inventory_events_delta",
            "delta <> 0 OR event_type = 'scrap'",
        )

    op.create_table(
        "requisition_returns",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_no", sa.String(length=40), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("requisition_id", sa.Integer(), nullable=False),
        sa.Column("consumable_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("stock_before", sa.Integer(), nullable=False),
        sa.Column("stock_after", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("remark", sa.String(length=255), nullable=True),
        sa.Column("operator_id", sa.Integer(), nullable=False),
        sa.Column("operator_name", sa.String(length=64), nullable=False),
        sa.Column("operator_role", sa.String(length=16), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("business_at", sa.DATETIME(), nullable=False),
        sa.Column("posted_at", sa.DATETIME(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.ForeignKeyConstraint(["consumable_id"], ["consumables.id"]),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["requisition_id"], ["requisitions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "order_no", name="uq_requisition_returns_order_no"
        ),
        sa.UniqueConstraint(
            "request_id", name="uq_requisition_returns_request_id"
        ),
    )
    op.create_index(
        "ix_requisition_returns_requisition_id",
        "requisition_returns",
        ["requisition_id"],
    )

    op.create_table(
        "requisition_scraps",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_no", sa.String(length=40), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("requisition_id", sa.Integer(), nullable=False),
        sa.Column("consumable_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("stock_before", sa.Integer(), nullable=False),
        sa.Column("stock_after", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("remark", sa.String(length=255), nullable=True),
        sa.Column("operator_id", sa.Integer(), nullable=False),
        sa.Column("operator_name", sa.String(length=64), nullable=False),
        sa.Column("operator_role", sa.String(length=16), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("business_at", sa.DATETIME(), nullable=False),
        sa.Column("posted_at", sa.DATETIME(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.ForeignKeyConstraint(["consumable_id"], ["consumables.id"]),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["requisition_id"], ["requisitions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "order_no", name="uq_requisition_scraps_order_no"
        ),
        sa.UniqueConstraint(
            "request_id", name="uq_requisition_scraps_request_id"
        ),
    )
    op.create_index(
        "ix_requisition_scraps_requisition_id",
        "requisition_scraps",
        ["requisition_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_requisition_scraps_requisition_id", table_name="requisition_scraps"
    )
    op.drop_table("requisition_scraps")
    op.drop_index(
        "ix_requisition_returns_requisition_id", table_name="requisition_returns"
    )
    op.drop_table("requisition_returns")

    with op.batch_alter_table("inventory_events") as batch_op:
        batch_op.drop_constraint("ck_inventory_events_delta", type_="check")
        batch_op.create_check_constraint(
            "ck_inventory_events_delta", "delta <> 0"
        )