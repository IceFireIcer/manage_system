"""setup system settings and super admin migration

Revision ID: q3f4a5b6c7d8
Revises: p2e3f4a5b6c7
Create Date: 2026-11-28 12:00:00.000000

"""

import datetime
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "q3f4a5b6c7d8"
down_revision: str | Sequence[str] | None = "p2e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SETUP_COMPLETED_KEY = "setup_completed"
SETUP_INITIALIZED_AT_KEY = "setup_initialized_at"
SETUP_INITIAL_ADMIN_KEY = "setup_initial_admin_account"
LEGACY_ADMIN_ACCOUNT = "admin"
ADMIN_ROLES_SQL = "'admin','super_admin'"


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )

    bind = op.get_bind()

    # 旧库：默认 admin 账号提升为超级管理员，其余 admin 角色保持不变（#56 处理 user_roles）。
    bind.execute(
        sa.text(
            "UPDATE users SET role='super_admin' "
            "WHERE account=:account AND role='admin'"
        ),
        {"account": LEGACY_ADMIN_ACCOUNT},
    )

    admin_row = bind.execute(
        sa.text("SELECT account FROM users WHERE account=:account LIMIT 1"),
        {"account": LEGACY_ADMIN_ACCOUNT},
    ).first()
    if admin_row is None:
        admin_row = bind.execute(
            sa.text(
                "SELECT account FROM users "
                f"WHERE role IN ({ADMIN_ROLES_SQL}) ORDER BY id LIMIT 1"
            )
        ).first()

    teacher_row = bind.execute(
        sa.text(
            "SELECT 1 FROM users WHERE role='teacher' AND status='enabled' LIMIT 1"
        )
    ).first()
    completed = "1" if teacher_row is not None else "0"

    now = datetime.datetime.now()
    settings_table = sa.table(
        "system_settings",
        sa.column("key", sa.String),
        sa.column("value", sa.Text),
        sa.column("updated_by", sa.String),
        sa.column("updated_at", sa.DATETIME),
    )
    rows: list[dict[str, object]] = [
        {
            "key": SETUP_COMPLETED_KEY,
            "value": completed,
            "updated_by": "migration",
            "updated_at": now,
        },
        {
            "key": SETUP_INITIALIZED_AT_KEY,
            "value": now.isoformat(sep=" "),
            "updated_by": "migration",
            "updated_at": now,
        },
    ]
    if admin_row is not None:
        rows.append(
            {
                "key": SETUP_INITIAL_ADMIN_KEY,
                "value": admin_row[0],
                "updated_by": "migration",
                "updated_at": now,
            }
        )
    op.bulk_insert(settings_table, rows)


def downgrade() -> None:
    op.execute("UPDATE users SET role='admin' WHERE role='super_admin'")
    op.drop_table("system_settings")