"""user profiles with base64 avatar

Revision ID: v8e9f0a1b2c3
Revises: u7d8e9f0a1b2
Create Date: 2026-11-28 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "v8e9f0a1b2c3"
down_revision: str | Sequence[str] | None = "u7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_profiles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("employee_no", sa.String(length=64), nullable=True),
        sa.Column("student_no", sa.String(length=64), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("email", sa.String(length=128), nullable=True),
        sa.Column("wechat", sa.String(length=64), nullable=True),
        sa.Column("qq", sa.String(length=32), nullable=True),
        sa.Column("github", sa.String(length=64), nullable=True),
        sa.Column(
            "avatar_base64",
            sa.Text().with_variant(mysql.LONGTEXT(), "mysql"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DATETIME(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DATETIME(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_profiles_user"),
    )
    op.create_index(
        "ix_user_profiles_user_id", "user_profiles", ["user_id"]
    )

    users = sa.table("users", sa.column("id", sa.Integer))
    profiles = sa.table(
        "user_profiles",
        sa.column("user_id", sa.Integer),
        sa.column("employee_no", sa.String),
        sa.column("student_no", sa.String),
        sa.column("phone", sa.String),
        sa.column("email", sa.String),
        sa.column("wechat", sa.String),
        sa.column("qq", sa.String),
        sa.column("github", sa.String),
    )
    bind = op.get_bind()
    user_ids = bind.execute(sa.select(users.c.id)).scalars().all()
    if user_ids:
        bind.execute(
            profiles.insert(),
            [
                {
                    "user_id": user_id,
                    "employee_no": "",
                    "student_no": "",
                    "phone": "",
                    "email": "",
                    "wechat": "",
                    "qq": "",
                    "github": "",
                }
                for user_id in user_ids
            ],
        )


def downgrade() -> None:
    op.drop_index("ix_user_profiles_user_id", table_name="user_profiles")
    op.drop_table("user_profiles")