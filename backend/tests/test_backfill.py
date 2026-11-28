from datetime import datetime, timedelta
from typing import Any

from app import business_time
from app.models import (
    Consumable,
    InventoryEvent,
    OperationLog,
    Requisition,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import select

BASE_URL = "/api/v1/requisitions"
CONSUMABLES_URL = "/api/v1/consumables"
COUNTS_URL = "/api/v1/inventory/counts"
CONTROLLED_URL = f"{BASE_URL}/backfills/controlled"

ADMIN = ("admin01", "Admin@123")
TEACHER = ("teacher01", "Teacher@123")
STUDENT = ("student01", "Student@123")


def _login(client: TestClient, account: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"account": account, "password": password}
    )
    assert response.status_code == 200, response.text


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


def _user_id(db_session, account: str) -> int:
    return db_session.scalars(select(User).where(User.account == account)).one().id


def _shift_opening(
    db_session,
    consumable_id: int,
    days: int,
    *,
    source_type: str = "opening",
) -> InventoryEvent:
    old = business_time.now().replace(microsecond=0) - timedelta(days=days)
    db_session.expire_all()
    consumable = db_session.get(Consumable, consumable_id)
    consumable.created_at = old
    opening = db_session.scalars(
        select(InventoryEvent).where(
            InventoryEvent.consumable_id == consumable_id,
            InventoryEvent.event_type == "opening",
        )
    ).one()
    opening.business_at = old
    opening.posted_at = old
    opening.source_type = source_type
    db_session.commit()
    return opening


def _stock(db_session, consumable_id: int) -> int:
    db_session.expire_all()
    return db_session.get(Consumable, consumable_id).current_stock


def _events(db_session, consumable_id: int) -> list[InventoryEvent]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(InventoryEvent)
            .where(InventoryEvent.consumable_id == consumable_id)
            .order_by(InventoryEvent.exec_seq, InventoryEvent.id)
        ).all()
    )


def _records(db_session) -> list[Requisition]:
    db_session.expire_all()
    return list(db_session.scalars(select(Requisition).order_by(Requisition.id)).all())


def _backfill(
    client: TestClient,
    consumable_id: int,
    *,
    business_at: datetime,
    quantity: int = 1,
    reason: str = "课间漏登",
    user_id: int | None = None,
):
    payload: dict[str, Any] = {
        "items": [{"consumable_id": consumable_id, "quantity": quantity}],
        "created_at": business_at.isoformat(),
        "backfill_reason": reason,
    }
    if user_id is not None:
        payload["user_id"] = user_id
    return client.post(BASE_URL, json=payload)


