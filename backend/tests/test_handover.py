from datetime import datetime

import pytest
from app.models import (
    Consumable,
    IncidentReport,
    InventoryCount,
    InventoryEvent,
    RequisitionOrder,
    RestockOrder,
    StockAlert,
    User,
)
from app.security import hash_password
from fastapi.testclient import TestClient
from sqlalchemy import select

BASE_URL = "/api/v1/handover/checklists"
SENSITIVE_KEYS = {
    "reporter_id",
    "reporter_name",
    "reporter_account",
    "owner_id",
    "owner_name",
    "owner_account",
}


def _login(client: TestClient, account: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"account": account, "password": password}
    )
    assert response.status_code == 200, response.text


def _user(db_session, account: str) -> User:
    return db_session.scalars(select(User).where(User.account == account)).one()


def _now() -> datetime:
    return datetime(2026, 11, 28, 10, 0, 0)


def _consumable(db_session, *, current_stock: int = 2, class_id: int = 1) -> Consumable:
    consumable = Consumable(
        name="焊丝",
        spec="φ1.2mm",
        unit="卷",
        location="一号柜",
        safety_stock=5,
        current_stock=current_stock,
        class_id=class_id,
        status="enabled",
        created_at=_now(),
        updated_at=_now(),
    )
    db_session.add(consumable)
    db_session.commit()
    return consumable


def _stock_alert(db_session, consumable: Consumable) -> StockAlert:
    alert = StockAlert(
        class_id=consumable.class_id,
        consumable_id=consumable.id,
        status="open",
        risk_level="low",
        current_stock=consumable.current_stock,
        safety_stock=consumable.safety_stock,
        gap=3,
        occurrence=1,
        detection_count=1,
        first_detected_at=_now(),
        last_detected_at=_now(),
        created_at=_now(),
        updated_at=_now(),
    )
    db_session.add(alert)
    db_session.commit()
    return alert


def _restock_order(db_session, consumable: Consumable, operator: User) -> RestockOrder:
    order = RestockOrder(
        order_no="RK-OPEN-001",
        request_id="req-restock-open-001",
        status="processing",
        operator_id=operator.id,
        operator_name=operator.name,
        operator_role=operator.role,
        class_id=operator.class_id,
        business_at=_now(),
        items_signature="[]",
        created_at=_now(),
        updated_at=_now(),
    )
    db_session.add(order)
    db_session.commit()
    return order


def _requisition_order(
    db_session, consumable: Consumable, owner: User, operator: User
) -> RequisitionOrder:
    order = RequisitionOrder(
        order_no="LQ-OPEN-001",
        request_id="req-requisition-open-001",
        status="processing",
        owner_id=owner.id,
        owner_name=owner.name,
        operator_id=operator.id,
        operator_name=operator.name,
        operator_role=operator.role,
        class_id=owner.class_id,
        business_at=_now(),
        items_signature="[]",
        created_at=_now(),
        updated_at=_now(),
    )
    db_session.add(order)
    db_session.commit()
    return order


def _incident(db_session, consumable: Consumable, reporter: User) -> IncidentReport:
    report = IncidentReport(
        class_id=reporter.class_id,
        consumable_id=consumable.id,
        reporter_id=reporter.id,
        reporter_name=reporter.name,
        reporter_role=reporter.role,
        location="一号柜",
        description="柜门损坏，耗材散落",
        status="open",
        created_at=_now(),
        updated_at=_now(),
    )
    db_session.add(report)
    db_session.commit()
    return report


def _inventory_count(db_session, operator: User) -> InventoryCount:
    count = InventoryCount(
        order_no="PD-OPEN-001",
        request_id="req-count-open-001",
        status="open",
        count_at=_now(),
        counter_id=operator.id,
        counter_name=operator.name,
        counter_role=operator.role,
        reviewer_id=operator.id,
        reviewer_name=operator.name,
        reviewer_role=operator.role,
        operator_id=operator.id,
        operator_name=operator.name,
        operator_role=operator.role,
        class_id=operator.class_id,
        created_at=_now(),
        updated_at=_now(),
    )
    db_session.add(count)
    db_session.commit()
    return count


@pytest.fixture()
def seeded(db_session):
    admin = _user(db_session, "admin01")
    student = _user(db_session, "student01")
    consumable = _consumable(db_session)
    alert = _stock_alert(db_session, consumable)
    restock = _restock_order(db_session, consumable, admin)
    requisition = _requisition_order(db_session, consumable, student, admin)
    incident = _incident(db_session, consumable, student)
    count = _inventory_count(db_session, admin)
    return {
        "admin": admin,
        "student": student,
        "consumable": consumable,
        "alert": alert,
        "restock": restock,
        "requisition": requisition,
        "incident": incident,
        "count": count,
    }


def _create(admin_client: TestClient, payload: dict | None = None) -> dict:
    response = admin_client.post(BASE_URL, json=payload or {})
    assert response.status_code == 201, response.text
    return response.json()


def test_requires_login(client: TestClient) -> None:
    assert client.post(BASE_URL, json={}).status_code == 401
    assert client.get(BASE_URL).status_code == 401


def test_student_forbidden(student_client: TestClient) -> None:
    assert student_client.post(BASE_URL, json={}).status_code == 403
    assert student_client.get(BASE_URL).status_code == 403


