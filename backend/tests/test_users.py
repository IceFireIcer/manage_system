import pytest
from app.models import OperationLog, User
from fastapi.testclient import TestClient
from sqlalchemy import select

NEW_STUDENT = {
    "account": "newstudent01",
    "name": "新同学",
    "password": "NewPass@123",
    "role": "student",
}


def _get_user_id(admin_client: TestClient, account: str) -> int:
    users = admin_client.get("/api/v1/users").json()
    return next(user["id"] for user in users if user["account"] == account)


def test_endpoints_require_login(client: TestClient) -> None:
    assert client.get("/api/v1/users").status_code == 401
    assert client.post("/api/v1/users", json=NEW_STUDENT).status_code == 401
    assert client.patch("/api/v1/users/1/status", json={"status": "disabled"}).status_code == 401


def test_student_forbidden_on_all_user_endpoints(student_client: TestClient) -> None:
    assert student_client.get("/api/v1/users").status_code == 403
    assert student_client.post("/api/v1/users", json=NEW_STUDENT).status_code == 403
    patch_response = student_client.patch(
        "/api/v1/users/1/status", json={"status": "disabled"}
    )
    assert patch_response.status_code == 403


def test_teacher_forbidden_on_all_user_endpoints(teacher_client: TestClient) -> None:
    assert teacher_client.get("/api/v1/users").status_code == 403
    assert teacher_client.post("/api/v1/users", json=NEW_STUDENT).status_code == 403
    patch_response = teacher_client.patch(
        "/api/v1/users/1/status", json={"status": "disabled"}
    )
    assert patch_response.status_code == 403


def test_admin_can_list_users(admin_client: TestClient) -> None:
    response = admin_client.get("/api/v1/users")

    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 4
    for item in data:
        assert "password_hash" not in item
        assert set(item.keys()) == {"id", "account", "name", "role", "status", "created_at"}


def test_list_users_filter_by_role_and_status(admin_client: TestClient) -> None:
    response = admin_client.get("/api/v1/users", params={"role": "student", "status": "enabled"})

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["account"] == "student01"
    assert data[0]["status"] == "enabled"


def test_admin_create_student_success(admin_client: TestClient, db_session) -> None:
    response = admin_client.post("/api/v1/users", json=NEW_STUDENT)

    assert response.status_code == 201
    data = response.json()
    assert data["account"] == "newstudent01"
    assert data["name"] == "新同学"
    assert data["role"] == "student"
    assert data["status"] == "enabled"
    assert "password_hash" not in data

    user = db_session.scalars(select(User).where(User.account == "newstudent01")).first()
    assert user is not None
    assert user.password_hash != NEW_STUDENT["password"]
    assert user.password_hash.startswith("$2")


def test_created_student_can_login(admin_client: TestClient) -> None:
    create_response = admin_client.post("/api/v1/users", json=NEW_STUDENT)
    assert create_response.status_code == 201

    login_response = admin_client.post(
        "/api/v1/auth/login",
        json={"account": NEW_STUDENT["account"], "password": NEW_STUDENT["password"]},
    )

    assert login_response.status_code == 200
    assert login_response.json()["role"] == "student"


