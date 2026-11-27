"""操作日志（T09）测试：覆盖全部写操作、筛选、分页、只读与敏感字段隔离。"""

from datetime import datetime, timedelta

from app.models import OperationLog, User
from fastapi.testclient import TestClient
from sqlalchemy import select

LOGS_URL = "/api/v1/logs"
CONSUMABLES_URL = "/api/v1/consumables"
USERS_URL = "/api/v1/users"
REQUISITIONS_URL = "/api/v1/requisitions"


def _login(client: TestClient, account: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"account": account, "password": password}
    )
    assert response.status_code == 200, response.text


def _user_id(db_session, account: str) -> int:
    return db_session.scalars(select(User).where(User.account == account)).one().id


def _consumable_payload(name: str = "焊丝") -> dict:
    return {
        "name": name,
        "spec": "φ1.2mm",
        "unit": "卷",
        "location": "一号柜",
        "safety_stock": 5,
        "current_stock": 100,
    }


def _create_consumable(admin_client: TestClient, name: str = "焊丝") -> dict:
    response = admin_client.post(CONSUMABLES_URL, json=_consumable_payload(name))
    assert response.status_code == 201, response.text
    return response.json()


def test_logs_requires_login(client: TestClient) -> None:
    assert client.get(LOGS_URL).status_code == 401


def test_logs_forbidden_for_student(student_client: TestClient) -> None:
    response = student_client.get(LOGS_URL)

    assert response.status_code == 403


def test_logs_visible_for_teacher(teacher_client: TestClient) -> None:
    response = teacher_client.get(LOGS_URL)

    assert response.status_code == 200
    assert response.json()["page"] == 1
    assert response.json()["page_size"] == 20


def test_login_and_logout_are_logged(admin_client: TestClient) -> None:
    assert admin_client.post("/api/v1/auth/logout").status_code == 200
    _login(admin_client, "admin01", "Admin@123")

    login_logs = admin_client.get(LOGS_URL, params={"action": "login"}).json()
    logout_logs = admin_client.get(LOGS_URL, params={"action": "logout"}).json()

    assert login_logs["total"] == 2
    assert logout_logs["total"] == 1
    assert logout_logs["items"][0]["operator_name"] == "李班委"
    assert logout_logs["items"][0]["role"] == "admin"


def test_consumable_lifecycle_logged_with_snapshots(admin_client: TestClient) -> None:
    item = _create_consumable(admin_client)
    updated = admin_client.put(
        f"{CONSUMABLES_URL}/{item['id']}",
        json={"name": "镀锌焊丝", "safety_stock": 8},
    )
    assert updated.status_code == 200, updated.text
    disabled = admin_client.patch(
        f"{CONSUMABLES_URL}/{item['id']}/status", json={"status": "disabled"}
    )
    assert disabled.status_code == 200, disabled.text
    enabled = admin_client.patch(
        f"{CONSUMABLES_URL}/{item['id']}/status", json={"status": "enabled"}
    )
    assert enabled.status_code == 200, enabled.text

    data = admin_client.get(LOGS_URL, params={"action": "consumable"}).json()

    assert data["total"] == 4
    by_action = {row["action"]: row for row in data["items"]}
    assert {"consumable_create", "consumable_update"} <= set(by_action)
    assert {"consumable_disable", "consumable_enable"} <= set(by_action)
    assert by_action["consumable_create"]["before_value"] is None
    assert "镀锌焊丝" in by_action["consumable_update"]["after_value"]
    assert "焊丝" in by_action["consumable_update"]["before_value"]
    assert by_action["consumable_disable"]["after_value"] is not None


def test_user_create_logged_without_password(admin_client: TestClient) -> None:
    created = admin_client.post(
        USERS_URL,
        json={
            "account": "student02",
            "password": "Student@123",
            "name": "王二",
            "role": "student",
        },
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]
    disabled = admin_client.patch(
        f"{USERS_URL}/{user_id}/status", json={"status": "disabled"}
    )
    assert disabled.status_code == 200, disabled.text

    data = admin_client.get(LOGS_URL, params={"action": "user_"}).json()
    by_action = {row["action"]: row for row in data["items"]}

    create_log = by_action["user_create"]
    assert "student02" in create_log["after_value"]
    assert "王二" in create_log["after_value"]
    assert "student" in create_log["after_value"]
    assert "Student@123" not in create_log["after_value"]
    assert by_action["user_disable"]["before_value"] == "enabled"
    assert by_action["user_disable"]["after_value"] == "disabled"


