"""assistant tool actions

Revision ID: y1b2c3d4e5f6
Revises: x0a1b2c3d4e5
Create Date: 2026-11-28 18:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "y1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "x0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assistant_tool_actions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column(
            "kind", sa.String(length=16), nullable=False, server_default="write"
        ),
        sa.Column("args_json", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("confirmation_token", sa.String(length=64), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DATETIME(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("confirmed_at", sa.DATETIME(), nullable=True),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["assistant_threads.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "confirmation_token", name="uq_assistant_tool_actions_token"
        ),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_assistant_tool_actions_thread_id",
        "assistant_tool_actions",
        ["thread_id"],
    )
    op.create_index(
        "ix_assistant_tool_actions_user_id",
        "assistant_tool_actions",
        ["user_id"],
    )
    op.create_index(
        "ix_assistant_tool_actions_user_class",
        "assistant_tool_actions",
        ["user_id", "class_id", "created_at"],
    )
    op.create_index(
        "ix_assistant_tool_actions_thread",
        "assistant_tool_actions",
        ["thread_id", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_assistant_tool_actions_thread", table_name="assistant_tool_actions"
    )
    op.drop_index(
        "ix_assistant_tool_actions_user_class", table_name="assistant_tool_actions"
    )
    op.drop_index(
        "ix_assistant_tool_actions_user_id", table_name="assistant_tool_actions"
    )
    op.drop_index(
        "ix_assistant_tool_actions_thread_id", table_name="assistant_tool_actions"
    )
    op.drop_table("assistant_tool_actions")
