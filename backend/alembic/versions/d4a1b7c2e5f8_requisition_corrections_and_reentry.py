"""requisition corrections, reversal and reentry links

Revision ID: d4a1b7c2e5f8
Revises: c8d2e4f6a9b1
Create Date: 2026-11-28 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4a1b7c2e5f8"
down_revision: str | None = "c8d2e4f6a9b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("requisitions") as batch_op:
        batch_op.add_column(sa.Column("order_no", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("reentry_of", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("handler_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("handler_name", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("handler_role", sa.String(length=16), nullable=True))
        batch_op.create_unique_constraint(
            "uq_requisitions_order_no", ["order_no"]
        )
        batch_op.create_index("ix_requisitions_reentry_of", ["reentry_of"])
        batch_op.create_foreign_key(
            "fk_requisitions_reentry_of",
            "requisitions",
            ["reentry_of"],
            ["id"],
        )
        batch_op.create_foreign_key(
            "fk_requisitions_handler_id", "users", ["handler_id"], ["id"]
        )

    op.create_table(
        "requisition_corrections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("requisition_id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("remark", sa.String(length=255), nullable=True),
        sa.Column("applicant_id", sa.Integer(), nullable=False),
        sa.Column("applicant_name", sa.String(length=64), nullable=False),
        sa.Column("applicant_role", sa.String(length=16), nullable=False),
        sa.Column("handler_id", sa.Integer(), nullable=True),
        sa.Column("handler_name", sa.String(length=64), nullable=True),
        sa.Column("handler_role", sa.String(length=16), nullable=True),
        sa.Column("handling_note", sa.Text(), nullable=True),
        sa.Column("reversal_id", sa.Integer(), nullable=True),
        sa.Column("reentry_id", sa.Integer(), nullable=True),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("handled_at", sa.DATETIME(), nullable=True),
        sa.CheckConstraint(
            "status IN ('submitted','accepted','rejected')",
            name="ck_requisition_corrections_status",
        ),
        sa.ForeignKeyConstraint(["applicant_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["handler_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["requisition_id"], ["requisitions.id"]),
        sa.ForeignKeyConstraint(["reversal_id"], ["requisitions.id"]),
        sa.ForeignKeyConstraint(["reentry_id"], ["requisitions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_id", name="uq_requisition_corrections_request_id"
        ),
    )
    op.create_index(
        "ix_requisition_corrections_requisition_id",
        "requisition_corrections",
        ["requisition_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_requisition_corrections_requisition_id",
        table_name="requisition_corrections",
    )
    op.drop_table("requisition_corrections")
    with op.batch_alter_table("requisitions") as batch_op:
        batch_op.drop_constraint("fk_requisitions_handler_id", type_="foreignkey")
        batch_op.drop_constraint("fk_requisitions_reentry_of", type_="foreignkey")
        batch_op.drop_index("ix_requisitions_reentry_of")
        batch_op.drop_constraint("uq_requisitions_order_no", type_="unique")
        batch_op.drop_column("handler_role")
        batch_op.drop_column("handler_name")
        batch_op.drop_column("handler_id")
        batch_op.drop_column("reentry_of")
        batch_op.drop_column("order_no")
