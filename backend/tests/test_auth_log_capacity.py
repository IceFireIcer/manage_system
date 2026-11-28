from datetime import timedelta

import bcrypt
from app import business_time
from app.auth_logs import trim_auth_logs
from app.config import Settings
from app.models import OperationLog, User
from fastapi.testclient import TestClient
from sqlalchemy import func, select

FAST_HASH = bcrypt.hashpw(b"Fast@123", bcrypt.gensalt(rounds=4)).decode()


def _log(action: str, created_at) -> OperationLog:
    return OperationLog(action=action, remark="test", created_at=created_at)


def test_trim_auth_logs_applies_retention_and_keeps_business_logs(db_session) -> None:
    now = business_time.now()
    for days in (120, 91):
        db_session.add(_log("login", now - timedelta(days=days)))
    db_session.add(_log("login", now - timedelta(days=1)))
    db_session.add(_log("logout", now - timedelta(days=2)))
    db_session.add(_log("requisition", now - timedelta(days=200)))
    db_session.commit()

    settings = Settings(_env_file=None, auth_log_retention_days=90, auth_log_max_rows=0)
    deleted = trim_auth_logs(db_session, settings)
    db_session.commit()

    assert deleted == 2
    actions = db_session.scalars(
        select(OperationLog.action).order_by(OperationLog.created_at.asc())
    ).all()
    assert actions == ["requisition", "logout", "login"]


def test_trim_auth_logs_caps_rows_and_keeps_newest(db_session) -> None:
    now = business_time.now()
    for index in range(5):
        db_session.add(_log("login", now - timedelta(minutes=10 - index)))
    db_session.commit()

    settings = Settings(_env_file=None, auth_log_retention_days=90, auth_log_max_rows=2)
    deleted = trim_auth_logs(db_session, settings)
    db_session.commit()

    assert deleted == 3
    remaining = db_session.scalars(
        select(OperationLog.created_at).order_by(OperationLog.created_at.asc())
    ).all()
    assert len(remaining) == 2
    assert remaining[-1] == now - timedelta(minutes=6)


def test_login_keeps_auth_log_table_within_capacity(
    client: TestClient, db_session, monkeypatch
) -> None:
    monkeypatch.setenv("AUTH_LOG_MAX_ROWS", "3")
    monkeypatch.setenv("AUTH_LOG_RETENTION_DAYS", "90")
    db_session.add(
        User(
            account="capacity01",
            password_hash=FAST_HASH,
            name="容量测试",
            role="student",
            status="enabled",
        )
    )
    db_session.commit()

    for _ in range(5):
        response = client.post(
            "/api/v1/auth/login",
            json={"account": "capacity01", "password": "Fast@123"},
        )
        assert response.status_code == 200

    count = db_session.scalar(
        select(func.count()).select_from(OperationLog).where(OperationLog.action == "login")
    )
    assert count == 3
