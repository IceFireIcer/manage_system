"""operation log audit snapshot result and search indexes

Revision ID: i4d5e6f7a8b9
Revises: h3c4d5e6f7a8
Create Date: 2026-11-28 23:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "i4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = "h3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("operation_logs") as batch_op:
        batch_op.add_column(sa.Column("operator_name", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("operator_account", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("result", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("target_label", sa.String(length=160), nullable=True))

    op.execute(
        sa.text(
            "UPDATE operation_logs SET result = "
            "CASE WHEN action LIKE '%_rejected' THEN 'rejected' ELSE 'success' END "
            "WHERE result IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE operation_logs SET operator_name = "
            "(SELECT users.name FROM users WHERE users.id = operation_logs.operator_id) "
            "WHERE operator_name IS NULL AND operator_id IS NOT NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE operation_logs SET operator_account = "
            "(SELECT users.account FROM users WHERE users.id = operation_logs.operator_id) "
            "WHERE operator_account IS NULL AND operator_id IS NOT NULL"
        )
    )

    op.create_index(
        "ix_operation_logs_target",
        "operation_logs",
        ["target_type", "target_id"],
    )
    op.create_index(
        "ix_operation_logs_action_created",
        "operation_logs",
        ["action", "created_at"],
    )
    op.create_index("ix_operation_logs_result", "operation_logs", ["result"])
    op.create_index(
        "ix_operation_logs_operator_created",
        "operation_logs",
        ["operator_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_operation_logs_operator_created", table_name="operation_logs")
    op.drop_index("ix_operation_logs_result", table_name="operation_logs")
    op.drop_index("ix_operation_logs_action_created", table_name="operation_logs")
    op.drop_index("ix_operation_logs_target", table_name="operation_logs")

    with op.batch_alter_table("operation_logs") as batch_op:
        batch_op.drop_column("target_label")
        batch_op.drop_column("result")
        batch_op.drop_column("operator_account")
        batch_op.drop_column("operator_name")
