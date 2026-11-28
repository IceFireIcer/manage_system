"""historical name, spec and unit snapshots

Revision ID: g2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-11-28 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "g2b3c4d5e6f7"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("requisitions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "name_snapshot",
                sa.String(length=128),
                nullable=False,
                server_default="",
            )
        )
        batch_op.add_column(
            sa.Column(
                "spec_snapshot",
                sa.String(length=128),
                nullable=False,
                server_default="",
            )
        )
        batch_op.add_column(
            sa.Column(
                "unit_snapshot",
                sa.String(length=16),
                nullable=False,
                server_default="",
            )
        )

    with op.batch_alter_table("inventory_events") as batch_op:
        batch_op.add_column(
            sa.Column(
                "name_snapshot",
                sa.String(length=128),
                nullable=False,
                server_default="",
            )
        )
        batch_op.add_column(
            sa.Column(
                "spec_snapshot",
                sa.String(length=128),
                nullable=False,
                server_default="",
            )
        )

    op.execute(
        "UPDATE requisitions SET name_snapshot = COALESCE("
        "(SELECT c.name FROM consumables c "
        "WHERE c.id = requisitions.consumable_id), name_snapshot, '') "
        "WHERE name_snapshot = ''"
    )
    op.execute(
        "UPDATE requisitions SET spec_snapshot = COALESCE("
        "(SELECT c.spec FROM consumables c "
        "WHERE c.id = requisitions.consumable_id), spec_snapshot, '') "
        "WHERE spec_snapshot = ''"
    )
    op.execute(
        "UPDATE inventory_events SET name_snapshot = COALESCE("
        "(SELECT c.name FROM consumables c "
        "WHERE c.id = inventory_events.consumable_id), name_snapshot, '') "
        "WHERE name_snapshot = ''"
    )
    op.execute(
        "UPDATE inventory_events SET spec_snapshot = COALESCE("
        "(SELECT c.spec FROM consumables c "
        "WHERE c.id = inventory_events.consumable_id), spec_snapshot, '') "
        "WHERE spec_snapshot = ''"
    )


def downgrade() -> None:
    with op.batch_alter_table("inventory_events") as batch_op:
        batch_op.drop_column("spec_snapshot")
        batch_op.drop_column("name_snapshot")

    with op.batch_alter_table("requisitions") as batch_op:
        batch_op.drop_column("unit_snapshot")
        batch_op.drop_column("spec_snapshot")
        batch_op.drop_column("name_snapshot")
