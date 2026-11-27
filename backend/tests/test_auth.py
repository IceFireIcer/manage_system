import pytest
from app.models import OperationLog
from fastapi.testclient import TestClient
from sqlalchemy import select

ROLE_ACCOUNTS = [
    ("teacher01", "Teacher@123", "王老师", "teacher"),
    ("admin01", "Admin@123", "李班委", "admin"),
    ("student01", "Student@123", "张同学", "student"),
]


@pytest.mark.parametrize(("account", "password", "name", "role"), ROLE_ACCOUNTS)
def test_login_success_for_each_role(
    client: TestClient, account: str, password: str, name: str, role: str
) -> None:
    response = client.post("/api/v1/auth/login", json={"account": account, "password": password})

    assert response.status_code == 200
    data = response.json()
    assert data["account"] == account
    assert data["name"] == name
    assert data["role"] == role


def test_login_wrong_password_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"account": "teacher01", "password": "wrong-password"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "账号或密码错误"


def test_login_unknown_account_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"account": "nobody", "password": "whatever"}
    )

    assert response.status_code == 401


def test_login_success_sets_secure_cookie(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"account": "teacher01", "password": "Teacher@123"}
    )

    assert response.status_code == 200
    cookie = response.headers.get("set-cookie", "").lower()
    assert "access_token=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "max-age=28800" in cookie


def test_me_requires_login(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["detail"] == "未登录"


def test_me_returns_current_user(teacher_client: TestClient) -> None:
    response = teacher_client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "account": "teacher01",
        "name": "王老师",
        "role": "teacher",
    }


def test_student_forbidden_on_restricted_route(student_client: TestClient) -> None:
    response = student_client.get("/api/v1/users")

    assert response.status_code == 403
    assert response.json()["detail"] == "无权限访问"


def test_teacher_forbidden_on_user_management(teacher_client: TestClient) -> None:
    response = teacher_client.get("/api/v1/users")

    assert response.status_code == 403
    assert response.json()["detail"] == "无权限访问"


def test_logout_clears_session(client: TestClient) -> None:
    client.post("/api/v1/auth/login", json={"account": "student01", "password": "Student@123"})
    assert client.get("/api/v1/auth/me").status_code == 200

    logout_response = client.post("/api/v1/auth/logout")

    assert logout_response.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401


def test_disabled_account_login_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"account": "disabled01", "password": "Disabled@123"}
    )

    assert response.status_code == 401


def test_login_attempts_written_to_operation_logs(
    client: TestClient, db_session
) -> None:
    client.post("/api/v1/auth/login", json={"account": "teacher01", "password": "wrong-password"})
    client.post("/api/v1/auth/login", json={"account": "teacher01", "password": "Teacher@123"})

    logs = db_session.scalars(
        select(OperationLog).where(OperationLog.action == "login")
    ).all()
    assert len(logs) == 2

    failure = next(log for log in logs if log.remark)
    assert "密码错误" in failure.remark
    success = next(log for log in logs if log.after_value)
    assert success.after_value == "teacher01"
