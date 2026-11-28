import os
import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _run_alembic(env: dict[str, str], *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert result.returncode == 0, f"alembic failed:\n{result.stdout}\n{result.stderr}"


def test_alembic_upgrade_creates_four_tables_without_seed_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "alembic_check.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path.as_posix()}"}

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert result.returncode == 0, f"alembic failed:\n{result.stdout}\n{result.stderr}"

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        consumables = conn.execute("SELECT COUNT(*) FROM consumables").fetchone()[0]
    finally:
        conn.close()

    assert {"users", "consumables", "requisitions", "operation_logs"} <= tables
    assert users == 0
    assert consumables == 0

def test_consumable_code_migration_backfills_and_cleans_duplicates(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "consumable_code_migration.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path.as_posix()}"}

    _run_alembic(env, "upgrade", "c8d2e4f6a9b1")
    conn = sqlite3.connect(db_path)
    try:
        rows = [
            ("焊丝", "φ1.2mm", 1),
            ("焊丝", "φ1.2mm", 1),
            ("焊丝", "φ1.6mm", 1),
            ("焊丝", "φ1.2mm", 2),
        ]
        for name, spec, class_id in rows:
            conn.execute(
                "INSERT INTO consumables ("
                "name, spec, unit, location, safety_stock, current_stock, "
                "class_id, status, created_at, updated_at"
                ") VALUES (?, ?, '卷', '一号柜', 5, 7, ?, 'enabled', "
                "'2026-11-01 08:00:00', '2026-11-01 08:00:00')",
                (name, spec, class_id),
            )
        conn.commit()
    finally:
        conn.close()

    _run_alembic(env, "upgrade", "head")

    conn = sqlite3.connect(db_path)
    try:
        migrated = conn.execute(
            "SELECT id, class_id, name, spec, code FROM consumables ORDER BY id"
        ).fetchall()
        logs = conn.execute(
            "SELECT action, target_id FROM operation_logs ORDER BY id"
        ).fetchall()
        batch_table = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='consumable_import_batches'"
        ).fetchone()

        duplicate_blocked = False
        try:
            conn.execute(
                "INSERT INTO consumables ("
                "name, spec, unit, location, safety_stock, current_stock, "
                "class_id, status, code, created_at, updated_at"
                ") VALUES ('焊丝', 'φ1.2mm', '卷', '一号柜', 0, 0, 1, "
                "'enabled', 'DUP', '2026-11-01 08:00:00', "
                "'2026-11-01 08:00:00')"
            )
            conn.commit()
        except sqlite3.IntegrityError:
            duplicate_blocked = True
        finally:
            conn.rollback()

        code_blocked = False
        try:
            conn.execute(
                "INSERT INTO consumables ("
                "name, spec, unit, location, safety_stock, current_stock, "
                "class_id, status, code, created_at, updated_at"
                ") VALUES ('砂纸', '240目', '张', '二号柜', 0, 0, 1, "
                "'enabled', 'C-00000001', '2026-11-01 08:00:00', "
                "'2026-11-01 08:00:00')"
            )
            conn.commit()
        except sqlite3.IntegrityError:
            code_blocked = True
        finally:
            conn.rollback()
    finally:
        conn.close()

    assert [row[0] for row in migrated] == [1, 2, 3, 4]
    assert migrated[0] == (1, 1, "焊丝", "φ1.2mm", "C-00000001")
    assert migrated[1] == (2, 1, "焊丝（重复档案 2）", "φ1.2mm", "C-00000002")
    assert migrated[2] == (3, 1, "焊丝", "φ1.6mm", "C-00000003")
    assert migrated[3] == (4, 2, "焊丝", "φ1.2mm", "C-00000004")
    assert logs == [("migration_duplicate_rename", 2)]
    assert batch_table == ("consumable_import_batches",)
    assert duplicate_blocked is True
    assert code_blocked is True