def test_requisition_reversal_and_restock_logged(admin_client: TestClient, db_session) -> None:
    item = _create_consumable(admin_client)
    student_id = _user_id(db_session, "student01")
    created_at = (datetime.now() - timedelta(minutes=1)).replace(microsecond=0)
    issued = admin_client.post(
        REQUISITIONS_URL,
        json={
            "user_id": student_id,
            "created_at": created_at.isoformat(),
            "items": [{"consumable_id": item["id"], "quantity": 4}],
        },
    )
    assert issued.status_code == 201, issued.text
    listed = admin_client.get(
        REQUISITIONS_URL, params={"consumable_id": item["id"], "page_size": 100}
    ).json()
    record_id = listed["items"][0]["id"]
    reversed_response = admin_client.post(
        f"{REQUISITIONS_URL}/{record_id}/reversal", json={"reason": "登记错误"}
    )
    assert reversed_response.status_code == 200, reversed_response.text
    restock = admin_client.post(
        "/api/v1/inventory/restock",
        json={"consumable_id": item["id"], "quantity": 6},
    )
    assert restock.status_code == 200, restock.text

    for action in ("requisition_create", "requisition_reverse", "restock"):
        data = admin_client.get(LOGS_URL, params={"action": action}).json()
        assert data["total"] >= 1, action

    restock_log = admin_client.get(LOGS_URL, params={"action": "restock"}).json()["items"][0]
    assert "current_stock" in restock_log["before_value"]
    assert "quantity" in restock_log["after_value"]


def test_logs_filter_by_operator(admin_client: TestClient, db_session) -> None:
    teacher_id = _user_id(db_session, "teacher01")
    _login(admin_client, "teacher01", "Teacher@123")

    data = admin_client.get(LOGS_URL, params={"operator_id": teacher_id}).json()

    assert data["total"] >= 1
    assert all(row["operator_name"] == "王老师" for row in data["items"])


def test_logs_filter_by_date_range(admin_client: TestClient, db_session) -> None:
    now = datetime.now().replace(microsecond=0)
    db_session.add_all(
        [
            OperationLog(
                operator_id=None,
                role=None,
                action="seeded",
                created_at=now - timedelta(days=2),
            ),
            OperationLog(
                operator_id=None,
                role=None,
                action="seeded",
                created_at=now - timedelta(days=1),
            ),
        ]
    )
    db_session.commit()
    yesterday = (now - timedelta(days=1)).date().isoformat()
    two_days_ago = (now - timedelta(days=2)).date().isoformat()

    only_yesterday = admin_client.get(
        LOGS_URL, params={"action": "seeded", "start_date": yesterday}
    ).json()
    only_two_days_ago = admin_client.get(
        LOGS_URL, params={"action": "seeded", "end_date": two_days_ago}
    ).json()

    assert only_yesterday["total"] == 1
    assert only_two_days_ago["total"] == 1


def test_logs_pagination_and_desc_order(admin_client: TestClient, db_session) -> None:
    base = datetime.now().replace(microsecond=0) - timedelta(minutes=10)
    db_session.add_all(
        [
            OperationLog(
                operator_id=None,
                role=None,
                action="page_probe",
                created_at=base + timedelta(seconds=index),
            )
            for index in range(5)
        ]
    )
    db_session.commit()

    first_page = admin_client.get(
        LOGS_URL, params={"action": "page_probe", "page": 1, "page_size": 2}
    ).json()
    second_page = admin_client.get(
        LOGS_URL, params={"action": "page_probe", "page": 2, "page_size": 2}
    ).json()

    assert first_page["total"] == second_page["total"] == 5
    assert len(first_page["items"]) == len(second_page["items"]) == 2
    first_times = [datetime.fromisoformat(row["created_at"]) for row in first_page["items"]]
    assert first_times[0] > first_times[1]
    second_times = [datetime.fromisoformat(row["created_at"]) for row in second_page["items"]]
    assert second_times[0] > second_times[1]


def test_logs_page_size_cap(admin_client: TestClient) -> None:
    assert admin_client.get(LOGS_URL, params={"page_size": 101}).status_code == 422


def test_logs_invalid_date_rejected(admin_client: TestClient) -> None:
    assert (
        admin_client.get(LOGS_URL, params={"start_date": "2026-13-01"}).status_code == 422
    )


def test_logs_read_only(admin_client: TestClient) -> None:
    assert admin_client.post(LOGS_URL, json={}).status_code == 405


def test_logs_have_no_sensitive_fields(admin_client: TestClient) -> None:
    created = admin_client.post(
        USERS_URL,
        json={
            "account": "student03",
            "password": "Sensitive@123",
            "name": "李三",
            "role": "student",
        },
    )
    assert created.status_code == 201, created.text

    data = admin_client.get(LOGS_URL, params={"page_size": 100}).json()
    blob = " ".join(
        f"{row['before_value']} {row['after_value']} {row['remark']}"
        for row in data["items"]
    )

    assert "Sensitive@123" not in blob
    assert "password" not in blob.lower()
