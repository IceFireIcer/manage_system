"""permission catalog, role matrix and class overrides

Revision ID: t6c7d8e9f0a1
Revises: s5b6c7d8e9f0
Create Date: 2026-11-28 18:00:00.000000

种子数据直接复用 app.permissions.CATALOG，保证代码目录与数据库同步。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.permissions import permission_rows_for_seed, role_rows_for_seed

revision: str = "t6c7d8e9f0a1"
down_revision: str | Sequence[str] | None = "s5b6c7d8e9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "permissions",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False, server_default="class"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "scope IN ('system','class','personal')", name="ck_permissions_scope"
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index(
        "ix_permissions_category", "permissions", ["category", "sort_order"]
    )

    op.create_table(
        "role_permissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("permission_key", sa.String(length=64), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "default_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "toggleable_by_teacher",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "updated_at",
            sa.DATETIME(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["permission_key"], ["permissions.key"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "role", "permission_key", name="uq_role_permissions_role_key"
        ),
    )
    op.create_index("ix_role_permissions_role", "role_permissions", ["role"])

    op.create_table(
        "class_role_permissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("permission_key", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DATETIME(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["permission_key"], ["permissions.key"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "class_id",
            "role",
            "permission_key",
            name="uq_class_role_permissions_class_role_key",
        ),
    )
    op.create_index(
        "ix_class_role_permissions_class_id", "class_role_permissions", ["class_id"]
    )
    op.create_index(
        "ix_class_role_permissions_class_role",
        "class_role_permissions",
        ["class_id", "role"],
    )

    permissions_table = sa.table(
        "permissions",
        sa.column("key", sa.String),
        sa.column("label", sa.String),
        sa.column("category", sa.String),
        sa.column("scope", sa.String),
        sa.column("sort_order", sa.Integer),
    )
    op.bulk_insert(permissions_table, permission_rows_for_seed())

    role_permissions_table = sa.table(
        "role_permissions",
        sa.column("role", sa.String),
        sa.column("permission_key", sa.String),
        sa.column("allowed", sa.Boolean),
        sa.column("default_enabled", sa.Boolean),
        sa.column("toggleable_by_teacher", sa.Boolean),
        sa.column("locked", sa.Boolean),
    )
    op.bulk_insert(role_permissions_table, role_rows_for_seed())


def downgrade() -> None:
    op.drop_index(
        "ix_class_role_permissions_class_role", table_name="class_role_permissions"
    )
    op.drop_index(
        "ix_class_role_permissions_class_id", table_name="class_role_permissions"
    )
    op.drop_table("class_role_permissions")
    op.drop_index("ix_role_permissions_role", table_name="role_permissions")
    op.drop_table("role_permissions")
    op.drop_index("ix_permissions_category", table_name="permissions")
    op.drop_table("permissions")
