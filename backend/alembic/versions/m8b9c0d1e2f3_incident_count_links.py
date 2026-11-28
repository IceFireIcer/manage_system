"""incident conversion and inventory count back links

Revision ID: m8b9c0d1e2f3
Revises: l7a8b9c0d1e2
Create Date: 2026-11-28 21:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "m8b9c0d1e2f3"
down_revision: str | Sequence[str] | None = "l7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("incident_reports") as batch_op:
        batch_op.add_column(
            sa.Column("linked_count_id", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("count_order_no", sa.String(length=40), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_incident_reports_linked_count_id",
            "inventory_counts",
            ["linked_count_id"],
            ["id"],
        )
        batch_op.drop_constraint(
            "ck_incident_reports_status", type_="check"
        )
        batch_op.create_check_constraint(
            "ck_incident_reports_status",
            "status IN ('open','confirmed','rejected','converted_to_count')",
        )

    with op.batch_alter_table("inventory_counts") as batch_op:
        batch_op.add_column(
            sa.Column("incident_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_inventory_counts_incident_id",
            "incident_reports",
            ["incident_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_inventory_counts_incident_id", ["incident_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("inventory_counts") as batch_op:
        batch_op.drop_index("ix_inventory_counts_incident_id")
        batch_op.drop_constraint(
            "fk_inventory_counts_incident_id", type_="foreignkey"
        )
        batch_op.drop_column("incident_id")

    with op.batch_alter_table("incident_reports") as batch_op:
        batch_op.drop_constraint(
            "ck_incident_reports_status", type_="check"
        )
        batch_op.create_check_constraint(
            "ck_incident_reports_status",
            "status IN ('open','confirmed','rejected')",
        )
        batch_op.drop_constraint(
            "fk_incident_reports_linked_count_id", type_="foreignkey"
        )
        batch_op.drop_column("count_order_no")
        batch_op.drop_column("linked_count_id")
