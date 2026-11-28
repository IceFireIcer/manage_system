"""teaching reviews

Revision ID: p2e3f4a5b6c7
Revises: o1d2e3f4a5b6
Create Date: 2026-11-29 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p2e3f4a5b6c7"
down_revision: str | Sequence[str] | None = "o1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "teaching_reviews",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("actual_net_quantity", sa.Integer(), nullable=False),
        sa.Column("standard_quantity", sa.Integer(), nullable=False),
        sa.Column("deviation_ratio", sa.Float(), nullable=True),
        sa.Column("reason_tags", sa.Text(), nullable=False),
        sa.Column("teacher_note", sa.String(length=255), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("created_by_name", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.CheckConstraint(
            "standard_quantity >= 0", name="ck_teaching_reviews_standard"
        ),
        sa.CheckConstraint(
            "actual_net_quantity >= 0", name="ck_teaching_reviews_actual"
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["course_sessions.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["course_recipe_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "version", name="uq_teaching_reviews_session_version"
        ),
    )
    op.create_index(
        "ix_teaching_reviews_session_id", "teaching_reviews", ["session_id"]
    )
    op.create_index(
        "ix_teaching_reviews_class_session",
        "teaching_reviews",
        ["class_id", "session_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_teaching_reviews_class_session", table_name="teaching_reviews"
    )
    op.drop_index("ix_teaching_reviews_session_id", table_name="teaching_reviews")
    op.drop_table("teaching_reviews")
