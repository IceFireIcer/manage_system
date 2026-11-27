import os
import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


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
