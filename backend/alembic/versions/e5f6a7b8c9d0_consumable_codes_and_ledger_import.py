"""consumable codes, identity constraint and ledger import batches

Revision ID: e5f6a7b8c9d0
Revises: c8d2e4f6a9b1
Create Date: 2026-11-28 15:00:00.000000

"""

import json
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "c8d2e4f6a9b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAME_MAX_LENGTH = 128


def _duplicate_name(
    name: str,
    consumable_id: int,
    *,
    key: tuple[int, str, str],
    used: set[tuple[int, str, str]],
) -> str:
    class_id, _, spec = key
    counter = 1
    while counter <= 1000:
        if counter == 1:
            suffix = f"（重复档案 {consumable_id}）"
        else:
            suffix = f"（重复档案 {consumable_id}-{counter}）"
        candidate = name[: NAME_MAX_LENGTH - len(suffix)] + suffix
        if (class_id, candidate, spec) not in used:
            return candidate
        counter += 1
    raise RuntimeError(f"无法为重复耗材 {consumable_id} 生成唯一名称")


def upgrade() -> None:
    with op.batch_alter_table("consumables") as batch_op:
        batch_op.add_column(sa.Column("code", sa.String(length=32), nullable=True))

    bind = op.get_bind()
    rows = (
        bind.execute(
            sa.text(
                "SELECT id, class_id, name, spec FROM consumables ORDER BY id"
            )
        )
        .mappings()
        .all()
    )

    first_seen: dict[tuple[int, str, str], int] = {}
    used_names = {(row["class_id"], row["name"], row["spec"]) for row in rows}
    for row in rows:
        bind.execute(
            sa.text("UPDATE consumables SET code = :code WHERE id = :id"),
            {"code": f"C-{row['id']:08d}", "id": row["id"]},
        )
        key = (row["class_id"], row["name"], row["spec"])
        if key not in first_seen:
            first_seen[key] = row["id"]
            continue

        retained_id = first_seen[key]
        renamed = _duplicate_name(
            row["name"], row["id"], key=key, used=used_names
        )
        used_names.add((row["class_id"], renamed, row["spec"]))
        timestamp = datetime.now()
        before = json.dumps(
            {"id": row["id"], "name": row["name"], "spec": row["spec"]},
            ensure_ascii=False,
        )
        after = json.dumps(
            {"id": row["id"], "name": renamed, "spec": row["spec"]},
            ensure_ascii=False,
        )
        bind.execute(
            sa.text(
                "UPDATE consumables SET name = :name, updated_at = :updated_at "
                "WHERE id = :id"
            ),
            {"name": renamed, "updated_at": timestamp, "id": row["id"]},
        )
        bind.execute(
            sa.text(
                "INSERT INTO operation_logs ("
                "operator_id, role, action, target_type, target_id, "
                "before_value, after_value, remark, created_at"
                ") VALUES (NULL, NULL, 'migration_duplicate_rename', "
                "'consumable', :id, :before, :after, :remark, :created_at)"
            ),
            {
                "id": row["id"],
                "before": before,
                "after": after,
                "remark": f"保留同班同名同规格档案 {retained_id}",
                "created_at": timestamp,
            },
        )

    with op.batch_alter_table("consumables") as batch_op:
        batch_op.create_unique_constraint(
            "uq_consumables_class_name_spec", ["class_id", "name", "spec"]
        )
        batch_op.create_unique_constraint(
            "uq_consumables_class_code", ["class_id", "code"]
        )

    op.create_table(
        "consumable_import_batches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("signature", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("counted_at", sa.DATETIME(), nullable=False),
        sa.Column("operator_id", sa.Integer(), nullable=False),
        sa.Column("operator_name", sa.String(length=64), nullable=False),
        sa.Column("operator_role", sa.String(length=16), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("created_kinds", sa.Integer(), nullable=False),
        sa.Column("skipped_existing", sa.Integer(), nullable=False),
        sa.Column("opening_total", sa.Integer(), nullable=False),
        sa.Column("by_unit_json", sa.Text(), nullable=False),
        sa.Column("receipt_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_id", name="uq_consumable_import_batches_request_id"
        ),
    )


def downgrade() -> None:
    op.drop_table("consumable_import_batches")
    with op.batch_alter_table("consumables") as batch_op:
        batch_op.drop_constraint("uq_consumables_class_code", type_="unique")
        batch_op.drop_constraint("uq_consumables_class_name_spec", type_="unique")
        batch_op.drop_column("code")
