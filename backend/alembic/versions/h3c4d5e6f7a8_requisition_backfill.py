"""requisition backfill provenance and controlled adjustments

Revision ID: h3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-11-28 23:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "h3c4d5e6f7a8"
down_revision: str | Sequence[str] | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("requisition_orders") as batch_op:
        batch_op.add_column(
            sa.Column("backfill_reason", sa.Text(), nullable=True)
        )

    with op.batch_alter_table("requisitions") as batch_op:
        batch_op.add_column(sa.Column("operator_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("operator_name", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("operator_role", sa.String(length=16), nullable=True)
        )
        batch_op.add_column(sa.Column("posted_at", sa.DATETIME(), nullable=True))
        batch_op.add_column(sa.Column("backfill_reason", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("backfill_mode", sa.String(length=16), nullable=True)
        )
        batch_op.add_column(sa.Column("linked_event_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("linked_count_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_requisitions_backfill_operator_id",
            "users",
            ["operator_id"],
            ["id"],
        )
        batch_op.create_foreign_key(
            "fk_requisitions_linked_event_id",
            "inventory_events",
            ["linked_event_id"],
            ["id"],
        )
        batch_op.create_foreign_key(
            "fk_requisitions_linked_count_id",
            "inventory_counts",
            ["linked_count_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_requisitions_linked_count_id", ["linked_count_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("requisitions") as batch_op:
        batch_op.drop_index("ix_requisitions_linked_count_id")
        batch_op.drop_constraint(
            "fk_requisitions_linked_count_id", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_requisitions_linked_event_id", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_requisitions_backfill_operator_id", type_="foreignkey"
        )
        batch_op.drop_column("linked_count_id")
        batch_op.drop_column("linked_event_id")
        batch_op.drop_column("backfill_mode")
        batch_op.drop_column("backfill_reason")
        batch_op.drop_column("posted_at")
        batch_op.drop_column("operator_role")
        batch_op.drop_column("operator_name")
        batch_op.drop_column("operator_id")

    with op.batch_alter_table("requisition_orders") as batch_op:
        batch_op.drop_column("backfill_reason")