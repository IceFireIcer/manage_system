from datetime import datetime

from app.models import Consumable, IncidentReport, InventoryCount, InventoryEvent, User
from fastapi.testclient import TestClient
from sqlalchemy import func, select

BASE_URL = "/api/v1/incidents"
CONSUMABLES_URL = "/api/v1/consumables"
COUNTS_URL = "/api/v1/inventory/counts"


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


def _create_consumable(
    admin_client: TestClient, *, name: str = "焊丝", current_stock: int = 10
) -> dict:
    response = admin_client.post(
        CONSUMABLES_URL,
        json={
            "name": name,
            "spec": "φ1.2mm",
            "unit": "卷",
            "location": "一号柜",
            "safety_stock": 5,
            "current_stock": current_stock,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _submit(client: TestClient, consumable_id: int) -> dict:
    response = client.post(
        BASE_URL,
        data={
            "consumable_id": str(consumable_id),
            "description": "柜内数量与系统不符",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _convert(client: TestClient, incident_id: int) -> "object":
    return client.post(f"{BASE_URL}/{incident_id}/convert-to-count")


def _event_count(db_session) -> int:
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(InventoryEvent))


def _stock(db_session, consumable_id: int) -> int:
    db_session.expire_all()
    return db_session.get(Consumable, consumable_id).current_stock


def test_open_incident_converts_to_single_open_count(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    baseline_events = _event_count(db_session)
    _login_student(admin_client)
    incident = _submit(admin_client, consumable["id"])

    _login_teacher(admin_client)
    response = _convert(admin_client, incident["id"])
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["status"] == "converted_to_count"
    assert data["linked_count_id"] is not None
    assert data["count_order_no"]
    assert data["count_status"] == "open"

    fetched = admin_client.get(f"{BASE_URL}/{incident['id']}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["linked_count_id"] == data["linked_count_id"]
    assert fetched.json()["count_order_no"] == data["count_order_no"]

    count = admin_client.get(f"{COUNTS_URL}/{data['count_order_no']}")
    assert count.status_code == 200, count.text
    count_body = count.json()
    assert count_body["status"] == "open"
    assert count_body["incident_id"] == incident["id"]
    assert len(count_body["lines"]) == 1
    assert count_body["lines"][0]["consumable_id"] == consumable["id"]
    assert count_body["lines"][0]["counted_qty"] is None
    assert count_body["lines"][0]["baseline_stock"] == 10

    assert _stock(db_session, consumable["id"]) == 10
    assert _event_count(db_session) == baseline_events


def test_convert_is_idempotent_after_confirm(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    baseline_events = _event_count(db_session)
    _login_student(admin_client)
    incident = _submit(admin_client, consumable["id"])

    _login_teacher(admin_client)
    confirmed = admin_client.patch(
        f"{BASE_URL}/{incident['id']}", json={"action": "confirm"}
    )
    assert confirmed.status_code == 200, confirmed.text

    first = _convert(admin_client, incident["id"])
    assert first.status_code == 201, first.text
    second = _convert(admin_client, incident["id"])
    assert second.status_code == 200, second.text
    assert second.json()["linked_count_id"] == first.json()["linked_count_id"]
    assert second.json()["count_order_no"] == first.json()["count_order_no"]

    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(InventoryCount)) == 1
    report = db_session.get(IncidentReport, incident["id"])
    assert report.status == "converted_to_count"
    assert report.linked_count_id == first.json()["linked_count_id"]
    assert _event_count(db_session) == baseline_events

    filtered = admin_client.get(
        BASE_URL, params={"status": "converted_to_count"}
    )
    assert filtered.status_code == 200, filtered.text
    assert [item["id"] for item in filtered.json()["items"]] == [incident["id"]]


def test_rejected_incident_cannot_convert(admin_client: TestClient) -> None:
    consumable = _create_consumable(admin_client)
    _login_student(admin_client)
    incident = _submit(admin_client, consumable["id"])

    _login_teacher(admin_client)
    rejected = admin_client.patch(
        f"{BASE_URL}/{incident['id']}",
        json={"action": "reject", "reject_reason": "信息不足"},
    )
    assert rejected.status_code == 200, rejected.text

    response = _convert(admin_client, incident["id"])
    assert response.status_code == 409
    assert response.json()["detail"] == "当前状态不可转盘点"


def test_student_cannot_convert(admin_client: TestClient) -> None:
    consumable = _create_consumable(admin_client)
    _login_student(admin_client)
    incident = _submit(admin_client, consumable["id"])

    assert _convert(admin_client, incident["id"]).status_code == 403


def test_cross_class_incident_cannot_convert(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client)
    other = Consumable(
        name="外班耗材",
        spec="",
        unit="件",
        location="二号柜",
        safety_stock=1,
        current_stock=5,
        class_id=2,
        status="enabled",
    )
    db_session.add(other)
    db_session.commit()
    reporter_id = db_session.scalars(
        select(User).where(User.account == "student01")
    ).one().id
    foreign = IncidentReport(
        class_id=2,
        consumable_id=other.id,
        reporter_id=reporter_id,
        reporter_name="外班同学",
        reporter_role="student",
        location="二号柜",
        description="外班异常",
        status="open",
        created_at=datetime.now().replace(microsecond=0),
        updated_at=datetime.now().replace(microsecond=0),
    )
    db_session.add(foreign)
    db_session.commit()

    _login_teacher(admin_client)
    assert _convert(admin_client, foreign.id).status_code == 404
    assert admin_client.get(f"{BASE_URL}/{foreign.id}").status_code == 404
    assert consumable["id"] > 0


def test_posted_count_derives_incident_status(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    baseline_events = _event_count(db_session)
    _login_student(admin_client)
    incident = _submit(admin_client, consumable["id"])

    _login_teacher(admin_client)
    converted = _convert(admin_client, incident["id"])
    assert converted.status_code == 201, converted.text
    order_no = converted.json()["count_order_no"]

    _login_admin(admin_client)
    updated = admin_client.patch(
        f"{COUNTS_URL}/{order_no}/lines",
        json={
            "lines": [{"consumable_id": consumable["id"], "counted_qty": 8}]
        },
    )
    assert updated.status_code == 200, updated.text

    posted = admin_client.post(f"{COUNTS_URL}/{order_no}/post")
    assert posted.status_code == 201, posted.text
    assert posted.json()["status"] == "posted"
    assert posted.json()["incident_id"] == incident["id"]

    _login_teacher(admin_client)
    fetched = admin_client.get(f"{BASE_URL}/{incident['id']}")
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert body["status"] == "converted_to_count"
    assert body["count_status"] == "posted"
    assert body["count_order_no"] == order_no

    assert _stock(db_session, consumable["id"]) == 8
    assert _event_count(db_session) == baseline_events + 1

    again = admin_client.patch(
        f"{BASE_URL}/{incident['id']}", json={"action": "confirm"}
    )
    assert again.status_code == 409
