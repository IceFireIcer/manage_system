"""courses sessions and lightweight recipes

Revision ID: k6f7a8b9c0d1
Revises: j5e6f7a8b9c0
Create Date: 2026-11-29 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "k6f7a8b9c0d1"
down_revision: str | Sequence[str] | None = "j5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "courses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("class_label", sa.String(length=64), nullable=False),
        sa.Column("semester", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("created_by_name", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.CheckConstraint(
            "status IN ('enabled','disabled')", name="ck_courses_status"
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "class_id", "name", "semester", name="uq_courses_class_name_semester"
        ),
    )
    op.create_index("ix_courses_class_status", "courses", ["class_id", "status"])

    op.create_table(
        "course_recipe_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("created_by_name", sa.String(length=64), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "course_id", "version", name="uq_course_recipe_snapshots_version"
        ),
    )
    op.create_index(
        "ix_course_recipe_snapshots_course_id",
        "course_recipe_snapshots",
        ["course_id"],
    )
    op.create_index(
        "ix_course_recipe_snapshots_course",
        "course_recipe_snapshots",
        ["course_id", "version"],
    )

    op.create_table(
        "course_recipe_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("consumable_id", sa.Integer(), nullable=False),
        sa.Column("consumable_name", sa.String(length=128), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("quantity_per_person", sa.Integer(), nullable=False),
        sa.Column("remark", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.CheckConstraint(
            "quantity_per_person > 0", name="ck_course_recipe_items_quantity"
        ),
        sa.ForeignKeyConstraint(["consumable_id"], ["consumables.id"]),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["course_recipe_snapshots.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id", "consumable_id", name="uq_course_recipe_items_consumable"
        ),
    )
    op.create_index(
        "ix_course_recipe_items_snapshot_id",
        "course_recipe_items",
        ["snapshot_id"],
    )
    op.create_index(
        "ix_course_recipe_items_consumable_id",
        "course_recipe_items",
        ["consumable_id"],
    )

    op.create_table(
        "course_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("headcount", sa.Integer(), nullable=False),
        sa.Column("class_label", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("recipe_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("created_by_name", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.CheckConstraint("headcount >= 0", name="ck_course_sessions_headcount"),
        sa.CheckConstraint(
            "status IN ('planned','completed','cancelled')",
            name="ck_course_sessions_status",
        ),
        sa.ForeignKeyConstraint(
            ["course_id"], ["courses.id"]
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["recipe_snapshot_id"], ["course_recipe_snapshots.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "course_id", "seq", name="uq_course_sessions_course_seq"
        ),
    )
    op.create_index(
        "ix_course_sessions_course_id", "course_sessions", ["course_id"]
    )
    op.create_index(
        "ix_course_sessions_course_date",
        "course_sessions",
        ["course_id", "session_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_course_sessions_course_date", table_name="course_sessions"
    )
    op.drop_index("ix_course_sessions_course_id", table_name="course_sessions")
    op.drop_table("course_sessions")

    op.drop_index(
        "ix_course_recipe_items_consumable_id",
        table_name="course_recipe_items",
    )
    op.drop_index(
        "ix_course_recipe_items_snapshot_id", table_name="course_recipe_items"
    )
    op.drop_table("course_recipe_items")

    op.drop_index(
        "ix_course_recipe_snapshots_course",
        table_name="course_recipe_snapshots",
    )
    op.drop_index(
        "ix_course_recipe_snapshots_course_id",
        table_name="course_recipe_snapshots",
    )
    op.drop_table("course_recipe_snapshots")

    op.drop_index("ix_courses_class_status", table_name="courses")
    op.drop_table("courses")