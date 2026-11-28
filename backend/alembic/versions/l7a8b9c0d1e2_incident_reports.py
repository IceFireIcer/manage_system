"""incident reports

Revision ID: l7a8b9c0d1e2
Revises: k6f7a8b9c0d1
Create Date: 2026-11-28 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "l7a8b9c0d1e2"
down_revision: str | Sequence[str] | None = "k6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "incident_reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("consumable_id", sa.Integer(), nullable=False),
        sa.Column("reporter_id", sa.Integer(), nullable=False),
        sa.Column("reporter_name", sa.String(length=64), nullable=False),
        sa.Column("reporter_role", sa.String(length=16), nullable=False),
        sa.Column("location", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("photo_path", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("handler_id", sa.Integer(), nullable=True),
        sa.Column("handler_name", sa.String(length=64), nullable=True),
        sa.Column("handled_at", sa.DATETIME(), nullable=True),
        sa.Column("reject_reason", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.CheckConstraint(
            "status IN ('open','confirmed','rejected')",
            name="ck_incident_reports_status",
        ),
        sa.ForeignKeyConstraint(["consumable_id"], ["consumables.id"]),
        sa.ForeignKeyConstraint(["handler_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["reporter_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_incident_reports_consumable_id",
        "incident_reports",
        ["consumable_id"],
    )
    op.create_index(
        "ix_incident_reports_reporter_id",
        "incident_reports",
        ["reporter_id"],
    )
    op.create_index(
        "ix_incident_reports_class_status",
        "incident_reports",
        ["class_id", "status"],
    )
    op.create_index(
        "ix_incident_reports_reporter_created",
        "incident_reports",
        ["reporter_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_incident_reports_reporter_created",
        table_name="incident_reports",
    )
    op.drop_index(
        "ix_incident_reports_class_status", table_name="incident_reports"
    )
    op.drop_index(
        "ix_incident_reports_reporter_id", table_name="incident_reports"
    )
    op.drop_index(
        "ix_incident_reports_consumable_id", table_name="incident_reports"
    )
    op.drop_table("incident_reports")
