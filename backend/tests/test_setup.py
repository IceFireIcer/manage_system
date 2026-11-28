from collections.abc import Iterator

import pytest
from app.database import Base, get_db
from app.main import app
from app.models import SystemSetting, User
from app.security import hash_password
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

SETUP_INCOMPLETE_DETAIL = "系统尚未完成初始化，请先完成初始化向导"


@pytest.fixture()
def env() -> Iterator[tuple[Session, TestClient]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db() -> Iterator[Session]:
        db = factory()
        try:
            yield db
            db.commit()
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with factory() as db:
        yield db, TestClient(app)
    app.dependency_overrides.clear()


def _seed_super_admin(db: Session, *, must_change: bool = False) -> User:
    user = User(
        account="admin",
        password_hash=hash_password("admin"),
        name="系统管理员",
        role="super_admin",
        status="enabled",
        must_change_password=must_change,
        session_version=0,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(SystemSetting, key)
    if row is None:
        db.add(SystemSetting(key=key, value=value, updated_by="test"))
    else:
        row.value = value
    db.commit()


def _login() -> TestClient:
    client = TestClient(app)
    response = client.post("/api/v1/auth/login", json={"account": "admin", "password": "admin"})
    assert response.status_code == 200, response.text
    return client


def test_status_completed_when_no_record(env: tuple[Session, TestClient]) -> None:
    _, client = env
    body = client.get("/api/v1/setup/status").json()
    assert body["setup_completed"] is True
    assert body["needs_setup"] is False


def test_status_needs_setup_when_flag_false(env: tuple[Session, TestClient]) -> None:
    db, client = env
    _set_setting(db, "setup_completed", "0")
    body = client.get("/api/v1/setup/status").json()
    assert body["needs_setup"] is True
    assert body["has_enabled_teacher"] is False


def test_business_api_blocked_before_setup(env: tuple[Session, TestClient]) -> None:
    db, _ = env
    _set_setting(db, "setup_completed", "0")
    _seed_super_admin(db)
    client = _login()

    blocked = client.get("/api/v1/users")
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == SETUP_INCOMPLETE_DETAIL

    assert client.get("/api/v1/setup/status").status_code == 200


def test_create_teacher_and_complete_setup(env: tuple[Session, TestClient]) -> None:
    db, _ = env
    _set_setting(db, "setup_completed", "0")
    _seed_super_admin(db)
    client = _login()

    created = client.post(
        "/api/v1/setup/teachers",
        json={"account": "teacher01", "name": "王老师"},
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["account"] == "teacher01"
    assert payload["temporary_password"]

    status = client.get("/api/v1/setup/status").json()
    assert status["has_enabled_teacher"] is True

    completed = client.post("/api/v1/setup/complete")
    assert completed.status_code == 200, completed.text
    assert completed.json()["setup_completed"] is True

    assert client.get("/api/v1/users").status_code == 200


def test_complete_requires_enabled_teacher(env: tuple[Session, TestClient]) -> None:
    db, _ = env
    _set_setting(db, "setup_completed", "0")
    _seed_super_admin(db)
    client = _login()

    response = client.post("/api/v1/setup/complete")
    assert response.status_code == 400
    assert "教师" in response.json()["detail"]
    assert client.get("/api/v1/setup/status").json()["setup_completed"] is False


def test_setup_teacher_rejects_duplicate_account(env: tuple[Session, TestClient]) -> None:
    db, _ = env
    _set_setting(db, "setup_completed", "0")
    _seed_super_admin(db)
    client = _login()

    duplicate = client.post(
        "/api/v1/setup/teachers",
        json={"account": "admin", "name": "冒充管理员"},
    )
    assert duplicate.status_code == 400
    assert "已存在" in duplicate.json()["detail"]
