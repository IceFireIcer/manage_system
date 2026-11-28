"""classes, class terms and memberships

Revision ID: q3f4a5b6c7d8
Revises: p2e3f4a5b6c7
Create Date: 2026-11-28 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "q3f4a5b6c7d8"
down_revision: str | Sequence[str] | None = "p2e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_CLASS_ID = 1
DEFAULT_CLASS_NAME = "默认班级"
DEFAULT_TERM_NAME = "默认学期"


def upgrade() -> None:
    op.create_table(
        "classes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="enabled"),
        sa.Column("created_at", sa.DATETIME(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DATETIME(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("status IN ('enabled','disabled')", name="ck_classes_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_classes_name"),
    )

    op.create_table(
        "class_terms",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DATETIME(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DATETIME(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint(
            "status IN ('planned','active','archived')", name="ck_class_terms_status"
        ),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("class_id", "name", name="uq_class_terms_class_name"),
    )
    op.create_index("ix_class_terms_class_id", "class_terms", ["class_id"])
    op.create_index(
        "ix_class_terms_class_status", "class_terms", ["class_id", "status"]
    )

    op.create_table(
        "class_memberships",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="teacher"),
        sa.Column("created_at", sa.DATETIME(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "class_id", "user_id", name="uq_class_memberships_class_user"
        ),
    )
    op.create_index("ix_class_memberships_class_id", "class_memberships", ["class_id"])
    op.create_index("ix_class_memberships_user_id", "class_memberships", ["user_id"])
    op.create_index("ix_class_memberships_user", "class_memberships", ["user_id"])

    # 旧数据回填：class_id=1 变为默认班级，现有教师分配为默认班级教师。
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO classes (id, name, code, status, created_at, updated_at) "
            "SELECT :cid, :name, '', 'enabled', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
            "WHERE NOT EXISTS (SELECT 1 FROM classes WHERE id = :cid)"
        ),
        {"cid": DEFAULT_CLASS_ID, "name": DEFAULT_CLASS_NAME},
    )
    bind.execute(
        sa.text(
            "INSERT INTO class_terms (class_id, name, status, created_at, updated_at) "
            "SELECT :cid, :name, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM class_terms WHERE class_id = :cid AND name = :name)"
        ),
        {"cid": DEFAULT_CLASS_ID, "name": DEFAULT_TERM_NAME},
    )
    bind.execute(
        sa.text(
            "INSERT INTO class_memberships (class_id, user_id, role, created_at) "
            "SELECT :cid, id, 'teacher', CURRENT_TIMESTAMP FROM users "
            "WHERE role = 'teacher' AND NOT EXISTS ("
            "SELECT 1 FROM class_memberships m "
            "WHERE m.class_id = :cid AND m.user_id = users.id)"
        ),
        {"cid": DEFAULT_CLASS_ID},
    )
    bind.execute(
        sa.text("UPDATE users SET class_id = :cid WHERE class_id IS NULL OR class_id = 0"),
        {"cid": DEFAULT_CLASS_ID},
    )


def downgrade() -> None:
    op.drop_index("ix_class_memberships_user", table_name="class_memberships")
    op.drop_index("ix_class_memberships_user_id", table_name="class_memberships")
    op.drop_index("ix_class_memberships_class_id", table_name="class_memberships")
    op.drop_table("class_memberships")
    op.drop_index("ix_class_terms_class_status", table_name="class_terms")
    op.drop_index("ix_class_terms_class_id", table_name="class_terms")
    op.drop_table("class_terms")
    op.drop_table("classes")