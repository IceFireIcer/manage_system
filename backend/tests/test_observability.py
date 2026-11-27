from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from app.config import Settings, get_settings
from app.observability import (
    backup_observation,
    collect_observability,
    disk_observation,
    error_window,
    record_error,
    reset_errors,
)
from app.routers.ops import _is_ops_request, get_observability
from fastapi import Request
from sqlalchemy.orm import Session

OPS_PATH = "/api/v1/ops/observability"


@pytest.fixture(autouse=True)
def _reset_error_window() -> Iterator[None]:
    reset_errors()
    yield
    reset_errors()


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def _request_for(host: str) -> Request:
    scope = {
        "type": "http",
        "client": (host, 50000),
        "method": "GET",
        "path": OPS_PATH,
        "headers": [],
        "query_string": b"",
        "scheme": "http",
    }
    return Request(scope)


def test_ops_route_rejects_non_loopback(client) -> None:
    response = client.get(OPS_PATH)

    assert response.status_code == 403
    assert response.json()["detail"] == "运维观测仅允许运维入口访问"


def test_ops_route_returns_snapshot_from_loopback(client, monkeypatch) -> None:
    monkeypatch.setattr("app.routers.ops._is_ops_request", lambda request: True)

    response = client.get(OPS_PATH)

    assert response.status_code == 200
    body = response.json()
    assert body["readiness"]["ready"] is True
    assert body["request_id"]
    assert "errors" in body
    assert "backup" in body
    assert "disk" in body


def test_loopback_detection() -> None:
    assert _is_ops_request(_request_for("127.0.0.1")) is True
    assert _is_ops_request(_request_for("::1")) is True
    assert _is_ops_request(_request_for("203.0.113.9")) is False


def test_backup_failure_signal_triggers_alert(tmp_path: Path) -> None:
    (tmp_path / "backup-failed.log").write_text(
        "[db-backup] 失败时间 2026-11-28T08:00:00+08:00：mysqldump 执行失败\n",
        encoding="utf-8",
    )
    settings = _settings(ops_backup_dir=str(tmp_path))

    observation = backup_observation(settings)

    assert observation["state"] == "failed"
    assert observation["alert"] is True
    assert observation["last_failure_at"] == "2026-11-28T08:00:00+08:00"


def test_stale_backup_triggers_alert(tmp_path: Path) -> None:
    manifest = tmp_path / "manage_system_20260101_000000.manifest"
    manifest.write_text(
        "created_at=2026-01-01T00:00:00+08:00\nimage_tag=latest\n",
        encoding="utf-8",
    )
    settings = _settings(ops_backup_dir=str(tmp_path), ops_backup_max_age_hours=24)

    observation = backup_observation(settings)

    assert observation["state"] == "stale"
    assert observation["alert"] is True
    assert observation["age_hours"] is not None


def test_missing_backup_directory_does_not_alert(tmp_path: Path) -> None:
    settings = _settings(ops_backup_dir=str(tmp_path / "absent"))

    observation = backup_observation(settings)

    assert observation["state"] == "absent"
    assert observation["alert"] is False


def test_disk_threshold_triggers_alert(tmp_path: Path) -> None:
    settings = _settings(
        ops_backup_dir=str(tmp_path),
        ops_disk_min_free_mb=1024 * 1024 * 1024,
    )

    observation = disk_observation(settings)

    assert observation["alert"] is True
    assert observation["alerts"]


def test_error_window_peak_triggers_alert(caplog: pytest.LogCaptureFixture) -> None:
    settings = _settings(ops_error_alert_threshold=3, ops_error_window_seconds=300)
    caplog.set_level(logging.WARNING, logger="app.ops")

    for index in range(3):
        record_error(settings, now=100.0 + index)

    observation = error_window(settings, now=103.0)

    assert observation.total == 3
    assert observation.peak == 3
    assert observation.alert is True
    assert any("运维告警" in record.message for record in caplog.records)


def test_error_window_expires_old_errors() -> None:
    settings = _settings(ops_error_alert_threshold=2, ops_error_window_seconds=60)

    record_error(settings, now=100.0)
    record_error(settings, now=100.5)
    observation = error_window(settings, now=500.0)

    assert observation.total == 0
    assert observation.alert is False


def test_collect_observability_surfaces_backup_failure(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "backup-failed.log").write_text(
        "[db-backup] 失败时间 2026-11-28T08:00:00+08:00：mysqldump 执行失败\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPS_BACKUP_DIR", str(tmp_path))
    get_settings.cache_clear()

    payload = collect_observability()

    assert payload["backup"]["alert"] is True
    assert any("数据库备份失败" in alert for alert in payload["alerts"])


def test_ops_snapshot_ready_for_loopback_session(db_session: Session) -> None:
    payload = get_observability(_request_for("127.0.0.1"), db_session)

    assert payload["readiness"]["ready"] is True
    assert payload["request_id"] == ""
