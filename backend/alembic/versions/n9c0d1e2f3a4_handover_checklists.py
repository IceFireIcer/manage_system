"""handover checklists

Revision ID: n9c0d1e2f3a4
Revises: m8b9c0d1e2f3
Create Date: 2026-11-28 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "n9c0d1e2f3a4"
down_revision: str | Sequence[str] | None = "m8b9c0d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "handover_checklists",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("outgoing_id", sa.Integer(), nullable=False),
        sa.Column("outgoing_name", sa.String(length=64), nullable=False),
        sa.Column("outgoing_account", sa.String(length=64), nullable=False),
        sa.Column("incoming_id", sa.Integer(), nullable=True),
        sa.Column("incoming_name", sa.String(length=64), nullable=True),
        sa.Column("incoming_account", sa.String(length=64), nullable=True),
        sa.Column("snapshot_at", sa.DATETIME(), nullable=False),
        sa.Column("confirmed_at", sa.DATETIME(), nullable=True),
        sa.Column("confirmed_by_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_by_name", sa.String(length=64), nullable=True),
        sa.Column("confirmed_by_account", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','confirmed')",
            name="ck_handover_checklists_status",
        ),
        sa.ForeignKeyConstraint(["confirmed_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["incoming_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["outgoing_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "class_id", "version", name="uq_handover_checklists_class_version"
        ),
    )
    op.create_index(
        "ix_handover_checklists_class_created",
        "handover_checklists",
        ["class_id", "created_at"],
    )
    op.create_table(
        "handover_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("checklist_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("ref_type", sa.String(length=32), nullable=False),
        sa.Column("ref_id", sa.Integer(), nullable=True),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("snapshot_payload", sa.Text(), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("confirmed_by_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_by_name", sa.String(length=64), nullable=True),
        sa.Column("confirmed_at", sa.DATETIME(), nullable=True),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('stock','restock','requisition','alert','incident',"
            "'suggestion','count')",
            name="ck_handover_items_kind",
        ),
        sa.ForeignKeyConstraint(["checklist_id"], ["handover_checklists.id"]),
        sa.ForeignKeyConstraint(["confirmed_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("checklist_id", "seq", name="uq_handover_items_seq"),
    )
    op.create_index(
        "ix_handover_items_checklist_id", "handover_items", ["checklist_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_handover_items_checklist_id", table_name="handover_items")
    op.drop_table("handover_items")
    op.drop_index(
        "ix_handover_checklists_class_created", table_name="handover_checklists"
    )
    op.drop_table("handover_checklists")
