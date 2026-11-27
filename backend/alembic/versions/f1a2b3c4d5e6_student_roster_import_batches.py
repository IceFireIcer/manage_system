"""student roster import batches

Revision ID: f1a2b3c4d5e6
Revises: e5f6a7b8c9d0
Create Date: 2026-11-28 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "student_import_batches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("signature", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("on_existing", sa.String(length=16), nullable=False),
        sa.Column("operator_id", sa.Integer(), nullable=False),
        sa.Column("operator_name", sa.String(length=64), nullable=False),
        sa.Column("operator_role", sa.String(length=16), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("created_rows", sa.Integer(), nullable=False),
        sa.Column("existing_rows", sa.Integer(), nullable=False),
        sa.Column("updated_rows", sa.Integer(), nullable=False),
        sa.Column("error_rows", sa.Integer(), nullable=False),
        sa.Column("receipt_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_id", name="uq_student_import_batches_request_id"
        ),
    )


def downgrade() -> None:
    op.drop_table("student_import_batches")
