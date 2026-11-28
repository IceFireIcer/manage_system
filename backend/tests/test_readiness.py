from __future__ import annotations

from collections.abc import Iterator

import pytest
from app.config import get_settings
from app.database import get_db
from app.main import _is_internal_probe, app
from app.readiness import code_migration_head
from fastapi import Request
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

READINESS_PATH = "/api/v1/bootstrap/readiness"
HEALTH_PATH = "/api/v1/health"
HTTPS_HEADERS = {"X-Forwarded-Proto": "https"}
VALID_JWT_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef"
VALID_DATABASE_URL = (
    "mysql+pymysql://appuser:S3curePassw0rd@db:3306/manage_system?charset=utf8mb4"
)


class UnavailableSession:
    def execute(self, *args: object, **kwargs: object) -> None:
        raise OperationalError("SELECT 1", {}, Exception("database is down"))

    def rollback(self) -> None:
        return None

    def get_bind(self) -> None:
        raise OperationalError("bind", {}, Exception("database is down"))


def _unavailable_db() -> Iterator[UnavailableSession]:
    yield UnavailableSession()


@pytest.fixture()
def unavailable_override() -> Iterator[None]:
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _unavailable_db
    yield
    if previous is None:
        app.dependency_overrides.pop(get_db, None)
    else:
        app.dependency_overrides[get_db] = previous


def _request_for(path: str, host: str) -> Request:
    scope = {
        "type": "http",
        "client": (host, 50000),
        "method": "GET",
        "path": path,
        "headers": [],
        "query_string": b"",
        "scheme": "http",
    }
    return Request(scope)


def test_readiness_ready_with_bootstrap_and_migrations(client, engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:value)"),
            {"value": code_migration_head()},
        )

    response = client.get(READINESS_PATH)

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["checks"] == {
        "database": "ok",
        "migration": "current",
        "bootstrap": "ready",
    }


def test_readiness_fails_when_database_unavailable(client, unavailable_override) -> None:
    response = client.get(READINESS_PATH)

    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["state"] == "database_unavailable"
    assert body["checks"]["database"] == "unavailable"


def test_readiness_recovers_after_database_returns(client, engine) -> None:
    normal = app.dependency_overrides[get_db]
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:value)"),
            {"value": code_migration_head()},
        )

    app.dependency_overrides[get_db] = _unavailable_db
    failed = client.get(READINESS_PATH)
    assert failed.status_code == 503

    app.dependency_overrides[get_db] = normal
    recovered = client.get(READINESS_PATH)

    assert recovered.status_code == 200
    assert recovered.json()["ready"] is True


def test_readiness_rejects_outdated_migration_in_production(client, engine, monkeypatch) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES ('deadbeefdead')")
        )
    monkeypatch.setenv("APP_ENV", "prod")
    get_settings.cache_clear()

    response = client.get(READINESS_PATH, headers=HTTPS_HEADERS)

    assert response.status_code == 503
    body = response.json()
    assert body["state"] == "not_ready"
    assert body["checks"]["migration"] == "outdated"


def test_readiness_rejects_unmanaged_migration_in_production(client, monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    get_settings.cache_clear()

    response = client.get(READINESS_PATH, headers=HTTPS_HEADERS)

    assert response.status_code == 503
    body = response.json()
    assert body["state"] == "not_ready"
    assert body["checks"]["migration"] == "unmanaged"


def test_readiness_reports_admin_count_when_recovery_needed(client, engine, monkeypatch) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:value)"),
            {"value": code_migration_head()},
        )
        connection.execute(text("UPDATE users SET status = 'disabled' WHERE role = 'admin'"))

    response = client.get(READINESS_PATH)

    assert response.status_code == 503
    body = response.json()
    assert body["state"] == "needs_recovery"
    assert "可用管理员 0 个" in body["message"]


def test_health_stays_alive_when_database_unavailable(client, unavailable_override) -> None:
    response = client.get(HEALTH_PATH)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_and_probes_skip_https_only_from_loopback() -> None:
    assert _is_internal_probe(_request_for(HEALTH_PATH, "127.0.0.1")) is True
    assert _is_internal_probe(_request_for(READINESS_PATH, "::1")) is True
    assert _is_internal_probe(_request_for(HEALTH_PATH, "203.0.113.9")) is False
    assert _is_internal_probe(_request_for(READINESS_PATH, "203.0.113.9")) is False
    assert _is_internal_probe(_request_for("/api/v1/logs", "127.0.0.1")) is False
