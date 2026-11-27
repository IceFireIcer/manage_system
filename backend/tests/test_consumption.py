from datetime import datetime, timedelta
from typing import Any

from app.consumption import local_day_start, reconstruct_stock
from app.models import Requisition, User
from fastapi.testclient import TestClient
from sqlalchemy import select

SUMMARY_URL = "/api/v1/consumption/summary"
AUDIT_URL = "/api/v1/consumption/audit"
CONSUMABLES_URL = "/api/v1/consumables"
REQUISITIONS_URL = "/api/v1/requisitions"


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


def _student_id(db_session) -> int:
    return db_session.scalars(select(User).where(User.account == "student01")).one().id


def _insert_flow(
    db_session,
    *,
    consumable_id: int,
    quantity: int,
    flow_type: str = "normal",
    flow_status: str = "active",
    created_at: datetime | None = None,
    stock_after: int = 0,
):

    record = Requisition(
        consumable_id=consumable_id,
        user_id=_student_id(db_session),
        quantity=quantity,
        stock_after=stock_after,
        type=flow_type,
        status=flow_status,
        created_at=created_at or datetime.now().replace(microsecond=0),
    )
    db_session.add(record)
    db_session.commit()
    return record


def _login(client: TestClient, account: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"account": account, "password": password}
    )
    assert response.status_code == 200, response.text


def test_summary_requires_login(client: TestClient) -> None:
    assert client.get(SUMMARY_URL).status_code == 401


def test_summary_forbidden_for_student(student_client: TestClient) -> None:
    response = student_client.get(SUMMARY_URL)

    assert response.status_code == 403
    assert response.json()["detail"] == "无权限访问"


def test_summary_invalid_window_rejected(teacher_client: TestClient) -> None:
    response = teacher_client.get(SUMMARY_URL, params={"window": 14})

    assert response.status_code == 422


def test_summary_empty_window_returns_zero_totals(admin_client: TestClient) -> None:
    data = admin_client.get(SUMMARY_URL, params={"window": 7}).json()

    assert data["window"] == 7
    assert data["items"] == []
    assert data["totals"] == {"quantity": 0, "daily_rate": 0}


def test_summary_net_consumption_and_zero_clamp(
    admin_client: TestClient, db_session
) -> None:
    item_a = _create_consumable(admin_client, name="焊丝")
    item_b = _create_consumable(admin_client, name="砂纸")
    item_c = _create_consumable(admin_client, name="手套")
    assert item_c["id"]
    now = datetime.now().replace(microsecond=0) - timedelta(minutes=1)

    _insert_flow(db_session, consumable_id=item_a["id"], quantity=10, created_at=now)
    _insert_flow(
        db_session,
        consumable_id=item_a["id"],
        quantity=2,
        flow_type="reversal",
        created_at=now,
    )
    _insert_flow(db_session, consumable_id=item_b["id"], quantity=5, created_at=now)
    _insert_flow(
        db_session,
        consumable_id=item_b["id"],
        quantity=6,
        flow_type="reversal",
        created_at=now,
    )

    data = admin_client.get(SUMMARY_URL, params={"window": 7}).json()

    by_name = {item["name"]: item for item in data["items"]}
    assert by_name["焊丝"]["quantity"] == 8
    assert by_name["焊丝"]["daily_rate"] == 1.14
    assert by_name["砂纸"]["quantity"] == 0
    assert by_name["砂纸"]["daily_rate"] == 0
    assert "手套" not in by_name
    assert data["totals"]["quantity"] == 8
    assert data["totals"]["daily_rate"] == 1.14


def test_summary_reversed_normal_flow_is_offset_by_reversal(
    admin_client: TestClient, db_session
) -> None:
    item = _create_consumable(admin_client)
    now = datetime.now().replace(microsecond=0) - timedelta(minutes=1)
    _insert_flow(
        db_session,
        consumable_id=item["id"],
        quantity=10,
        flow_status="reversed",
        created_at=now,
    )
    _insert_flow(
        db_session,
        consumable_id=item["id"],
        quantity=10,
        flow_type="reversal",
        created_at=now,
    )

    data = admin_client.get(SUMMARY_URL, params={"window": 7}).json()

    assert data["items"][0]["quantity"] == 0


