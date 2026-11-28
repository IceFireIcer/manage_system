import json
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from app.database import Base, get_db
from app.main import app
from app.models import OperationLog, User
from app.provisioning import (
    ProvisioningError,
    bootstrap_initial_admin,
    bootstrap_state,
    handover_admin,
    provision_teacher,
    recover_admin,
)
from app.security import hash_password, verify_password
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@dataclass
class Environment:
    db: Session
    client: TestClient
    session_factory: sessionmaker


@pytest.fixture()
def env() -> Iterator[Environment]:
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
        yield Environment(db=db, client=TestClient(app), session_factory=factory)
    app.dependency_overrides.clear()


def _seed(
    env: Environment,
    *,
    account: str,
    password: str,
    name: str,
    role: str,
    status: str = "enabled",
) -> User:
    user = User(
        account=account,
        password_hash=hash_password(password),
        name=name,
        role=role,
        status=status,
        must_change_password=False,
        session_version=0,
    )
    env.db.add(user)
    env.db.commit()
    env.db.refresh(user)
    return user


def _login(account: str, password: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login", json={"account": account, "password": password}
    )
    assert response.status_code == 200, response.text
    return client


def _count_users(env: Environment) -> int:
    return env.db.scalar(select(func.count()).select_from(User)) or 0


def _logs(env: Environment, action: str) -> list[OperationLog]:
    return list(
        env.db.scalars(select(OperationLog).where(OperationLog.action == action)).all()
    )


def test_empty_database_reports_needs_initialization(env: Environment) -> None:
    assert bootstrap_state(env.db) == "needs_initialization"

    status_response = env.client.get("/api/v1/bootstrap/status")
    assert status_response.status_code == 200
    body = status_response.json()
    assert body["state"] == "needs_initialization"
    assert "需初始化" in body["message"]

    readiness = env.client.get("/api/v1/bootstrap/readiness")
    assert readiness.status_code == 503
    assert readiness.json()["state"] == "needs_initialization"


def test_bootstrap_creates_admin_requiring_first_login_change(env: Environment) -> None:
    result = bootstrap_initial_admin(
        env.db, account="admin", name="系统管理员", password="AdminInit@123"
    )

    assert result is not None
    assert result.role == "super_admin"
    assert result.generated is False
    admin = env.db.scalars(select(User).where(User.account == "admin")).one()
    assert admin.status == "enabled"
    assert admin.must_change_password is True
    assert bootstrap_state(env.db) == "ready"
    assert env.client.get("/api/v1/bootstrap/readiness").status_code == 200

    session = _login("admin", "AdminInit@123").get("/api/v1/auth/session").json()
    assert session["must_change_password"] is True


def test_bootstrap_is_idempotent_with_existing_admin(env: Environment) -> None:
    _seed(env, account="class01", password="Class@123", name="李班委", role="admin")

    assert (
        bootstrap_initial_admin(
            env.db, account="admin", name="系统管理员", password="AdminInit@123"
        )
        is None
    )
    assert _count_users(env) == 1


def test_bootstrap_refuses_to_create_when_recovery_required(env: Environment) -> None:
    _seed(
        env,
        account="class01",
        password="Class@123",
        name="李班委",
        role="admin",
        status="disabled",
    )

    with pytest.raises(ProvisioningError) as excinfo:
        bootstrap_initial_admin(
            env.db, account="admin", name="系统管理员", password="AdminInit@123"
        )

    assert "需恢复" in str(excinfo.value)
    assert _count_users(env) == 1


