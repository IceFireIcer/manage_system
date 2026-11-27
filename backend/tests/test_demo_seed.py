from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
SEED_SCRIPT = BACKEND_DIR / "scripts" / "seed_demo.py"
DEMO_CODES = ("DEMO-GLOVE", "DEMO-WIRE", "DEMO-PLA", "DEMO-TAPE", "DEMO-OLD")


def _env(tmp_path: Path, app_env: str = "demo") -> dict[str, str]:
    return {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{(tmp_path / 'demo.db').as_posix()}",
        "APP_ENV": app_env,
        "PYTHONPATH": str(BACKEND_DIR),
    }


def _migrate(env: dict[str, str]) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert result.returncode == 0, result.stderr


def _seed(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SEED_SCRIPT), *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def test_seed_requires_explicit_confirmation(tmp_path: Path) -> None:
    env = _env(tmp_path)

    result = _seed(env)

    assert result.returncode == 0
    assert "--yes" in result.stdout
    assert not (tmp_path / "demo.db").exists()


def test_seed_rejects_production_environment(tmp_path: Path) -> None:
    env = _env(tmp_path, app_env="prod")

    result = _seed(env, "--yes")

    assert result.returncode == 2
    assert "正式环境禁止装载演示数据" in result.stderr


def test_seed_rejects_demo_environment_without_demo_database(tmp_path: Path) -> None:
    env = {
        **_env(tmp_path),
        "DATABASE_URL": f"sqlite:///{(tmp_path / 'manage_system.db').as_posix()}",
    }

    result = _seed(env, "--yes")

    assert result.returncode == 2
    assert "数据库标识不含 demo" in result.stderr


def test_seed_is_isolated_idempotent_and_reproduces_demo_scenarios(tmp_path: Path) -> None:
    env = _env(tmp_path)
    _migrate(env)
    db_path = tmp_path / "demo.db"

    with _connect(db_path) as connection:
        connection.execute(
            "INSERT INTO consumables ("
            "code, name, spec, unit, location, safety_stock, current_stock, "
            "class_id, status, version, created_at, updated_at"
            ") VALUES ('C-REAL01', '正式耗材', '', '件', '正式柜', 5, 99, 1, "
            "'enabled', 1, '2026-11-01 08:00:00', '2026-11-01 08:00:00')"
        )
        connection.commit()

    first = _seed(env, "--yes")
    assert first.returncode == 0, first.stderr
    assert "档位 urgent" in first.stdout
    assert "档位 moderate" in first.stdout
    assert "档位 observe" in first.stdout

    with _connect(db_path) as connection:
        accounts = {
            row["account"]
            for row in connection.execute(
                "SELECT account FROM users WHERE account IN "
                "('teacher01', 'admin01', 'student01')"
            )
        }
        assert accounts == {"teacher01", "admin01", "student01"}
        pending = connection.execute(
            "SELECT COUNT(*) FROM users WHERE must_change_password = 1"
        ).fetchone()[0]
        assert pending == 0
        stocks = {
            row["code"]: (row["current_stock"], row["safety_stock"], row["status"])
            for row in connection.execute(
                "SELECT code, current_stock, safety_stock, status FROM consumables"
            )
        }
        assert stocks["DEMO-GLOVE"] == (0, 20, "enabled")
        assert stocks["DEMO-WIRE"] == (5, 10, "enabled")
        assert stocks["DEMO-PLA"] == (30, 20, "enabled")
        assert stocks["DEMO-TAPE"] == (40, 5, "enabled")
        assert stocks["DEMO-OLD"] == (12, 5, "disabled")
        assert stocks["C-REAL01"] == (99, 5, "enabled")
        order_count = connection.execute(
            "SELECT COUNT(*) FROM requisition_orders"
        ).fetchone()[0]
        record_count = connection.execute(
            "SELECT COUNT(*) FROM requisitions"
        ).fetchone()[0]
        for code in DEMO_CODES:
            consumable_id = connection.execute(
                "SELECT id FROM consumables WHERE code = ?", (code,)
            ).fetchone()[0]
            events = connection.execute(
                "SELECT exec_seq, delta, stock_before, stock_after FROM inventory_events "
                "WHERE consumable_id = ? ORDER BY exec_seq",
                (consumable_id,),
            ).fetchall()
            assert events
            assert [event["exec_seq"] for event in events] == list(
                range(1, len(events) + 1)
            )
            for event in events:
                assert event["stock_after"] == event["stock_before"] + event["delta"]
            stock = connection.execute(
                "SELECT current_stock FROM consumables WHERE id = ?",
                (consumable_id,),
            ).fetchone()[0]
            assert events[-1]["stock_after"] == stock

    second = _seed(env, "--yes")
    assert second.returncode == 0, second.stderr

    with _connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM requisition_orders"
        ).fetchone()[0] == order_count
        assert connection.execute(
            "SELECT COUNT(*) FROM requisitions"
        ).fetchone()[0] == record_count
        assert connection.execute(
            "SELECT current_stock FROM consumables WHERE code = 'DEMO-GLOVE'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT current_stock FROM consumables WHERE code = 'C-REAL01'"
        ).fetchone()[0] == 99
