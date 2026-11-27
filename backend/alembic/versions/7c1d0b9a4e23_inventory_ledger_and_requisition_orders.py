"""inventory ledger and requisition orders

Revision ID: 7c1d0b9a4e23
Revises: 631bec3432b3
Create Date: 2026-11-28 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c1d0b9a4e23"
down_revision: str | None = "631bec3432b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENT_TYPES = (
    "'opening','requisition','reversal','restock','return','scrap',"
    "'inventory_adjustment','restock_correction'"
)


def upgrade() -> None:
    op.create_table(
        "requisition_orders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_no", sa.String(length=40), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("owner_name", sa.String(length=64), nullable=False),
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
            name="ck_requisition_orders_status",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_no", name="uq_requisition_orders_order_no"),
        sa.UniqueConstraint("request_id", name="uq_requisition_orders_request_id"),
    )
    op.create_table(
        "inventory_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("consumable_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=24), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("stock_before", sa.Integer(), nullable=False),
        sa.Column("stock_after", sa.Integer(), nullable=False),
        sa.Column("exec_seq", sa.Integer(), nullable=False),
        sa.Column("business_at", sa.DATETIME(), nullable=False),
        sa.Column("posted_at", sa.DATETIME(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("requisition_id", sa.Integer(), nullable=True),
        sa.Column("line_no", sa.Integer(), nullable=True),
        sa.Column("operator_id", sa.Integer(), nullable=True),
        sa.Column("operator_name", sa.String(length=64), nullable=True),
        sa.Column("operator_role", sa.String(length=16), nullable=True),
        sa.Column("unit_snapshot", sa.String(length=16), nullable=False),
        sa.Column("evidence_status", sa.String(length=16), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.CheckConstraint(
            "delta <> 0", name="ck_inventory_events_delta"
        ),
        sa.CheckConstraint(
            "stock_after = stock_before + delta", name="ck_inventory_events_balance"
        ),
        sa.CheckConstraint("stock_before >= 0", name="ck_inventory_events_before"),
        sa.CheckConstraint("stock_after >= 0", name="ck_inventory_events_after"),
        sa.CheckConstraint(
            f"event_type IN ({EVENT_TYPES})", name="ck_inventory_events_type"
        ),
        sa.CheckConstraint(
            "evidence_status IN ('trusted','insufficient','unverifiable')",
            name="ck_inventory_events_evidence",
        ),
        sa.ForeignKeyConstraint(["consumable_id"], ["consumables.id"]),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["requisition_id"], ["requisitions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "consumable_id", "exec_seq", name="uq_inventory_events_consumable_seq"
        ),
    )
    op.create_index(
        "ix_inventory_events_consumable_id", "inventory_events", ["consumable_id"]
    )
    op.create_index(
        "ix_inventory_events_requisition_id", "inventory_events", ["requisition_id"]
    )
    op.create_index(
        "ix_inventory_events_source_id", "inventory_events", ["source_id"]
    )

    connection = op.get_bind()
    consumables = connection.execute(
        sa.text(
            "SELECT id, current_stock, unit, class_id, created_at "
            "FROM consumables WHERE current_stock > 0"
        )
    ).mappings()
    opening_rows = [
        {
            "consumable_id": row["id"],
            "current_stock": row["current_stock"],
            "created_at": row["created_at"],
            "unit": row["unit"],
            "class_id": row["class_id"],
        }
        for row in consumables
    ]
    if opening_rows:
        connection.execute(
            sa.text(
                "INSERT INTO inventory_events ("
                "consumable_id, event_type, delta, stock_before, stock_after, exec_seq, "
                "business_at, posted_at, source_type, source_id, requisition_id, line_no, "
                "operator_id, operator_name, operator_role, unit_snapshot, "
                "evidence_status, class_id, created_at"
                ") VALUES ("
                ":consumable_id, 'opening', :current_stock, 0, :current_stock, 1, "
                ":created_at, :created_at, 'opening', NULL, NULL, NULL, NULL, NULL, NULL, "
                ":unit, 'insufficient', :class_id, :created_at"
                ")"
            ),
            opening_rows,
        )


def downgrade() -> None:
    op.drop_index("ix_inventory_events_source_id", table_name="inventory_events")
    op.drop_index("ix_inventory_events_requisition_id", table_name="inventory_events")
    op.drop_index("ix_inventory_events_consumable_id", table_name="inventory_events")
    op.drop_table("inventory_events")
    op.drop_table("requisition_orders")
