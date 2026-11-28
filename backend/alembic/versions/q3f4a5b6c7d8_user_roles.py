"""user roles and multi-role grants

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

LEGACY_ROLE_GRANTS: dict[str, tuple[str, ...]] = {
    "teacher": ("teacher",),
    "student": ("student",),
    "admin": ("admin", "student"),
    "super_admin": ("super_admin",),
}


def upgrade() -> None:
    op.create_table(
        "user_roles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "role", name="uq_user_roles_user_role"),
    )
    op.create_index("ix_user_roles_user_id", "user_roles", ["user_id"])

    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, role FROM users")).fetchall()
    values: list[dict[str, object]] = []
    for user_id, role in rows:
        for granted in LEGACY_ROLE_GRANTS.get(role, (role,)):
            if granted:
                values.append({"user_id": user_id, "role": granted})
    if values:
        connection.execute(
            sa.text(
                "INSERT INTO user_roles (user_id, role, created_at) "
                "VALUES (:user_id, :role, now())"
            ),
            values,
        )


def downgrade() -> None:
    op.drop_index("ix_user_roles_user_id", table_name="user_roles")
    op.drop_table("user_roles")