def test_empty_database_can_provide_teacher_admin_student(env: Environment) -> None:
    bootstrap_initial_admin(
        env.db, account="admin", name="系统管理员", password="AdminInit@123"
    )
    provision_teacher(
        env.db, account="teacher01", name="王老师", password="TeacherInit@123"
    )

    admin_client = _login("admin", "AdminInit@123")
    changed = admin_client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "AdminInit@123", "new_password": "AdminNew@123"},
    )
    assert changed.status_code == 200
    created = admin_client.post(
        "/api/v1/users",
        json={
            "account": "student01",
            "name": "张同学",
            "password": "StudentInit@123",
            "role": "student",
        },
    )
    assert created.status_code == 201

    teacher_client = _login("teacher01", "TeacherInit@123")
    assert teacher_client.get("/api/v1/auth/me").json()["role"] == "teacher"
    student_client = _login("student01", "StudentInit@123")
    assert student_client.get("/api/v1/auth/me").json()["role"] == "student"

    assert (
        teacher_client.post(
            "/api/v1/auth/change-password",
            json={"old_password": "TeacherInit@123", "new_password": "TeacherNew@123"},
        ).status_code
        == 200
    )
    forbidden = teacher_client.post(
        "/api/v1/users",
        json={
            "account": "teacher02",
            "name": "新教师",
            "password": "Teacher2@123",
            "role": "teacher",
        },
    )
    assert forbidden.status_code == 403


def test_provision_teacher_keeps_existing_admin(env: Environment) -> None:
    admin = _seed(
        env, account="class01", password="Class@123", name="李班委", role="admin"
    )
    before = (admin.role, admin.status, admin.session_version, admin.password_hash)

    result = provision_teacher(
        env.db, account="teacher02", name="李老师", password="Teacher@123"
    )

    env.db.refresh(admin)
    assert (admin.role, admin.status, admin.session_version, admin.password_hash) == before
    assert result.role == "teacher"
    teacher = env.db.scalars(select(User).where(User.account == "teacher02")).one()
    assert teacher.role == "teacher"
    assert teacher.must_change_password is True
    assert bootstrap_state(env.db) == "ready"


def test_handover_disables_previous_admin_and_invalidates_session(env: Environment) -> None:
    old = _seed(
        env, account="class01", password="Class@123", name="原班委", role="admin"
    )
    old_client = _login("class01", "Class@123")
    assert old_client.get("/api/v1/auth/me").status_code == 200

    result = handover_admin(
        env.db,
        previous_account="class01",
        account="class02",
        name="新班委",
        password="Class2@123",
        actor="张主任",
    )

    env.db.refresh(old)
    assert old.role == "admin"
    assert old.status == "disabled"
    assert old.session_version == 1
    assert result.new_admin.role == "admin"
    new_admin = env.db.scalars(select(User).where(User.account == "class02")).one()
    assert new_admin.status == "enabled"
    assert new_admin.must_change_password is True
    assert old_client.get("/api/v1/auth/me").status_code == 401

    log = _logs(env, "admin_handover")[0]
    assert log.role == "operator"
    assert log.operator_id is None
    assert log.created_at is not None
    payload = json.loads(log.after_value)
    assert payload["actor"] == "张主任"
    assert payload["previous_account"] == "class01"
    assert payload["new_account"] == "class02"
    assert payload["result"] == "success"
    assert "张主任" in log.remark
    assert "Class2@123" not in " ".join(
        [log.before_value or "", log.after_value or "", log.remark or ""]
    )


def test_recover_custom_admin_resets_password_and_sessions(env: Environment) -> None:
    custom = _seed(
        env,
        account="school_admin",
        password="OldPass@123",
        name="校方管理员",
        role="admin",
    )
    teacher = _seed(
        env, account="teacher01", password="Teacher@123", name="王老师", role="teacher"
    )
    teacher_hash = teacher.password_hash
    old_client = _login("school_admin", "OldPass@123")

    result = recover_admin(
        env.db,
        account="school_admin",
        name="校方管理员",
        password="Recover@123",
        actor="张主任",
    )

    env.db.refresh(custom)
    env.db.refresh(teacher)
    assert result.role == "admin"
    assert result.generated is False
    assert custom.status == "enabled"
    assert custom.must_change_password is True
    assert custom.session_version == 1
    assert verify_password("Recover@123", custom.password_hash)
    assert not verify_password("OldPass@123", custom.password_hash)
    assert teacher.role == "teacher"
    assert teacher.password_hash == teacher_hash
    assert old_client.get("/api/v1/auth/me").status_code == 401

    log = _logs(env, "admin_recover")[0]
    assert json.loads(log.after_value)["actor"] == "张主任"