def test_backfill_within_window_deducts_once(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    _shift_opening(db_session, consumable["id"], 10)
    student_id = _user_id(db_session, "student01")
    business_at = business_time.now().replace(microsecond=0) - timedelta(days=5)

    response = _backfill(
        admin_client,
        consumable["id"],
        business_at=business_at,
        reason="课间漏登，事后补录",
        user_id=student_id,
    )

    assert response.status_code == 201, response.text
    data = response.json()
    record = _records(db_session)[0]
    assert record.backfill_mode == "normal"
    assert record.backfill_reason == "课间漏登，事后补录"
    assert record.operator_name == "李班委"
    assert record.posted_at is not None
    assert record.posted_at > record.created_at
    assert data["receipt"]["backfill_reason"] == "课间漏登，事后补录"
    assert data["receipt"]["lines"][0]["backfill_mode"] == "normal"
    assert data["receipt"]["lines"][0]["event_id"] is not None
    assert _stock(db_session, consumable["id"]) == 9
    events = _events(db_session, consumable["id"])
    assert [(event.event_type, event.delta) for event in events] == [
        ("opening", 10),
        ("requisition", -1),
    ]

    detail = admin_client.get(f"{BASE_URL}/{record.id}").json()
    assert detail["backfill_reason"] == "课间漏登，事后补录"
    assert detail["operator_name"] == "李班委"
    assert detail["backfill_mode"] == "normal"


def test_backfill_after_posted_count_affects_stock_once(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    _shift_opening(db_session, consumable["id"], 10)
    student_id = _user_id(db_session, "student01")
    count_at = business_time.now().replace(microsecond=0) - timedelta(days=5)

    count_response = admin_client.post(
        COUNTS_URL,
        json={
            "lines": [
                {
                    "consumable_id": consumable["id"],
                    "counted_qty": 7,
                    "reason": "丢失",
                }
            ],
            "count_at": count_at.isoformat(),
        },
    )
    assert count_response.status_code == 201, count_response.text
    count = count_response.json()
    posted = admin_client.post(f"{COUNTS_URL}/{count['order_no']}/post")
    assert posted.status_code == 201, posted.text
    posted_count = posted.json()
    assert _stock(db_session, consumable["id"]) == 7
    adjustment = posted_count["lines"][0]["event_id"]
    assert adjustment is not None

    business_at = count_at - timedelta(days=1)
    response = _backfill(
        admin_client,
        consumable["id"],
        business_at=business_at,
        quantity=3,
        reason="盘点前实际领用，盘点后补登",
        user_id=student_id,
    )

    assert response.status_code == 201, response.text
    data = response.json()
    record = _records(db_session)[0]
    assert record.backfill_mode == "counted"
    assert record.linked_count_id == posted_count["id"]
    assert record.linked_event_id == adjustment
    assert record.stock_after == 7
    assert record.backfill_reason == "盘点前实际领用，盘点后补登"
    assert data["receipt"]["lines"][0]["event_id"] is None
    assert data["receipt"]["lines"][0]["linked_count_order_no"] == count["order_no"]
    assert _stock(db_session, consumable["id"]) == 7
    events = _events(db_session, consumable["id"])
    assert [(event.event_type, event.delta) for event in events] == [
        ("opening", 10),
        ("inventory_adjustment", -3),
    ]

    detail = admin_client.get(f"{BASE_URL}/{record.id}").json()
    assert detail["backfill_mode"] == "counted"
    assert detail["linked_count_order_no"] == count["order_no"]
    assert detail["linked_event_id"] == adjustment


def test_backfill_at_imported_opening_is_not_deducted_twice(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    opening = _shift_opening(
        db_session, consumable["id"], 10, source_type="ledger_import"
    )
    business_at = opening.business_at - timedelta(days=2)

    response = _backfill(
        admin_client,
        consumable["id"],
        business_at=business_at,
        quantity=2,
        reason="期初导入前实际发生",
    )

    assert response.status_code == 201, response.text
    record = _records(db_session)[0]
    assert record.backfill_mode == "opening"
    assert record.linked_event_id == opening.id
    assert _stock(db_session, consumable["id"]) == 10
    assert [event.event_type for event in _events(db_session, consumable["id"])] == [
        "opening"
    ]


def test_backfill_out_of_window_returns_controlled_path(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    business_at = business_time.now().replace(microsecond=0) - timedelta(days=31)

    response = _backfill(admin_client, consumable["id"], business_at=business_at)

    assert response.status_code == 400
    assert response.headers["X-Error-Code"] == "backfill_out_of_range"
    detail = response.json()["detail"]
    assert "30" in detail
    assert "受控调整" in detail
    assert CONTROLLED_URL in detail
    assert _records(db_session) == []
    assert _stock(db_session, consumable["id"]) == 10


def test_controlled_backfill_allows_over_window_and_traces_reason(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    _shift_opening(db_session, consumable["id"], 40)
    student_id = _user_id(db_session, "student01")
    business_at = business_time.now().replace(microsecond=0) - timedelta(days=31)
    reason = "历史漏登，教师授权补录"

    _login(admin_client, *TEACHER)
    response = admin_client.post(
        CONTROLLED_URL,
        json={
            "items": [{"consumable_id": consumable["id"], "quantity": 1}],
            "user_id": student_id,
            "business_at": business_at.isoformat(),
            "reason": reason,
            "remark": "已核对纸质台账",
        },
    )

    assert response.status_code == 201, response.text
    data = response.json()
    record = _records(db_session)[0]
    assert record.backfill_mode == "controlled"
    assert record.backfill_reason == reason
    assert record.operator_name == "王老师"
    assert record.posted_at is not None
    assert data["receipt"]["backfill_reason"] == reason
    assert data["receipt"]["operator_name"] == "王老师"
    assert _stock(db_session, consumable["id"]) == 9
    events = _events(db_session, consumable["id"])
    assert [(event.event_type, event.delta) for event in events] == [
        ("opening", 10),
        ("requisition", -1),
    ]

    logs = db_session.scalars(
        select(OperationLog).where(
            OperationLog.action == "requisition_backfill_controlled"
        )
    ).all()
    assert len(logs) == 1
    assert reason in logs[0].after_value

    detail = admin_client.get(f"{BASE_URL}/{record.id}").json()
    assert detail["backfill_mode"] == "controlled"
    assert detail["backfill_reason"] == reason
    assert detail["operator_name"] == "王老师"


def test_student_cannot_use_controlled_backfill(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    student_id = _user_id(db_session, "student01")
    business_at = business_time.now().replace(microsecond=0) - timedelta(days=31)

    _login(admin_client, *STUDENT)
    response = admin_client.post(
        CONTROLLED_URL,
        json={
            "items": [{"consumable_id": consumable["id"], "quantity": 1}],
            "user_id": student_id,
            "business_at": business_at.isoformat(),
            "reason": "学生尝试超期补录",
        },
    )

    assert response.status_code == 403
    assert _records(db_session) == []
    assert _stock(db_session, consumable["id"]) == 10


def test_controlled_backfill_requires_reason(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    business_at = business_time.now().replace(microsecond=0) - timedelta(days=31)

    _login(admin_client, *ADMIN)
    response = admin_client.post(
        CONTROLLED_URL,
        json={
            "items": [{"consumable_id": consumable["id"], "quantity": 1}],
            "business_at": business_at.isoformat(),
        },
    )

    assert response.status_code == 422
    assert _records(db_session) == []