def test_summary_window_7_boundaries(admin_client: TestClient, db_session) -> None:
    item = _create_consumable(admin_client)
    inside = local_day_start(6)
    outside = local_day_start(7) + timedelta(hours=23, minutes=59, seconds=59)
    _insert_flow(
        db_session, consumable_id=item["id"], quantity=3, created_at=inside
    )
    _insert_flow(
        db_session, consumable_id=item["id"], quantity=5, created_at=outside
    )

    seven = admin_client.get(SUMMARY_URL, params={"window": 7}).json()
    thirty = admin_client.get(SUMMARY_URL, params={"window": 30}).json()

    assert seven["items"][0]["quantity"] == 3
    assert seven["items"][0]["daily_rate"] == 0.43
    assert thirty["items"][0]["quantity"] == 8
    assert thirty["items"][0]["daily_rate"] == 0.27


def test_summary_window_30_includes_older_flow(admin_client: TestClient, db_session) -> None:
    item = _create_consumable(admin_client)
    ten_days_ago = local_day_start(10) + timedelta(hours=3)
    recent = datetime.now().replace(microsecond=0) - timedelta(minutes=1)
    _insert_flow(
        db_session, consumable_id=item["id"], quantity=28, created_at=ten_days_ago
    )
    _insert_flow(db_session, consumable_id=item["id"], quantity=7, created_at=recent)

    seven = admin_client.get(SUMMARY_URL, params={"window": 7}).json()
    thirty = admin_client.get(SUMMARY_URL, params={"window": 30}).json()

    assert seven["items"][0]["quantity"] == 7
    assert seven["items"][0]["daily_rate"] == 1.0
    assert thirty["items"][0]["quantity"] == 35
    assert thirty["items"][0]["daily_rate"] == 1.17


def test_summary_item_fields(admin_client: TestClient, db_session) -> None:
    item = _create_consumable(admin_client, name="砂纸", spec="240目", unit="张")
    now = datetime.now().replace(microsecond=0) - timedelta(minutes=1)
    _insert_flow(db_session, consumable_id=item["id"], quantity=2, created_at=now)

    row = admin_client.get(SUMMARY_URL, params={"window": 7}).json()["items"][0]

    assert row["consumable_id"] == item["id"]
    assert row["name"] == "砂纸"
    assert row["spec"] == "240目"
    assert row["unit"] == "张"


def test_summary_matches_requisition_records(admin_client: TestClient, db_session) -> None:
    item = _create_consumable(admin_client)
    student_id = _student_id(db_session)
    created_at = (datetime.now() - timedelta(minutes=2)).replace(microsecond=0)
    first = admin_client.post(
        REQUISITIONS_URL,
        json={
            "user_id": student_id,
            "created_at": created_at.isoformat(),
            "items": [{"consumable_id": item["id"], "quantity": 10}],
        },
    )
    assert first.status_code == 201, first.text
    listed = admin_client.get(
        REQUISITIONS_URL, params={"consumable_id": item["id"], "page_size": 100}
    ).json()
    record_id = listed["items"][0]["id"]
    reversed_response = admin_client.post(
        f"{REQUISITIONS_URL}/{record_id}/reversal", json={"reason": "登记错误"}
    )
    assert reversed_response.status_code == 200, reversed_response.text
    second = admin_client.post(
        REQUISITIONS_URL,
        json={
            "user_id": student_id,
            "created_at": created_at.isoformat(),
            "items": [{"consumable_id": item["id"], "quantity": 3}],
        },
    )
    assert second.status_code == 201, second.text

    summary = admin_client.get(SUMMARY_URL, params={"window": 7}).json()
    flows = admin_client.get(
        REQUISITIONS_URL, params={"consumable_id": item["id"], "page_size": 100}
    ).json()["items"]
    issued = sum(row["quantity"] for row in flows if row["type"] == "normal")
    reversed_quantity = sum(
        row["quantity"] for row in flows if row["type"] == "reversal"
    )
    expected = max(issued - reversed_quantity, 0)

    assert summary["items"][0]["quantity"] == expected == 3


def test_reconstruct_stock_pure_function() -> None:
    events = [
        {"delta": -3, "stock_after": 97},
        {"delta": 2, "stock_after": 99},
        {"delta": 10, "stock_after": 109},
    ]

    result = reconstruct_stock(events)

    assert result.opening_stock == 100
    assert result.stocks == [97, 99, 109]
    assert result.consistent is True


