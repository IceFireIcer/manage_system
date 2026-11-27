from datetime import datetime
from typing import Any

from app.models import Consumable, Requisition, User
from fastapi.testclient import TestClient
from sqlalchemy import select

OVERVIEW_URL = "/api/v1/inventory/overview"
CONSUMABLES_URL = "/api/v1/consumables"
REQUISITIONS_URL = "/api/v1/requisitions"
EXPORT_URL = "/api/v1/requisitions/export"
LOGS_URL = "/api/v1/logs"


def _consumable_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "焊丝",
        "spec": "φ1.2mm",
        "unit": "卷",
        "location": "一号柜",
        "safety_stock": 5,
        "current_stock": 100,
    }
    payload.update(overrides)
    return payload


def _create_consumable(admin_client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = admin_client.post(CONSUMABLES_URL, json=_consumable_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def _login(client: TestClient, account: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"account": account, "password": password}
    )
    assert response.status_code == 200, response.text


def _user_id(db_session, account: str) -> int:
    return db_session.scalars(select(User).where(User.account == account)).one().id


def _other_class_consumable(db_session, class_id: int = 2) -> Consumable:
    consumable = Consumable(
        name="外班耗材",
        spec="",
        unit="件",
        location="二号柜",
        safety_stock=1,
        current_stock=10,
        class_id=class_id,
        status="enabled",
    )
    db_session.add(consumable)
    db_session.commit()
    return consumable


def _other_class_requisition(db_session, class_id: int = 2) -> Requisition:
    consumable = _other_class_consumable(db_session, class_id=class_id)
    student_id = _user_id(db_session, "student01")
    record = Requisition(
        consumable_id=consumable.id,
        user_id=student_id,
        quantity=1,
        stock_after=9,
        type="normal",
        status="active",
        class_id=class_id,
        created_at=datetime.now().replace(microsecond=0),
    )
    db_session.add(record)
    db_session.commit()
    return record


def test_overview_student_has_no_summary(admin_client: TestClient) -> None:
    _create_consumable(admin_client, name="焊丝", current_stock=10)

    _login(admin_client, "student01", "Student@123")
    data = admin_client.get(OVERVIEW_URL).json()

    assert "summary" not in data
    assert [item["name"] for item in data["items"]] == ["焊丝"]


def test_overview_teacher_and_admin_have_summary(teacher_client: TestClient) -> None:
    teacher_data = teacher_client.get(OVERVIEW_URL).json()
    assert "summary" in teacher_data
    assert set(teacher_data["summary"].keys()) == {
        "total_kinds",
        "low_stock_count",
        "today_requisitions",
    }


def test_overview_student_only_authorized_class_inventory(
    admin_client: TestClient, db_session
) -> None:
    _create_consumable(admin_client, name="本班耗材", current_stock=10)
    _other_class_consumable(db_session, class_id=2)

    _login(admin_client, "student01", "Student@123")
    data = admin_client.get(OVERVIEW_URL).json()

    names = {item["name"] for item in data["items"]}
    assert names == {"本班耗材"}


def test_teacher_overview_excludes_other_class(
    admin_client: TestClient, db_session
) -> None:
    _create_consumable(admin_client, name="本班耗材", current_stock=10)
    _other_class_consumable(db_session, class_id=2)

    _login(admin_client, "teacher01", "Teacher@123")
    data = admin_client.get(OVERVIEW_URL).json()

    assert data["summary"]["total_kinds"] == 1
    assert {item["name"] for item in data["items"]} == {"本班耗材"}


def test_consumables_student_only_authorized_class(
    admin_client: TestClient, db_session
) -> None:
    _create_consumable(admin_client, name="本班耗材")
    _other_class_consumable(db_session, class_id=2)

    _login(admin_client, "student01", "Student@123")
    items = admin_client.get(CONSUMABLES_URL).json()

    assert {item["name"] for item in items} == {"本班耗材"}


def test_student_requisition_list_only_self(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client)
    student1_id = _user_id(db_session, "student01")
    student2_id = _user_id(db_session, "admin01")

    _login(admin_client, "student01", "Student@123")
    created = admin_client.post(
        REQUISITIONS_URL,
        json={"items": [{"consumable_id": consumable["id"], "quantity": 1}]},
    )
    assert created.status_code == 201, created.text

    db_session.add(
        Requisition(
            consumable_id=consumable["id"],
            user_id=student2_id,
            quantity=1,
            stock_after=98,
            type="normal",
            status="active",
            class_id=1,
            created_at=datetime.now().replace(microsecond=0),
        )
    )
    db_session.commit()

    data = admin_client.get(REQUISITIONS_URL).json()

    assert data["total"] == 1
    assert [item["user_id"] for item in data["items"]] == [student1_id]


def test_teacher_export_ok_and_scoped(admin_client: TestClient, db_session) -> None:
    _create_consumable(admin_client)
    _other_class_requisition(db_session, class_id=2)

    _login(admin_client, "teacher01", "Teacher@123")
    response = admin_client.get(EXPORT_URL)

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "外班耗材" not in content


def test_teacher_can_reverse_within_class(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)

    _login(admin_client, "student01", "Student@123")
    created = admin_client.post(
        REQUISITIONS_URL,
        json={"items": [{"consumable_id": consumable["id"], "quantity": 2}]},
    )
    assert created.status_code == 201, created.text
    record_id = db_session.scalars(
        select(Requisition).order_by(Requisition.id.desc())
    ).first().id

    _login(admin_client, "teacher01", "Teacher@123")
    response = admin_client.post(
        f"{REQUISITIONS_URL}/{record_id}/reversal", json={"reason": "登记错误"}
    )

    assert response.status_code == 200, response.text


def test_teacher_cannot_reverse_other_class(admin_client: TestClient, db_session) -> None:
    record = _other_class_requisition(db_session, class_id=2)

    _login(admin_client, "teacher01", "Teacher@123")
    response = admin_client.post(
        f"{REQUISITIONS_URL}/{record.id}/reversal", json={"reason": "越权"}
    )

    assert response.status_code == 403
    body = response.json()
    assert "consumable_name" not in body
    assert "quantity" not in body


def test_teacher_cannot_view_other_class_detail(
    admin_client: TestClient, db_session
) -> None:
    record = _other_class_requisition(db_session, class_id=2)

    _login(admin_client, "teacher01", "Teacher@123")
    response = admin_client.get(f"{REQUISITIONS_URL}/{record.id}")

    assert response.status_code == 404
    assert response.json()["detail"] == "流水不存在"


def test_teacher_logs_exclude_sensitive_audit(admin_client: TestClient) -> None:
    _login(admin_client, "admin01", "Admin@123")
    admin_client.post("/api/v1/auth/logout")
    _login(admin_client, "admin01", "Admin@123")

    _login(admin_client, "teacher01", "Teacher@123")
    data = admin_client.get(LOGS_URL, params={"page_size": 100}).json()

    actions = {row["action"] for row in data["items"]}
    assert "login" not in actions
    assert "logout" not in actions


def test_teacher_sensitive_action_filter_forbidden(admin_client: TestClient) -> None:
    _login(admin_client, "teacher01", "Teacher@123")

    assert admin_client.get(LOGS_URL, params={"action": "login"}).status_code == 403
    assert admin_client.get(LOGS_URL, params={"action": "logout"}).status_code == 403


def test_admin_logs_include_sensitive_audit(admin_client: TestClient) -> None:
    admin_client.post("/api/v1/auth/logout")
    _login(admin_client, "admin01", "Admin@123")

    data = admin_client.get(LOGS_URL, params={"action": "login"}).json()

    assert data["total"] >= 1
    assert data["items"][0]["action"] == "login"