@pytest.mark.parametrize("forbidden_role", ["teacher", "admin"])
def test_admin_cannot_create_teacher_or_admin(
    admin_client: TestClient, forbidden_role: str
) -> None:
    response = admin_client.post(
        "/api/v1/users", json={**NEW_STUDENT, "role": forbidden_role}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "仅允许创建学生账号"


def test_create_missing_role_rejected(admin_client: TestClient) -> None:
    payload = {k: v for k, v in NEW_STUDENT.items() if k != "role"}
    response = admin_client.post("/api/v1/users", json=payload)

    assert response.status_code == 422


def test_create_invalid_role_rejected(admin_client: TestClient) -> None:
    response = admin_client.post("/api/v1/users", json={**NEW_STUDENT, "role": "manager"})

    assert response.status_code == 400
    assert response.json()["detail"] == "仅允许创建学生账号"


def test_create_duplicate_account_rejected(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/api/v1/users", json={**NEW_STUDENT, "account": "student01"}
    )

    assert response.status_code == 400
    assert "已存在" in response.json()["detail"]


def test_create_short_password_rejected(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/api/v1/users", json={**NEW_STUDENT, "password": "12345"}
    )

    assert response.status_code == 422


def test_create_empty_account_rejected(admin_client: TestClient) -> None:
    response = admin_client.post("/api/v1/users", json={**NEW_STUDENT, "account": ""})

    assert response.status_code == 422


def test_disable_then_login_rejected_and_record_kept(
    admin_client: TestClient, db_session
) -> None:
    user_id = _get_user_id(admin_client, "student01")
    response = admin_client.patch(
        f"/api/v1/users/{user_id}/status", json={"status": "disabled"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "disabled"

    user = db_session.get(User, user_id)
    assert user is not None
    assert user.status == "disabled"

    login_response = admin_client.post(
        "/api/v1/auth/login", json={"account": "student01", "password": "Student@123"}
    )
    assert login_response.status_code == 401


def test_enable_disabled_student_then_login(admin_client: TestClient) -> None:
    user_id = _get_user_id(admin_client, "disabled01")
    response = admin_client.patch(
        f"/api/v1/users/{user_id}/status", json={"status": "enabled"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "enabled"

    login_response = admin_client.post(
        "/api/v1/auth/login", json={"account": "disabled01", "password": "Disabled@123"}
    )
    assert login_response.status_code == 200


def test_admin_cannot_change_own_status(admin_client: TestClient) -> None:
    user_id = _get_user_id(admin_client, "admin01")
    response = admin_client.patch(
        f"/api/v1/users/{user_id}/status", json={"status": "disabled"}
    )

    assert response.status_code == 400
    assert "自己" in response.json()["detail"]


def test_admin_cannot_change_teacher_status(admin_client: TestClient) -> None:
    user_id = _get_user_id(admin_client, "teacher01")
    response = admin_client.patch(
        f"/api/v1/users/{user_id}/status", json={"status": "disabled"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "仅允许对学生账号进行启停"


def test_patch_unknown_user_returns_404(admin_client: TestClient) -> None:
    response = admin_client.patch("/api/v1/users/999/status", json={"status": "disabled"})

    assert response.status_code == 404


def test_user_create_written_to_logs(admin_client: TestClient, db_session) -> None:
    admin_client.post("/api/v1/users", json=NEW_STUDENT)

    logs = db_session.scalars(
        select(OperationLog).where(OperationLog.action == "user_create")
    ).all()
    assert len(logs) == 1
    log = logs[0]
    assert log.target_id is not None
    assert log.after_value == "newstudent01"
    assert log.operator_id == _get_user_id(admin_client, "admin01")

    fields = [log.before_value, log.after_value, log.remark]
    assert all(NEW_STUDENT["password"] not in (field or "") for field in fields)


def test_enable_disable_written_to_logs(admin_client: TestClient, db_session) -> None:
    student_id = _get_user_id(admin_client, "student01")
    disabled_id = _get_user_id(admin_client, "disabled01")

    admin_client.patch(f"/api/v1/users/{student_id}/status", json={"status": "disabled"})
    admin_client.patch(f"/api/v1/users/{disabled_id}/status", json={"status": "enabled"})

    disable_log = db_session.scalars(
        select(OperationLog).where(OperationLog.action == "user_disable")
    ).first()
    assert disable_log is not None
    assert disable_log.before_value == "enabled"
    assert disable_log.after_value == "disabled"
    assert disable_log.target_id == student_id

    enable_log = db_session.scalars(
        select(OperationLog).where(OperationLog.action == "user_enable")
    ).first()
    assert enable_log is not None
    assert enable_log.before_value == "disabled"
    assert enable_log.after_value == "enabled"
    assert enable_log.target_id == disabled_id
