from datetime import datetime, timedelta

from app import inventory_ledger
from app.models import Consumable, InventoryEvent
from fastapi.testclient import TestClient
from sqlalchemy import select

AUDIT_URL = "/api/v1/inventory/audit"
CONSUMABLES_URL = "/api/v1/consumables"
REQUISITIONS_URL = "/api/v1/requisitions"


def _create_consumable(admin_client: TestClient, **overrides) -> dict:
    payload = {
        "name": "焊丝",
        "spec": "φ1.2mm",
        "unit": "卷",
        "location": "一号柜",
        "safety_stock": 5,
        "current_stock": 10,
    }
    payload.update(overrides)
    response = admin_client.post(CONSUMABLES_URL, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _student_id(db_session) -> int:
    from app.models import User

    return db_session.scalars(select(User).where(User.account == "student01")).one().id


def _issue(
    client: TestClient,
    db_session,
    consumable_id: int,
    quantity: int,
    *,
    created_at: datetime | None = None,
) -> dict:
    payload = {
        "user_id": _student_id(db_session),
        "items": [{"consumable_id": consumable_id, "quantity": quantity}],
    }
    if created_at is not None:
        payload["created_at"] = created_at.isoformat()
    response = client.post(REQUISITIONS_URL, json=payload)
    assert response.status_code == 201, response.text
    data = response.json()
    return data.get("receipt", data)


def _login_student(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"account": "student01", "password": "Student@123"},
    )
    assert response.status_code == 200, response.text


def _login_admin(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"account": "admin01", "password": "Admin@123"},
    )
    assert response.status_code == 200, response.text


def _login_teacher(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"account": "teacher01", "password": "Teacher@123"},
    )
    assert response.status_code == 200, response.text


def _set_stock(db_session, consumable_id: int, value: int) -> None:
    db_session.expire_all()
    consumable = db_session.get(Consumable, consumable_id)
    consumable.current_stock = value
    db_session.commit()


