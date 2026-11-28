import pytest
from app.main import app
from app.models import OperationLog, User
from fastapi.testclient import TestClient
from sqlalchemy import select

NEW_STUDENT = {
    "account": "fresh01",
    "name": "新同学",
    "password": "InitPass@123",
    "role": "student",
}


def _login(instance: TestClient, account: str, password: str) -> None:
    response = instance.post(
        "/api/v1/auth/login", json={"account": account, "password": password}
    )
    assert response.status_code == 200, response.text


def _user_id(db_session, account: str) -> int:
    user = db_session.scalars(select(User).where(User.account == account)).first()
    assert user is not None
    return user.id


def _client_with_token(token: str) -> TestClient:
    instance = TestClient(app)
    instance.cookies.set("access_token", token)
    return instance


def test_first_login_change_password_invalidates_initial_credentials(client, db_session) -> None:
    admin = TestClient(app)
    _login(admin, "admin01", "Admin@123")
    created = admin.post("/api/v1/users", json=NEW_STUDENT)
    assert created.status_code == 201

    student = TestClient(app)
    _login(student, NEW_STUDENT["account"], NEW_STUDENT["password"])
    old_token = student.cookies.get("access_token")
    assert old_token

    session = student.get("/api/v1/auth/session").json()
    assert session["must_change_password"] is True
    assert session["session_expires_at"] is not None
    assert student.get("/api/v1/requisitions").status_code == 403

    changed = student.post(
        "/api/v1/auth/change-password",
        json={"old_password": NEW_STUDENT["password"], "new_password": "Changed@1234"},
    )
    assert changed.status_code == 200
    assert changed.json()["must_change_password"] is False
    new_token = student.cookies.get("access_token")
    assert new_token and new_token != old_token

    assert _client_with_token(old_token).get("/api/v1/auth/me").status_code == 401

    stale_login = TestClient(app)
    assert (
        stale_login.post(
            "/api/v1/auth/login",
            json={"account": NEW_STUDENT["account"], "password": NEW_STUDENT["password"]},
        ).status_code
        == 401
    )

    fresh = TestClient(app)
    _login(fresh, NEW_STUDENT["account"], "Changed@1234")
    assert fresh.get("/api/v1/auth/session").json()["must_change_password"] is False
    assert fresh.get("/api/v1/requisitions").status_code == 200


def test_disable_then_enable_keeps_old_session_invalid(client, db_session) -> None:
    admin = TestClient(app)
    _login(admin, "admin01", "Admin@123")
    student = TestClient(app)
    _login(student, "student01", "Student@123")
    old_token = student.cookies.get("access_token")
    assert student.get("/api/v1/auth/me").status_code == 200

    user_id = _user_id(db_session, "student01")
    assert (
        admin.patch(
            f"/api/v1/users/{user_id}/status", json={"status": "disabled"}
        ).status_code
        == 200
    )
    assert _client_with_token(old_token).get("/api/v1/auth/me").status_code == 401

    assert (
        admin.patch(
            f"/api/v1/users/{user_id}/status", json={"status": "enabled"}
        ).status_code
        == 200
    )
    assert _client_with_token(old_token).get("/api/v1/auth/me").status_code == 401

    relogin = TestClient(app)
    _login(relogin, "student01", "Student@123")
    assert relogin.get("/api/v1/auth/me").status_code == 200


def test_logout_current_only_keeps_other_sessions(client) -> None:
    first = TestClient(app)
    second = TestClient(app)
    _login(first, "student01", "Student@123")
    _login(second, "student01", "Student@123")

    assert first.post("/api/v1/auth/logout").status_code == 200
    assert first.get("/api/v1/auth/me").status_code == 401
    assert second.get("/api/v1/auth/me").status_code == 200


def test_logout_all_revokes_every_session(client) -> None:
    first = TestClient(app)
    second = TestClient(app)
    _login(first, "student01", "Student@123")
    _login(second, "student01", "Student@123")
    second_token = second.cookies.get("access_token")

    assert first.post("/api/v1/auth/logout-all").status_code == 200
    assert first.get("/api/v1/auth/me").status_code == 401
    assert _client_with_token(second_token).get("/api/v1/auth/me").status_code == 401


@pytest.mark.parametrize(
    "payload",
    [
        {"old_password": "wrong-password", "new_password": "Changed@1234"},
        {"old_password": "Student@123", "new_password": "Student@123"},
    ],
)
def test_change_password_rejects_invalid_requests(client, payload) -> None:
    student = TestClient(app)
    _login(student, "student01", "Student@123")

    response = student.post("/api/v1/auth/change-password", json=payload)

    assert response.status_code == 400


def test_change_password_audit_has_no_plaintext(client, db_session) -> None:
    student = TestClient(app)
    _login(student, "student01", "Student@123")

    student.post(
        "/api/v1/auth/change-password",
        json={"old_password": "Student@123", "new_password": "BrandNew@123"},
    )

    log = db_session.scalars(
        select(OperationLog).where(OperationLog.action == "password_change")
    ).first()
    assert log is not None
    serialized = " ".join(
        [log.before_value or "", log.after_value or "", log.remark or ""]
    )
    assert "Student@123" not in serialized
    assert "BrandNew@123" not in serialized
