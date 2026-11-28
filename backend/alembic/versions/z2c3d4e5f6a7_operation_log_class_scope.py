"""operation log class scope

Revision ID: z2c3d4e5f6a7
Revises: y1b2c3d4e5f6
Create Date: 2026-11-28 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "z2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "y1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("operation_logs") as batch_op:
        batch_op.add_column(sa.Column("class_id", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE operation_logs SET class_id = ("
        "SELECT users.class_id FROM users WHERE users.id = operation_logs.operator_id"
        ") WHERE class_id IS NULL AND operator_id IS NOT NULL"
    )
    op.create_index(
        "ix_operation_logs_class_created",
        "operation_logs",
        ["class_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_operation_logs_class_created", table_name="operation_logs")
    with op.batch_alter_table("operation_logs") as batch_op:
        batch_op.drop_column("class_id")