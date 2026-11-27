import json
from typing import Any

from app.models import User
from fastapi.testclient import TestClient
from sqlalchemy import select

USERS_URL = "/api/v1/users"
IMPORT_URL = f"{USERS_URL}/import"
PREVIEW_URL = f"{USERS_URL}/import/preview"
TEMPLATE_URL = f"{USERS_URL}/import/template"
BATCH_URL = f"{USERS_URL}/import/batches"
REQUISITIONS_URL = "/api/v1/requisitions"
CONSUMABLES_URL = "/api/v1/consumables"


def _user_id(db_session, account: str) -> int:
    db_session.expire_all()
    return db_session.scalars(select(User).where(User.account == account)).one().id


def _count_users(db_session, account: str) -> int:
    db_session.expire_all()
    return len(
        list(db_session.scalars(select(User).where(User.account == account)).all())
    )


def _login(client: TestClient, account: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"account": account, "password": password}
    )
    assert response.status_code == 200, response.text


def _login_admin(client: TestClient) -> None:
    _login(client, "admin01", "Admin@123")


def _login_teacher(client: TestClient) -> None:
    _login(client, "teacher01", "Teacher@123")


def _create_consumable(admin_client: TestClient) -> dict[str, Any]:
    response = admin_client.post(
        CONSUMABLES_URL,
        json={
            "name": "焊丝",
            "spec": "φ1.2mm",
            "unit": "卷",
            "location": "实训室一号柜",
            "safety_stock": 5,
            "current_stock": 10,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _payload(rows: list[dict], **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"source": "2026级花名册", "rows": rows}
    payload.update(extra)
    return payload


def test_roster_import_requires_admin(client: TestClient) -> None:
    rows = [{"line_no": 1, "account": "20260001", "name": "王五"}]

    _login_teacher(client)
    assert client.get(TEMPLATE_URL).status_code == 403
    assert client.post(PREVIEW_URL, json=_payload(rows)).status_code == 403
    assert client.post(IMPORT_URL, json=_payload(rows)).status_code == 403

    client.post("/api/v1/auth/logout")
    _login(client, "student01", "Student@123")
    assert client.post(PREVIEW_URL, json=_payload(rows)).status_code == 403


def test_roster_template_download(admin_client: TestClient) -> None:
    response = admin_client.get(TEMPLATE_URL)

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    text = response.content.decode("utf-8-sig")
    assert text.splitlines()[0] == "学号,姓名"


def test_preview_reports_row_level_errors(admin_client: TestClient) -> None:
    rows = [
        {"line_no": 1, "account": "20260001", "name": "王五"},
        {"line_no": 2, "account": "", "name": "缺学号"},
        {"line_no": 3, "account": "20260003", "name": "   "},
        {"line_no": 4, "account": "20260001", "name": "批内重复"},
        {"line_no": 5, "account": "a b", "name": "含空格"},
        {"line_no": 6, "account": "20260006", "name": "李" * 70},
    ]

    response = admin_client.post(PREVIEW_URL, json=_payload(rows))

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["valid"] is False
    assert data["summary"] == {
        "total_rows": 6,
        "ready_rows": 1,
        "existing_rows": 0,
        "update_rows": 0,
        "error_rows": 5,
    }
    by_line = {row["line_no"]: row for row in data["rows"]}
    assert by_line[1]["status"] == "ready"
    assert by_line[2]["status"] == "error"
    assert by_line[2]["errors"][0]["field"] == "account"
    assert by_line[3]["errors"][0]["field"] == "name"
    assert by_line[4]["errors"][0]["code"] == "duplicate_account_in_file"
    assert by_line[5]["errors"][0]["code"] == "invalid_account"
    assert by_line[6]["errors"][0]["field"] == "name"


def test_preview_flags_same_name_but_keeps_row_ready(
    admin_client: TestClient, db_session
) -> None:
    student_id = _user_id(db_session, "student01")

    response = admin_client.post(
        PREVIEW_URL,
        json=_payload([{"line_no": 1, "account": "20260001", "name": "张同学"}]),
    )

    assert response.status_code == 200
    row = response.json()["rows"][0]
    assert row["status"] == "ready"
    assert student_id in row["same_name_ids"]
    assert "同名" in row["message"]


def test_preview_marks_existing_and_update_modes(
    admin_client: TestClient, db_session
) -> None:
    student_id = _user_id(db_session, "student01")
    rows = [{"line_no": 1, "account": "student01", "name": "张同学更正"}]

    skip = admin_client.post(PREVIEW_URL, json=_payload(rows, on_existing="skip"))
    update = admin_client.post(PREVIEW_URL, json=_payload(rows, on_existing="update"))

    skip_row = skip.json()["rows"][0]
    update_row = update.json()["rows"][0]
    assert skip_row["status"] == "existing"
    assert skip_row["existing_user_id"] == student_id
    assert update_row["status"] == "update"
    assert update_row["existing_user_id"] == student_id


def test_preview_rejects_staff_or_other_class_accounts(
    admin_client: TestClient, db_session
) -> None:
    teacher = db_session.scalars(
        select(User).where(User.account == "teacher01")
    ).one()
    teacher.class_id = 2
    other = User(
        account="otherclass01",
        password_hash="x",
        name="外班同学",
        role="student",
        class_id=2,
        status="enabled",
    )
    db_session.add(teacher)
    db_session.add(other)
    db_session.commit()

    response = admin_client.post(
        PREVIEW_URL,
        json=_payload(
            [
                {"line_no": 1, "account": "teacher01", "name": "教师"},
                {"line_no": 2, "account": "otherclass01", "name": "外班同学"},
            ]
        ),
    )

    data = response.json()
    assert data["rows"][0]["errors"][0]["code"] == "account_taken_by_staff"
    assert data["rows"][1]["errors"][0]["code"] == "account_in_other_class"


def test_import_creates_students_and_returns_temporary_passwords(
    admin_client: TestClient, db_session
) -> None:
    rows = [
        {"line_no": 1, "account": "20260001", "name": "王五"},
        {"line_no": 2, "account": "20260002", "name": "李四"},
    ]

    response = admin_client.post(IMPORT_URL, json=_payload(rows, request_id="batch-a"))

    assert response.status_code == 201, response.text
    receipt = response.json()
    assert receipt["summary"]["ready_rows"] == 2
    assert receipt["summary"]["error_rows"] == 0
    assert receipt["request_id"] == "batch-a"
    passwords = {
        row["account"]: row["temporary_password"] for row in receipt["rows"]
    }
    assert len(passwords) == 2
    assert all(password for password in passwords.values())

    user = db_session.scalars(select(User).where(User.account == "20260001")).one()
    assert user.must_change_password is True
    assert user.status == "enabled"
    assert user.role == "student"

    admin_client.post("/api/v1/auth/logout")
    _login(admin_client, "20260001", passwords["20260001"])
    session = admin_client.get("/api/v1/auth/session").json()
    assert session["must_change_password"] is True


def test_same_request_id_retry_is_idempotent(admin_client: TestClient, db_session) -> None:
    rows = [{"line_no": 1, "account": "20260001", "name": "王五"}]
    first = admin_client.post(IMPORT_URL, json=_payload(rows, request_id="retry-1"))
    assert first.status_code == 201

    retry = admin_client.post(IMPORT_URL, json=_payload(rows, request_id="retry-1"))

    assert retry.status_code == 200
    assert retry.json()["batch_id"] == first.json()["batch_id"]
    assert retry.json()["summary"]["ready_rows"] == 1
    assert _count_users(db_session, "20260001") == 1


def test_new_request_id_retry_skips_existing_accounts(
    admin_client: TestClient, db_session
) -> None:
    rows = [{"line_no": 1, "account": "20260001", "name": "王五"}]
    admin_client.post(IMPORT_URL, json=_payload(rows, request_id="retry-2a"))

    retry = admin_client.post(IMPORT_URL, json=_payload(rows, request_id="retry-2b"))

    assert retry.status_code == 201
    assert retry.json()["summary"]["ready_rows"] == 0
    assert retry.json()["summary"]["existing_rows"] == 1
    assert retry.json()["rows"][0]["temporary_password"] is None
    assert _count_users(db_session, "20260001") == 1


def test_request_id_reuse_with_different_content_conflicts(admin_client: TestClient) -> None:
    first = [{"line_no": 1, "account": "20260001", "name": "王五"}]
    second = [{"line_no": 1, "account": "20260002", "name": "李四"}]
    admin_client.post(IMPORT_URL, json=_payload(first, request_id="reuse-1"))

    response = admin_client.post(IMPORT_URL, json=_payload(second, request_id="reuse-1"))

    assert response.status_code == 409
    assert "请求标识" in response.json()["detail"]


def test_import_with_row_errors_is_rejected(admin_client: TestClient, db_session) -> None:
    rows = [
        {"line_no": 1, "account": "20260001", "name": "王五"},
        {"line_no": 2, "account": "", "name": "缺学号"},
    ]

    response = admin_client.post(IMPORT_URL, json=_payload(rows))

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["summary"]["error_rows"] == 1
    assert detail["rows"][1]["errors"][0]["field"] == "account"
    assert _count_users(db_session, "20260001") == 0


def test_import_batch_receipt_is_queryable_without_passwords(
    admin_client: TestClient, db_session
) -> None:
    rows = [{"line_no": 1, "account": "20260001", "name": "王五"}]
    created = admin_client.post(IMPORT_URL, json=_payload(rows))
    batch_id = created.json()["batch_id"]
    assert created.json()["rows"][0]["temporary_password"]

    receipt = admin_client.get(f"{BATCH_URL}/{batch_id}")

    assert receipt.status_code == 200
    assert receipt.json()["batch_id"] == batch_id
    assert receipt.json()["rows"][0]["temporary_password"] is None
    assert admin_client.get(f"{BATCH_URL}/9999").status_code == 404


def test_update_existing_roster_row_renames_by_stable_id(
    admin_client: TestClient, db_session
) -> None:
    student_id = _user_id(db_session, "student01")
    rows = [{"line_no": 1, "account": "student01", "name": "张同学（册）"}]

    response = admin_client.post(
        IMPORT_URL, json=_payload(rows, on_existing="update", request_id="rename-1")
    )

    assert response.status_code == 201, response.text
    assert response.json()["summary"]["update_rows"] == 1
    assert response.json()["summary"]["ready_rows"] == 0
    assert db_session.get(User, student_id).name == "张同学（册）"
    assert _count_users(db_session, "student01") == 1


def test_offboarded_student_history_remains_queryable(
    admin_client: TestClient, db_session
) -> None:
    rows = [{"line_no": 1, "account": "20260001", "name": "王五"}]
    created = admin_client.post(IMPORT_URL, json=_payload(rows))
    student_id = created.json()["rows"][0]["user_id"]
    consumable = _create_consumable(admin_client)
    _login_admin(admin_client)
    submitted = admin_client.post(
        REQUISITIONS_URL,
        json={
            "items": [{"consumable_id": consumable["id"], "quantity": 1}],
            "user_id": student_id,
        },
    )
    assert submitted.status_code == 201, submitted.text

    offboard = admin_client.patch(
        f"{USERS_URL}/{student_id}/status", json={"status": "disabled"}
    )
    assert offboard.status_code == 200
    assert offboard.json()["status"] == "disabled"

    listed = admin_client.get(USERS_URL, params={"status": "disabled"}).json()
    assert any(item["id"] == student_id for item in listed)

    _login_teacher(admin_client)
    history = admin_client.get(
        REQUISITIONS_URL, params={"user_id": student_id}
    ).json()
    assert history["total"] == 1
    assert history["items"][0]["user_id"] == student_id
    assert history["items"][0]["user_name"] == "王五"

    proxy = admin_client.post(
        REQUISITIONS_URL,
        json={
            "items": [{"consumable_id": consumable["id"], "quantity": 1}],
            "user_id": student_id,
        },
    )
    assert proxy.status_code == 400
    assert "停用" in json.dumps(proxy.json(), ensure_ascii=False)