def test_recover_creates_custom_admin_when_no_enabled_admin(env: Environment) -> None:
    _seed(
        env,
        account="class01",
        password="Class@123",
        name="停用班委",
        role="admin",
        status="disabled",
    )
    assert bootstrap_state(env.db) == "needs_recovery"

    result = recover_admin(
        env.db, account="school_admin", name="校方管理员", password="Recover@123"
    )

    assert result.role == "admin"
    assert result.generated is False
    created = env.db.scalars(select(User).where(User.account == "school_admin")).one()
    assert created.status == "enabled"
    assert created.must_change_password is True
    assert bootstrap_state(env.db) == "ready"


def test_recover_refuses_extra_admin_when_one_is_usable(env: Environment) -> None:
    _seed(env, account="class01", password="Class@123", name="李班委", role="admin")

    with pytest.raises(ProvisioningError) as excinfo:
        recover_admin(env.db, account="second_admin", name="另一管理员")

    assert "班委交接" in str(excinfo.value)
    assert _count_users(env) == 1


def test_recover_rejects_teacher_account(env: Environment) -> None:
    teacher = _seed(
        env, account="teacher01", password="Teacher@123", name="王老师", role="teacher"
    )
    before = (teacher.role, teacher.status, teacher.password_hash, teacher.session_version)

    with pytest.raises(ProvisioningError) as excinfo:
        recover_admin(env.db, account="teacher01", password="Recover@123")

    assert "不能把教师或学生提升为管理员" in str(excinfo.value)
    env.db.refresh(teacher)
    assert (
        teacher.role,
        teacher.status,
        teacher.password_hash,
        teacher.session_version,
    ) == before
    rejected = _logs(env, "admin_recover_rejected")[0]
    assert json.loads(rejected.after_value)["result"] == "rejected"


def test_provision_teacher_rejects_existing_account(env: Environment) -> None:
    admin = _seed(
        env, account="class01", password="Class@123", name="李班委", role="admin"
    )
    before = (admin.role, admin.status, admin.password_hash, admin.session_version)

    with pytest.raises(ProvisioningError) as excinfo:
        provision_teacher(env.db, account="class01", name="冒充教师")

    assert "已存在" in str(excinfo.value)
    env.db.refresh(admin)
    assert (admin.role, admin.status, admin.password_hash, admin.session_version) == before
    rejected = _logs(env, "teacher_provision_rejected")[0]
    assert json.loads(rejected.after_value)["result"] == "rejected"
    assert json.loads(rejected.after_value)["actor"] == "校方授权人"


def test_handover_rejects_non_admin_source(env: Environment) -> None:
    teacher = _seed(
        env, account="teacher01", password="Teacher@123", name="王老师", role="teacher"
    )

    with pytest.raises(ProvisioningError) as excinfo:
        handover_admin(
            env.db,
            previous_account="teacher01",
            account="class02",
            name="新班委",
            password="Class2@123",
        )

    assert "提升权限" in str(excinfo.value)
    env.db.refresh(teacher)
    assert teacher.role == "teacher"
    assert teacher.status == "enabled"
    assert teacher.session_version == 0


def test_status_reports_needs_recovery_when_admin_disabled(env: Environment) -> None:
    _seed(
        env,
        account="class01",
        password="Class@123",
        name="停用班委",
        role="admin",
        status="disabled",
    )

    body = env.client.get("/api/v1/bootstrap/status").json()
    assert body["state"] == "needs_recovery"
    assert "需恢复" in body["message"]

    readiness = env.client.get("/api/v1/bootstrap/readiness")
    assert readiness.status_code == 503
    assert "可用管理员 0 个" in readiness.json()["message"]