def test_create_checklist_snapshots_all_sources(
    admin_client: TestClient, seeded
) -> None:
    data = _create(admin_client)

    assert data["version"] == 1
    assert data["status"] == "pending"
    assert data["outgoing_name"] == "李班委"
    assert data["outgoing_account"] == "admin01"
    assert data["item_count"] == len(data["items"])
    assert data["confirmed_count"] == 0

    kinds = {item["kind"] for item in data["items"]}
    assert {
        "stock",
        "restock",
        "requisition",
        "suggestion",
        "incident",
        "count",
    } <= kinds

    for item in data["items"]:
        assert SENSITIVE_KEYS.isdisjoint(item["payload"].keys())
    assert "张同学" not in admin_client.get(
        f"{BASE_URL}/{data['id']}"
    ).text

    page = admin_client.get(BASE_URL, params={"page": 1, "page_size": 10})
    assert page.status_code == 200
    body = page.json()
    assert body["total"] == 1
    assert body["items"][0]["item_count"] == data["item_count"]


def test_snapshot_frozen_and_sources_unchanged(
    admin_client: TestClient, db_session, seeded
) -> None:
    consumable = seeded["consumable"]
    incident = seeded["incident"]
    restock = seeded["restock"]
    requisition = seeded["requisition"]
    count = seeded["count"]
    stock_before = consumable.current_stock

    created = _create(admin_client)
    payload_before = [
        (item["kind"], item["label"], item["payload"]) for item in created["items"]
    ]

    db_session.refresh(consumable)
    db_session.refresh(incident)
    db_session.refresh(restock)
    db_session.refresh(requisition)
    db_session.refresh(count)
    assert consumable.current_stock == stock_before
    assert incident.status == "open"
    assert restock.status == "processing"
    assert requisition.status == "processing"
    assert count.status == "open"

    consumable.current_stock = 999
    db_session.add(
        IncidentReport(
            class_id=1,
            consumable_id=consumable.id,
            reporter_id=seeded["student"].id,
            reporter_name=seeded["student"].name,
            reporter_role="student",
            location="二号柜",
            description="后续新增异常",
            status="open",
            created_at=_now(),
            updated_at=_now(),
        )
    )
    db_session.commit()

    response = admin_client.get(f"{BASE_URL}/{created['id']}")
    assert response.status_code == 200
    payload_after = [
        (item["kind"], item["label"], item["payload"])
        for item in response.json()["items"]
    ]
    assert payload_after == payload_before


def test_confirm_items_then_checklist(
    admin_client: TestClient, seeded, db_session
) -> None:
    created = _create(
        admin_client, {"incoming_account": "teacher01"}
    )
    assert created["incoming_name"] == "王老师"
    assert created["incoming_account"] == "teacher01"

    items = created["items"]
    assert items
    first_item = items[0]
    url = f"{BASE_URL}/{created['id']}/items/{first_item['id']}/confirm"
    response = admin_client.post(url)
    assert response.status_code == 200, response.text
    confirmed = response.json()
    assert confirmed["confirmed"] is True
    assert confirmed["confirmed_by_name"] == "李班委"
    assert confirmed["confirmed_at"] is not None

    assert admin_client.post(url).status_code == 200

    blocked = admin_client.post(f"{BASE_URL}/{created['id']}/confirm")
    assert blocked.status_code == 400
    assert "未确认" in blocked.json()["detail"]

    for item in items[1:]:
        assert (
            admin_client.post(
                f"{BASE_URL}/{created['id']}/items/{item['id']}/confirm"
            ).status_code
            == 200
        )

    response = admin_client.post(f"{BASE_URL}/{created['id']}/confirm")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "confirmed"
    assert body["confirmed_by_account"] == "admin01"
    assert body["confirmed_at"] is not None
    assert body["confirmed_count"] == body["item_count"]

    assert admin_client.post(url).status_code == 409
    assert admin_client.post(f"{BASE_URL}/{created['id']}/confirm").status_code == 200


def test_class_isolation_and_incoming_validation(
    admin_client: TestClient, db_session, seeded
) -> None:
    created = _create(admin_client)

    other = User(
        account="admin02",
        password_hash=hash_password("Admin@123"),
        name="外班班委",
        role="admin",
        class_id=2,
        status="enabled",
    )
    db_session.add(other)
    db_session.commit()

    assert (
        admin_client.get(f"{BASE_URL}/{created['id']}").status_code == 200
    )
    _login(admin_client, "admin02", "Admin@123")
    assert admin_client.get(f"{BASE_URL}/{created['id']}").status_code == 404
    assert admin_client.get(BASE_URL).json()["total"] == 0
    assert admin_client.post(BASE_URL, json={}).status_code == 201

    missing = admin_client.post(BASE_URL, json={"incoming_account": "nobody"})
    assert missing.status_code == 400


def test_teacher_can_create_and_empty_checklist_confirms(
    teacher_client: TestClient, db_session
) -> None:
    created = _create(teacher_client)
    assert created["outgoing_name"] == "王老师"
    assert created["item_count"] == 0

    response = teacher_client.post(f"{BASE_URL}/{created['id']}/confirm")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "confirmed"


def test_checklist_does_not_write_inventory_events(
    admin_client: TestClient, db_session, seeded
) -> None:
    before = db_session.scalars(select(InventoryEvent)).all()
    _create(admin_client)
    after = db_session.scalars(select(InventoryEvent)).all()
    assert len(after) == len(before)
