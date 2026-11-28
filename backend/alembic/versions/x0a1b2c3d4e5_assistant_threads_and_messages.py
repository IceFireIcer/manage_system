"""assistant threads and messages

Revision ID: x0a1b2c3d4e5
Revises: w9e0f1a2b3c4
Create Date: 2026-11-28 16:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "x0a1b2c3d4e5"
down_revision: str | Sequence[str] | None = "w9e0f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assistant_threads",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.String(length=128), nullable=False, server_default="新对话"),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("0")),
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
        sa.Column(
            "last_message_at",
            sa.DATETIME(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assistant_threads_user_id", "assistant_threads", ["user_id"]
    )
    op.create_index(
        "ix_assistant_threads_user_class",
        "assistant_threads",
        ["user_id", "class_id", "archived"],
    )
    op.create_index(
        "ix_assistant_threads_last_message",
        "assistant_threads",
        ["user_id", "class_id", "last_message_at"],
    )

    op.create_table(
        "assistant_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("context_json", sa.Text(), nullable=True),
        sa.Column("tool_calls_json", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DATETIME(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["assistant_threads.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assistant_messages_thread_id", "assistant_messages", ["thread_id"]
    )
    op.create_index(
        "ix_assistant_messages_thread", "assistant_messages", ["thread_id", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_messages_thread", table_name="assistant_messages")
    op.drop_index("ix_assistant_messages_thread_id", table_name="assistant_messages")
    op.drop_table("assistant_messages")
    op.drop_index("ix_assistant_threads_last_message", table_name="assistant_threads")
    op.drop_index("ix_assistant_threads_user_class", table_name="assistant_threads")
    op.drop_index("ix_assistant_threads_user_id", table_name="assistant_threads")
    op.drop_table("assistant_threads")