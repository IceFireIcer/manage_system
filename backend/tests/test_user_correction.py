import json
from typing import Any

from app.models import OperationLog, User
from fastapi.testclient import TestClient
from sqlalchemy import select

USERS_URL = "/api/v1/users"
REQUISITIONS_URL = "/api/v1/requisitions"
CONSUMABLES_URL = "/api/v1/consumables"


def _user_id(db_session, account: str) -> int:
    db_session.expire_all()
    return db_session.scalars(select(User).where(User.account == account)).one().id


def _user(db_session, account: str) -> User:
    db_session.expire_all()
    return db_session.scalars(select(User).where(User.account == account)).one()


def _create_consumable(admin_client: TestClient, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "焊丝",
        "spec": "φ1.2mm",
        "unit": "卷",
        "location": "实训室一号柜",
        "safety_stock": 5,
        "current_stock": 10,
    }
    payload.update(overrides)
    response = admin_client.post(CONSUMABLES_URL, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _create_student(
    admin_client: TestClient, account: str, name: str
) -> dict[str, Any]:
    response = admin_client.post(
        USERS_URL,
        json={
            "account": account,
            "name": name,
            "password": "Student@123",
            "role": "student",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _login(client: TestClient, account: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"account": account, "password": password}
    )
    assert response.status_code == 200, response.text


def _login_student(client: TestClient) -> None:
    _login(client, "student01", "Student@123")


def _login_teacher(client: TestClient) -> None:
    _login(client, "teacher01", "Teacher@123")


def _login_admin(client: TestClient) -> None:
    _login(client, "admin01", "Admin@123")


def _submit(client: TestClient, items: list[dict], **extra: Any):
    payload: dict[str, Any] = {"items": items}
    payload.update(extra)
    return client.post(REQUISITIONS_URL, json=payload)


def test_correction_and_reset_permissions(
    client: TestClient, db_session
) -> None:
    target_id = _user_id(db_session, "disabled01")

    _login_teacher(client)
    assert (
        client.patch(f"{USERS_URL}/{target_id}", json={"name": "新名"}).status_code
        == 200
    )
    assert (
        client.post(f"{USERS_URL}/{target_id}/reset-password").status_code == 200
    )
    assert client.patch(f"{USERS_URL}/1", json={"name": "新名"}).status_code == 400
    assert client.post(f"{USERS_URL}/1/reset-password").status_code == 400

    client.post("/api/v1/auth/logout")
    _login_student(client)
    assert client.patch(f"{USERS_URL}/1", json={"name": "新名"}).status_code == 403
    assert client.post(f"{USERS_URL}/1/reset-password").status_code == 403


def test_history_options_include_disabled_students(teacher_client: TestClient, db_session) -> None:
    disabled_id = _user_id(db_session, "disabled01")

    response = teacher_client.get(f"{USERS_URL}/history-options")

    assert response.status_code == 200
    options = {option["id"]: option for option in response.json()}
    assert disabled_id in options
    assert options[disabled_id]["status"] == "disabled"
    assert options[disabled_id]["account"] == "disabled01"
    assert set(options[disabled_id].keys()) == {"id", "account", "name", "role", "status"}

    active_response = teacher_client.get(f"{USERS_URL}/options")
    active_ids = {option["id"] for option in active_response.json()}
    assert disabled_id not in active_ids


def test_history_options_forbidden_for_student(student_client: TestClient) -> None:
    assert student_client.get(f"{USERS_URL}/history-options").status_code == 403


def test_rename_keeps_history_owned_by_same_user(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client)
    student_id = _user_id(db_session, "student01")
    _login_admin(admin_client)
    created = _submit(
        admin_client,
        [{"consumable_id": consumable["id"], "quantity": 2}],
        user_id=student_id,
    )
    assert created.status_code == 201, created.text

    response = admin_client.patch(
        f"{USERS_URL}/{student_id}", json={"name": "张同学（更正）"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "张同学（更正）"

    records = admin_client.get(REQUISITIONS_URL, params={"user_id": student_id}).json()
    assert records["total"] == 1
    assert records["items"][0]["user_id"] == student_id
    assert records["items"][0]["user_name"] == "张同学（更正）"

    log = db_session.scalars(
        select(OperationLog).where(OperationLog.action == "user_update")
    ).one()
    assert log.target_id == student_id
    assert json.loads(log.before_value)["name"] == "张同学"
    assert json.loads(log.after_value)["name"] == "张同学（更正）"


def test_rename_account_keeps_login_working(admin_client: TestClient, db_session) -> None:
    student_id = _user_id(db_session, "student01")

    response = admin_client.patch(
        f"{USERS_URL}/{student_id}", json={"account": "student-renamed"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["account"] == "student-renamed"
    assert _user(db_session, "student-renamed").id == student_id
    admin_client.post("/api/v1/auth/logout")
    _login(admin_client, "student-renamed", "Student@123")


def test_rename_rejects_duplicate_account(admin_client: TestClient, db_session) -> None:
    student_id = _user_id(db_session, "student01")
    other_id = _user_id(db_session, "disabled01")

    response = admin_client.patch(
        f"{USERS_URL}/{student_id}", json={"account": "disabled01"}
    )

    assert response.status_code == 400
    assert "已被其他人员使用" in response.json()["detail"]
    assert _user(db_session, "student01").id == student_id
    assert _user(db_session, "disabled01").id == other_id


def test_rename_rejects_blank_and_spaced_account(admin_client: TestClient, db_session) -> None:
    student_id = _user_id(db_session, "student01")

    blank = admin_client.patch(f"{USERS_URL}/{student_id}", json={"account": "  "})
    spaced = admin_client.patch(f"{USERS_URL}/{student_id}", json={"account": "a b"})

    assert blank.status_code == 400
    assert spaced.status_code == 400
    assert "空白" in spaced.json()["detail"]


def test_rename_only_applies_to_students(admin_client: TestClient, db_session) -> None:
    teacher_id = _user_id(db_session, "teacher01")

    response = admin_client.patch(
        f"{USERS_URL}/{teacher_id}", json={"name": "改名教师"}
    )

    assert response.status_code == 400
    assert "学生" in response.json()["detail"]


def test_same_name_students_are_distinguished_and_keep_own_history(
    admin_client: TestClient, db_session
) -> None:
    same_named = _create_student(admin_client, "student02", "张同学")
    first_id = _user_id(db_session, "student01")
    second_id = same_named["id"]
    consumable = _create_consumable(admin_client)
    _login_admin(admin_client)

    for user_id in (first_id, second_id):
        created = _submit(
            admin_client,
            [{"consumable_id": consumable["id"], "quantity": 1}],
            user_id=user_id,
        )
        assert created.status_code == 201, created.text

    options = admin_client.get(f"{USERS_URL}/history-options").json()
    same_named_options = [
        option
        for option in options
        if option["name"] == "张同学" and option["role"] == "student"
    ]
    assert {option["account"] for option in same_named_options} == {"student01", "student02"}
    assert {option["id"] for option in same_named_options} == {first_id, second_id}

    admin_client.patch(f"{USERS_URL}/{first_id}", json={"name": "张同学（一）"})
    admin_client.patch(f"{USERS_URL}/{second_id}", json={"name": "张同学（二）"})

    first_records = admin_client.get(
        REQUISITIONS_URL, params={"user_id": first_id}
    ).json()
    second_records = admin_client.get(
        REQUISITIONS_URL, params={"user_id": second_id}
    ).json()
    assert first_records["items"][0]["user_name"] == "张同学（一）"
    assert second_records["items"][0]["user_name"] == "张同学（二）"


def test_disabled_student_history_visible_but_proxy_submission_rejected(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client)
    disabled_id = _user_id(db_session, "disabled01")
    _login_admin(admin_client)
    admin_client.patch(f"{USERS_URL}/{disabled_id}/status", json={"status": "enabled"})
    created = _submit(
        admin_client,
        [{"consumable_id": consumable["id"], "quantity": 1}],
        user_id=disabled_id,
    )
    assert created.status_code == 201, created.text
    admin_client.patch(f"{USERS_URL}/{disabled_id}/status", json={"status": "disabled"})

    _login_teacher(admin_client)
    records = admin_client.get(
        REQUISITIONS_URL, params={"user_id": disabled_id}
    ).json()
    assert records["total"] == 1
    assert records["items"][0]["user_id"] == disabled_id
    assert records["items"][0]["user_name"] == "赵停用"

    rejected = _submit(
        admin_client,
        [{"consumable_id": consumable["id"], "quantity": 1}],
        user_id=disabled_id,
    )
    assert rejected.status_code == 400
    assert "停用" in json.dumps(rejected.json(), ensure_ascii=False)


def test_reset_password_rotates_credential_and_invalidates_old_session(
    client: TestClient, db_session
) -> None:
    _login_student(client)
    old_token = client.cookies.get("access_token")
    student_id = _user_id(db_session, "student01")

    client.post("/api/v1/auth/logout")
    _login_admin(client)
    response = client.post(f"{USERS_URL}/{student_id}/reset-password")

    assert response.status_code == 200, response.text
    data = response.json()
    temporary_password = data["temporary_password"]
    assert len(temporary_password) >= 12
    assert data["user"]["id"] == student_id
    assert "password_hash" not in data["user"]
    log = db_session.scalars(
        select(OperationLog).where(OperationLog.action == "user_password_reset")
    ).one()
    assert temporary_password not in (log.before_value or "")
    assert temporary_password not in (log.after_value or "")
    assert temporary_password not in (log.remark or "")

    client.cookies.set("access_token", old_token)
    assert client.get("/api/v1/auth/me").status_code == 401
    client.cookies.clear()

    stale = client.post(
        "/api/v1/auth/login",
        json={"account": "student01", "password": "Student@123"},
    )
    assert stale.status_code == 401

    fresh = client.post(
        "/api/v1/auth/login",
        json={"account": "student01", "password": temporary_password},
    )
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["role"] == "student"
    session = client.get("/api/v1/auth/session").json()
    assert session["must_change_password"] is True


def test_reset_password_rejects_non_student_and_self(
    admin_client: TestClient, db_session
) -> None:
    teacher_id = _user_id(db_session, "teacher01")
    admin_id = _user_id(db_session, "admin01")

    teacher_response = admin_client.post(f"{USERS_URL}/{teacher_id}/reset-password")
    self_response = admin_client.post(f"{USERS_URL}/{admin_id}/reset-password")

    assert teacher_response.status_code == 400
    assert self_response.status_code == 400
    assert "自己" in self_response.json()["detail"]


def test_active_options_detail_exposes_account_for_same_name_selection(
    admin_client: TestClient, db_session
) -> None:
    _create_student(admin_client, "student02", "张同学")

    response = admin_client.get(f"{USERS_URL}/options", params={"detail": "true"})

    assert response.status_code == 200
    options = response.json()
    assert all("account" in option for option in options)
    same_named = [
        option
        for option in options
        if option["name"] == "张同学" and option["role"] == "student"
    ]
    assert {option["account"] for option in same_named} == {"student01", "student02"}
    disabled_id = _user_id(db_session, "disabled01")
    assert all(option["id"] != disabled_id for option in options)
