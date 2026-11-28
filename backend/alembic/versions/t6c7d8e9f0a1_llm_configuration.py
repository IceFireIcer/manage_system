"""llm providers, prompt versions and feature bindings

Revision ID: t6c7d8e9f0a1
Revises: s5b6c7d8e9f0
Create Date: 2026-11-28 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "t6c7d8e9f0a1"
down_revision: str | Sequence[str] | None = "s5b6c7d8e9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_providers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("base_url", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("encrypted_api_key", sa.Text(), nullable=True),
        sa.Column("timeout", sa.Float(), nullable=False, server_default=sa.text("30")),
        sa.Column("temperature", sa.Float(), nullable=False, server_default=sa.text("0.3")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("supports_tools", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.DATETIME(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", sa.DATETIME(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_llm_providers_name"),
    )

    op.create_table(
        "llm_prompts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("feature", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DATETIME(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feature", "version", name="uq_llm_prompts_feature_version"),
    )
    op.create_index("ix_llm_prompts_feature", "llm_prompts", ["feature"])
    op.create_index("ix_llm_prompts_feature_active", "llm_prompts", ["feature", "active"])

    op.create_table(
        "llm_feature_bindings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("feature", sa.String(length=32), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=True),
        sa.Column("prompt_id", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "updated_at", sa.DATETIME(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"], ["llm_providers.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["prompt_id"], ["llm_prompts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feature", name="uq_llm_feature_bindings_feature"),
    )


def downgrade() -> None:
    op.drop_table("llm_feature_bindings")
    op.drop_index("ix_llm_prompts_feature_active", table_name="llm_prompts")
    op.drop_index("ix_llm_prompts_feature", table_name="llm_prompts")
    op.drop_table("llm_prompts")
    op.drop_table("llm_providers")
