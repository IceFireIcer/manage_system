"""align consumable.view and account.manage defaults with migrated routes

Revision ID: w9e0f1a2b3c4
Revises: v8e9f0a1b2c3
Create Date: 2026-11-28 20:00:00.000000

#60 迁移后学生仍需只读耗材目录，班委仍需管理本班学生账号；
旧库的 role_permissions 行沿用 #59 默认值，这里按代码目录补齐。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "w9e0f1a2b3c4"
down_revision: str | Sequence[str] | None = "v8e9f0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _update_row(role: str, permission_key: str, *, toggleable: bool) -> None:
    table = sa.table(
        "role_permissions",
        sa.column("role", sa.String),
        sa.column("permission_key", sa.String),
        sa.column("allowed", sa.Boolean),
        sa.column("default_enabled", sa.Boolean),
        sa.column("toggleable_by_teacher", sa.Boolean),
        sa.column("locked", sa.Boolean),
    )
    op.execute(
        table.update()
        .where(
            sa.and_(
                table.c.role == role,
                table.c.permission_key == permission_key,
            )
        )
        .values(
            allowed=True,
            default_enabled=True,
            toggleable_by_teacher=toggleable,
            locked=False,
        )
    )


def upgrade() -> None:
    _update_row("student", "consumable.view", toggleable=True)
    _update_row("admin", "account.manage", toggleable=True)


def downgrade() -> None:
    table = sa.table(
        "role_permissions",
        sa.column("role", sa.String),
        sa.column("permission_key", sa.String),
        sa.column("allowed", sa.Boolean),
        sa.column("default_enabled", sa.Boolean),
        sa.column("toggleable_by_teacher", sa.Boolean),
        sa.column("locked", sa.Boolean),
    )
    op.execute(
        table.update()
        .where(
            sa.and_(
                table.c.role == "student",
                table.c.permission_key == "consumable.view",
            )
        )
        .values(
            allowed=False,
            default_enabled=False,
            toggleable_by_teacher=False,
            locked=False,
        )
    )
    op.execute(
        table.update()
        .where(
            sa.and_(
                table.c.role == "admin",
                table.c.permission_key == "account.manage",
            )
        )
        .values(
            allowed=False,
            default_enabled=False,
            toggleable_by_teacher=False,
            locked=False,
        )
    )