def _audit(client: TestClient, consumable_id: int, **params) -> dict:
    response = client.get(
        AUDIT_URL, params={"consumable_id": consumable_id, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_audit_reports_diff_between_ledger_end_and_current_stock(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    _login_student(admin_client)
    _issue(admin_client, db_session, consumable["id"], 3)
    _set_stock(db_session, consumable["id"], 9)

    _login_teacher(admin_client)
    data = _audit(admin_client, consumable["id"])

    assert data["opening_stock"] == 10
    assert data["ledger_end"] == 7
    assert data["current_stock"] == 9
    assert data["diff"] == 2
    assert data["evidence_status"] == "unverifiable"
    assert data["evidence_label"] == "无法验证"
    assert any("相差 2" in reason for reason in data["evidence_reasons"])
    assert data["affected_range"] is not None
    assert data["affected_range"]["start_exec_seq"] == 2


def test_audit_marks_missing_trusted_opening_as_unverifiable(
    admin_client: TestClient, db_session
) -> None:
    now = datetime.now().replace(microsecond=0)
    consumable = Consumable(
        name="砂纸", spec="240目", unit="张", location="二号柜",
        safety_stock=0, current_stock=3, class_id=1, status="enabled",
        created_at=now, updated_at=now,
    )
    db_session.add(consumable)
    db_session.flush()
    inventory_ledger.append_event(
        db_session,
        consumable=consumable,
        event_type=inventory_ledger.EVENT_REQUISITION,
        delta=-2,
        stock_before=5,
        stock_after=3,
        business_at=now,
        posted_at=now,
        source_type=inventory_ledger.SOURCE_REQUISITION_ORDER,
    )
    db_session.commit()

    _login_teacher(admin_client)
    data = _audit(admin_client, consumable.id)

    assert data["opening_stock"] is None
    assert data["evidence_status"] == "unverifiable"
    assert data["evidence_label"] == "无法验证"
    assert "缺少可信期初事件" in data["evidence_reasons"]
    assert data["first_anomaly"] is not None
    assert data["first_anomaly"]["exec_seq"] == 1
    assert data["affected_range"]["start_exec_seq"] == 1
    assert data["affected_range"]["end_exec_seq"] == 1


def test_audit_marks_unreadable_event_with_affected_range(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    _login_student(admin_client)
    _issue(admin_client, db_session, consumable["id"], 2)
    _issue(admin_client, db_session, consumable["id"], 1)

    db_session.expire_all()
    events = list(
        db_session.scalars(
            select(InventoryEvent)
            .where(InventoryEvent.consumable_id == consumable["id"])
            .order_by(InventoryEvent.exec_seq)
        ).all()
    )
    events[1].evidence_status = "unverifiable"
    db_session.commit()

    _login_teacher(admin_client)
    data = _audit(admin_client, consumable["id"])

    assert data["evidence_status"] == "unverifiable"
    assert data["first_anomaly"]["exec_seq"] == 2
    assert data["first_anomaly"]["reason"] == "第 2 步事件无法读取"
    assert data["affected_range"]["start_exec_seq"] == 2
    assert data["affected_range"]["end_exec_seq"] == 3


def _set_event_posted_times(
    db_session, consumable_id: int, posted_times: list[datetime]
) -> None:
    db_session.expire_all()
    events = list(
        db_session.scalars(
            select(InventoryEvent)
            .where(InventoryEvent.consumable_id == consumable_id)
            .order_by(InventoryEvent.exec_seq)
        ).all()
    )
    assert len(events) == len(posted_times)
    for event, posted_at in zip(events, posted_times, strict=True):
        event.posted_at = posted_at
    db_session.commit()


def test_audit_separates_posting_and_business_basis(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    now = datetime.now().replace(microsecond=0)
    _login_student(admin_client)
    _issue(
        admin_client,
        db_session,
        consumable["id"],
        3,
        created_at=now - timedelta(minutes=1),
    )
    _issue(
        admin_client,
        db_session,
        consumable["id"],
        5,
        created_at=now - timedelta(days=5),
    )
    _set_event_posted_times(
        db_session,
        consumable["id"],
        [
            now - timedelta(hours=2),
            now - timedelta(hours=1),
            now - timedelta(minutes=10),
        ],
    )

    _login_teacher(admin_client)
    posting_cutoff = (now - timedelta(minutes=30)).isoformat()
    business_cutoff = (now - timedelta(days=3)).isoformat()
    data = _audit(
        admin_client,
        consumable["id"],
        posting_cutoff=posting_cutoff,
        business_cutoff=business_cutoff,
    )

    posting = data["posting"]
    business = data["business"]
    assert posting["basis"] == "posted_at"
    assert posting["label"] == "按入账时间"
    assert business["basis"] == "business_at"
    assert business["label"] == "按业务时间重排"
    assert posting["cutoff"].startswith(posting_cutoff[:10])
    assert business["cutoff"].startswith(business_cutoff[:10])

    assert [event["delta"] for event in posting["events"]] == [10, -3]
    assert [event["stock_after"] for event in posting["events"]] == [10, 7]
    assert posting["stock_at_cutoff"] == 7

    assert [event["delta"] for event in business["events"]] == [10, -5]
    assert [event["stock_after"] for event in business["events"]] == [10, 5]
    assert business["stock_at_cutoff"] == 5

    future = _audit(
        admin_client,
        consumable["id"],
        posting_cutoff=(now + timedelta(days=1)).isoformat(),
        business_cutoff=(now + timedelta(days=1)).isoformat(),
    )
    assert future["posting"]["stock_at_cutoff"] == 2
    assert future["business"]["stock_at_cutoff"] == 2

    before_first = _audit(
        admin_client,
        consumable["id"],
        posting_cutoff=(now - timedelta(days=30)).isoformat(),
        business_cutoff=(now - timedelta(days=30)).isoformat(),
    )
    assert before_first["posting"]["stock_at_cutoff"] is None
    assert before_first["business"]["stock_at_cutoff"] is None


def test_audit_events_expose_source_links(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    _login_student(admin_client)
    receipt = _issue(admin_client, db_session, consumable["id"], 3)
    order_no = receipt["order_no"]
    record_id = receipt["lines"][0]["requisition_id"]

    _login_admin(admin_client)
    reversal = admin_client.post(
        f"{REQUISITIONS_URL}/{record_id}/reversal", json={"reason": "登记错误"}
    )
    assert reversal.status_code == 200, reversal.text
    restock = admin_client.post(
        "/api/v1/inventory/restock",
        json={"consumable_id": consumable["id"], "quantity": 4},
    )
    assert restock.status_code == 200, restock.text

    _login_teacher(admin_client)
    data = _audit(admin_client, consumable["id"])
    events = data["posting"]["events"]

    opening = next(event for event in events if event["event_type"] == "opening")
    issue_event = next(
        event for event in events if event["event_type"] == "requisition"
    )
    reversal_event = next(
        event for event in events if event["event_type"] == "reversal"
    )
    restock_event = next(
        event for event in events if event["event_type"] == "restock"
    )

    assert opening["can_open_source"] is False
    assert issue_event["can_open_source"] is True
    assert issue_event["requisition_id"] == record_id
    assert issue_event["order_no"] == order_no
    assert reversal_event["can_open_source"] is True
    assert reversal_event["order_no"].startswith("RVL-")
    assert restock_event["can_open_source"] is True
    assert restock_event["order_no"].startswith("RSK-")


def test_audit_requires_login(client: TestClient) -> None:
    assert client.get(AUDIT_URL, params={"consumable_id": 1}).status_code == 401


def test_audit_isolates_consumables_and_requires_staff(
    admin_client: TestClient,
) -> None:
    first = _create_consumable(admin_client, name="焊丝", current_stock=6)
    second = _create_consumable(admin_client, name="砂纸", unit="张", current_stock=8)

    _login_student(admin_client)
    assert (
        admin_client.get(
            AUDIT_URL, params={"consumable_id": first["id"]}
        ).status_code
        == 403
    )

    _login_teacher(admin_client)
    first_data = _audit(admin_client, first["id"])
    second_data = _audit(admin_client, second["id"])

    assert first_data["consumable_id"] == first["id"]
    assert first_data["name"] == "焊丝"
    assert second_data["consumable_id"] == second["id"]
    assert second_data["name"] == "砂纸"
    assert all(
        event["event_type"] == "opening" for event in second_data["posting"]["events"]
    )
