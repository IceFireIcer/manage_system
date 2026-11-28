"""suggestion feedback

Revision ID: m8b9c0d1e2f3
Revises: l7a8b9c0d1e2
Create Date: 2026-11-28 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "m8b9c0d1e2f3"
down_revision: str | Sequence[str] | None = "l7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "suggestion_feedbacks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("consumable_id", sa.Integer(), nullable=False),
        sa.Column("suggestion_key", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("feedback_by", sa.Integer(), nullable=False),
        sa.Column("feedback_name", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.CheckConstraint(
            "reason IN ('substitute','schedule_adjust','low_usage','other')",
            name="ck_suggestion_feedbacks_reason",
        ),
        sa.ForeignKeyConstraint(["consumable_id"], ["consumables.id"]),
        sa.ForeignKeyConstraint(["feedback_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "class_id",
            "consumable_id",
            "suggestion_key",
            "feedback_by",
            name="uq_suggestion_feedbacks_member",
        ),
    )
    op.create_index(
        "ix_suggestion_feedbacks_consumable_id",
        "suggestion_feedbacks",
        ["consumable_id"],
    )
    op.create_index(
        "ix_suggestion_feedbacks_class_consumable_created",
        "suggestion_feedbacks",
        ["class_id", "consumable_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_suggestion_feedbacks_class_consumable_created",
        table_name="suggestion_feedbacks",
    )
    op.drop_index(
        "ix_suggestion_feedbacks_consumable_id",
        table_name="suggestion_feedbacks",
    )
    op.drop_table("suggestion_feedbacks")
