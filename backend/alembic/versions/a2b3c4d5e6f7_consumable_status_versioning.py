"""consumable status versioning and disabled reason

Revision ID: a2b3c4d5e6f7
Revises: b3d4e5f6a7b8
Create Date: 2026-11-28 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2b3c4d5e6f7"
down_revision: str | None = "b3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "consumables",
        sa.Column("disabled_reason", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "consumables",
        sa.Column(
            "version", sa.Integer(), nullable=False, server_default="1"
        ),
    )


def downgrade() -> None:
    op.drop_column("consumables", "version")
    op.drop_column("consumables", "disabled_reason")