def test_reconstruct_stock_empty_events() -> None:
    result = reconstruct_stock([])

    assert result.opening_stock is None
    assert result.stocks == []
    assert result.consistent is True


def test_reconstruct_stock_detects_inconsistent_sequence() -> None:
    events = [
        {"delta": -3, "stock_after": 97},
        {"delta": 2, "stock_after": 100},
    ]

    result = reconstruct_stock(events)

    assert result.stocks == [97, 99]
    assert result.consistent is False


def test_audit_requires_login(client: TestClient) -> None:
    assert client.get(AUDIT_URL, params={"consumable_id": 1}).status_code == 401


def test_audit_forbidden_for_student(student_client: TestClient) -> None:
    assert student_client.get(AUDIT_URL, params={"consumable_id": 1}).status_code == 403


def test_audit_requires_consumable_id(admin_client: TestClient) -> None:
    assert admin_client.get(AUDIT_URL).status_code == 422


def test_audit_unknown_consumable_returns_404(admin_client: TestClient) -> None:
    assert admin_client.get(AUDIT_URL, params={"consumable_id": 9999}).status_code == 404


def test_audit_reconstructs_stock_from_all_sources(
    admin_client: TestClient, db_session
) -> None:
    item = _create_consumable(admin_client, current_stock=100)
    student_id = _student_id(db_session)
    created_at = (datetime.now() - timedelta(minutes=1)).replace(microsecond=0)
    issued = admin_client.post(
        REQUISITIONS_URL,
        json={
            "user_id": student_id,
            "created_at": created_at.isoformat(),
            "items": [{"consumable_id": item["id"], "quantity": 3}],
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
        json={"consumable_id": item["id"], "quantity": 10},
    )
    assert restock.status_code == 200, restock.text

    data = admin_client.get(AUDIT_URL, params={"consumable_id": item["id"]}).json()

    assert data["consumable_id"] == item["id"]
    assert data["consistent"] is True
    assert data["opening_stock"] == 100
    assert data["current_stock"] == 110
    assert [event["source"] for event in data["events"]] == [
        "requisition",
        "reversal",
        "restock",
    ]
    assert [event["delta"] for event in data["events"]] == [-3, 3, 10]
    assert [event["stock_after"] for event in data["events"]] == [97, 100, 110]

def test_audit_uses_execution_order_for_backdated_flow(
    admin_client: TestClient, db_session
) -> None:
    item = _create_consumable(admin_client, current_stock=100)
    student_id = _student_id(db_session)
    now = datetime.now().replace(microsecond=0)

    normal = admin_client.post(
        REQUISITIONS_URL,
        json={
            "user_id": student_id,
            "created_at": (now - timedelta(minutes=2)).isoformat(),
            "items": [{"consumable_id": item["id"], "quantity": 3}],
        },
    )
    assert normal.status_code == 201, normal.text

    backdated_at = (now - timedelta(days=5)).isoformat()
    backfill = admin_client.post(
        REQUISITIONS_URL,
        json={
            "user_id": student_id,
            "created_at": backdated_at,
            "items": [{"consumable_id": item["id"], "quantity": 5}],
        },
    )
    assert backfill.status_code == 201, backfill.text

    listed = admin_client.get(
        REQUISITIONS_URL, params={"consumable_id": item["id"], "page_size": 100}
    ).json()["items"]
    normal_id = next(row["id"] for row in listed if row["quantity"] == 3)
    reversed_response = admin_client.post(
        f"{REQUISITIONS_URL}/{normal_id}/reversal", json={"reason": "登记错误"}
    )
    assert reversed_response.status_code == 200, reversed_response.text
    restock = admin_client.post(
        "/api/v1/inventory/restock",
        json={"consumable_id": item["id"], "quantity": 10},
    )
    assert restock.status_code == 200, restock.text

    data = admin_client.get(AUDIT_URL, params={"consumable_id": item["id"]}).json()

    assert data["consistent"] is True
    assert data["opening_stock"] == 100
    assert data["current_stock"] == 105
    assert [event["source"] for event in data["events"]] == [
        "requisition",
        "requisition",
        "reversal",
        "restock",
    ]
    assert [event["delta"] for event in data["events"]] == [-3, -5, 3, 10]
    assert [event["stock_after"] for event in data["events"]] == [97, 92, 95, 105]
    assert data["events"][1]["time"].startswith(backdated_at[:10])
