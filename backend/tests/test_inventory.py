from datetime import datetime, timedelta
from typing import Any

from app.models import Requisition, User
from fastapi.testclient import TestClient
from sqlalchemy import select

OVERVIEW_URL = "/api/v1/inventory/overview"
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


def _user_id(db_session, account: str) -> int:
    return db_session.scalars(select(User).where(User.account == account)).one().id


def _insert_requisition(db_session, **kwargs: Any) -> Requisition:
    record = Requisition(
        consumable_id=kwargs["consumable_id"],
        user_id=kwargs["user_id"],
        quantity=kwargs.get("quantity", 1),
        stock_after=kwargs.get("stock_after", 99),
        type=kwargs.get("type", "normal"),
        status=kwargs.get("status", "active"),
        reversal_reason=kwargs.get("reversal_reason"),
        remark=kwargs.get("remark"),
        created_at=kwargs.get("created_at", datetime.now().replace(microsecond=0)),
    )
    db_session.add(record)
    db_session.commit()
    return record


def _login(client: TestClient, account: str, password: str) -> None:
    response = client.post("/api/v1/auth/login", json={"account": account, "password": password})
    assert response.status_code == 200, response.text


def test_overview_requires_login(client: TestClient) -> None:
    response = client.get(OVERVIEW_URL)

    assert response.status_code == 401


def test_overview_summary_counts(admin_client: TestClient) -> None:
    _create_consumable(admin_client, name="焊丝", safety_stock=5, current_stock=2)
    _create_consumable(admin_client, name="手套", safety_stock=5, current_stock=0)
    _create_consumable(admin_client, name="砂纸", safety_stock=5, current_stock=10)
    disabled = _create_consumable(admin_client, name="旧焊丝", safety_stock=5, current_stock=1)
    response = admin_client.patch(
        f"{CONSUMABLES_URL}/{disabled['id']}/status", json={"status": "disabled"}
    )
    assert response.status_code == 200, response.text

    data = admin_client.get(OVERVIEW_URL).json()

    assert data["summary"] == {
        "total_kinds": 3,
        "low_stock_count": 2,
        "today_requisitions": 0,
    }
    assert len(data["items"]) == 3


def test_overview_today_requisitions(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    student_id = _user_id(db_session, "student01")
    _login(admin_client, "student01", "Student@123")
    response = admin_client.post(
        REQUISITIONS_URL,
        json={
            "items": [
                {"consumable_id": consumable["id"], "quantity": 1},
                {"consumable_id": consumable["id"], "quantity": 2},
            ]
        },
    )
    assert response.status_code == 201, response.text

    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    _insert_requisition(
        db_session,
        consumable_id=consumable["id"],
        user_id=student_id,
        created_at=today_start - timedelta(seconds=1),
    )
    _insert_requisition(
        db_session,
        consumable_id=consumable["id"],
        user_id=student_id,
        type="reversal",
        status="reversed",
    )
    _insert_requisition(
        db_session,
        consumable_id=consumable["id"],
        user_id=student_id,
        status="reversed",
    )

    data = admin_client.get(OVERVIEW_URL).json()

    assert data["summary"]["today_requisitions"] == 2


def test_overview_items_ordering(admin_client: TestClient) -> None:
    _create_consumable(admin_client, name="砂纸", safety_stock=5, current_stock=10)
    _create_consumable(admin_client, name="D耗材", safety_stock=5, current_stock=3)
    _create_consumable(admin_client, name="B耗材", safety_stock=5, current_stock=2)
    _create_consumable(admin_client, name="A耗材", safety_stock=5, current_stock=0)
    _create_consumable(admin_client, name="C耗材", safety_stock=5, current_stock=10)

    data = admin_client.get(OVERVIEW_URL).json()

    names = [item["name"] for item in data["items"]]
    assert names == ["A耗材", "B耗材", "D耗材", "C耗材", "砂纸"]


def test_overview_item_fields(admin_client: TestClient) -> None:
    created = _create_consumable(admin_client)

    data = admin_client.get(OVERVIEW_URL).json()

    assert data["items"] == [
        {
            "id": created["id"],
            "name": "焊丝",
            "spec": "φ1.2mm",
            "unit": "卷",
            "location": "一号柜",
            "current_stock": 100,
            "safety_stock": 5,
            "low_stock": False,
            "is_out": False,
        }
    ]


def test_overview_safety_zero_not_low_but_out(admin_client: TestClient) -> None:
    _create_consumable(admin_client, name="无预警线", safety_stock=0, current_stock=0)

    data = admin_client.get(OVERVIEW_URL).json()

    assert data["summary"]["low_stock_count"] == 0
    assert data["items"][0]["low_stock"] is False
    assert data["items"][0]["is_out"] is True


def test_overview_student_access(student_client: TestClient) -> None:
    response = student_client.get(OVERVIEW_URL)

    assert response.status_code == 200
    assert response.json()["summary"]["total_kinds"] == 0
