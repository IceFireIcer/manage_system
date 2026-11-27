from collections.abc import Iterator

import pytest
from app.config import get_settings
from app.database import Base, get_db
from app.login_throttle import login_throttle
from app.main import app
from app.models import User
from app.security import hash_password
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

SEED_USERS = [
    ("teacher01", "Teacher@123", "王老师", "teacher", "enabled"),
    ("admin01", "Admin@123", "李班委", "admin", "enabled"),
    ("student01", "Student@123", "张同学", "student", "enabled"),
    ("disabled01", "Disabled@123", "赵停用", "student", "disabled"),
]


@pytest.fixture(autouse=True)
def _reset_login_state():
    login_throttle.reset()
    get_settings.cache_clear()
    yield
    login_throttle.reset()
    get_settings.cache_clear()


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    testing_session = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    with testing_session() as db:
        for account, password, name, role, status in SEED_USERS:
            db.add(
                User(
                    account=account,
                    password_hash=hash_password(password),
                    name=name,
                    role=role,
                    status=status,
                )
            )
        db.commit()
    return eng


@pytest.fixture()
def client(engine) -> Iterator[TestClient]:
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db() -> Iterator[Session]:
        db = testing_session()
        try:
            yield db
            db.commit()
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def db_session(engine) -> Iterator[Session]:
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with testing_session() as db:
        yield db


def _login(client: TestClient, account: str, password: str):
    return client.post("/api/v1/auth/login", json={"account": account, "password": password})


@pytest.fixture()
def teacher_client(client: TestClient) -> TestClient:
    response = _login(client, "teacher01", "Teacher@123")
    assert response.status_code == 200
    return client


@pytest.fixture()
def admin_client(client: TestClient) -> TestClient:
    response = _login(client, "admin01", "Admin@123")
    assert response.status_code == 200
    return client


@pytest.fixture()
def student_client(client: TestClient) -> TestClient:
    response = _login(client, "student01", "Student@123")
    assert response.status_code == 200
    return client
