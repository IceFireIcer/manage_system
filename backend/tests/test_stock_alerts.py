from typing import Any

from app.models import StockAlert
from fastapi.testclient import TestClient
from sqlalchemy import func, select

ALERTS_URL = "/api/v1/inventory/alerts"
SUGGESTIONS_URL = "/api/v1/inventory/suggestions"
CONSUMABLES_URL = "/api/v1/consumables"
ORDERS_URL = "/api/v1/inventory/restock/orders"
REQUISITIONS_URL = "/api/v1/requisitions"


def _consumable_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "焊丝",
        "spec": "φ1.2mm",
        "unit": "卷",
        "location": "实训室一号柜",
        "safety_stock": 5,
        "current_stock": 0,
    }
    payload.update(overrides)
    return payload


def _create_consumable(admin_client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = admin_client.post(
        CONSUMABLES_URL, json=_consumable_payload(**overrides)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _receive(admin_client: TestClient, consumable_id: int, quantity: int) -> None:
    response = admin_client.post(
        ORDERS_URL,
        json={"items": [{"consumable_id": consumable_id, "quantity": quantity}]},
    )
    assert response.status_code == 201, response.text


def _consume(client: TestClient, consumable_id: int, quantity: int) -> None:
    response = client.post(
        REQUISITIONS_URL,
        json={"items": [{"consumable_id": consumable_id, "quantity": quantity}]},
    )
    assert response.status_code == 201, response.text


def _login(client: TestClient, account: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"account": account, "password": password}
    )
    assert response.status_code == 200, response.text


def _alerts(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get(ALERTS_URL, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _alert_rows(db_session) -> list[StockAlert]:
    db_session.expire_all()
    return list(db_session.scalars(select(StockAlert).order_by(StockAlert.id)).all())


def _row_count(db_session) -> int:
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(StockAlert)) or 0


def test_alert_generated_without_opening_suggestion_page(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=2)

    data = _alerts(admin_client)
    assert data["active_count"] == 1
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["consumable_id"] == consumable["id"]
    assert item["status"] == "open"
    assert item["risk_level"] == "low"
    assert item["gap"] == 3
    assert item["detection_count"] == 1
    assert _row_count(db_session) == 1

    assert admin_client.get(SUGGESTIONS_URL).status_code == 200
    assert _row_count(db_session) == 1

    admin_client.post("/api/v1/auth/logout")
    _login(admin_client, "student01", "Student@123")
    _consume(admin_client, consumable["id"], 2)

    admin_client.post("/api/v1/auth/logout")
    _login(admin_client, "admin01", "Admin@123")
    data = _alerts(admin_client)
    item = data["items"][0]
    assert item["risk_level"] == "out"
    assert item["current_stock"] == 0
    assert item["detection_count"] == 2
    assert db_session.scalar(select(func.count()).select_from(StockAlert)) == 1


def test_persistent_shortage_dedupes_and_partial_arrival_keeps_alert(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(
        admin_client, name="砂纸", safety_stock=10, current_stock=0
    )
    assert _row_count(db_session) == 1

    _receive(admin_client, consumable["id"], 3)
    data = _alerts(admin_client)
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["status"] == "open"
    assert item["gap"] == 7
    assert item["partial_arrival"] is True
    assert item["last_order_no"].startswith("RSK-")

    _receive(admin_client, consumable["id"], 2)
    for _ in range(3):
        admin_client.get(ALERTS_URL)
        admin_client.post(f"{ALERTS_URL}/sync")
    data = _alerts(admin_client)
    assert len(data["items"]) == 1
    assert data["active_count"] == 1
    assert data["items"][0]["gap"] == 5
    assert data["items"][0]["status"] == "open"
    assert _row_count(db_session) == 1


def test_alert_completed_when_risk_cleared(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(
        admin_client, name="手套", safety_stock=5, current_stock=1
    )
    assert _row_count(db_session) == 1

    _receive(admin_client, consumable["id"], 4)
    data = _alerts(admin_client)
    assert data["items"] == []
    assert data["active_count"] == 0

    completed = _alerts(admin_client, include_completed=True)["items"][0]
    assert completed["status"] == "completed"
    assert completed["completed_by_name"] == "系统自动"
    assert completed["occurrence"] == 1

    admin_client.post("/api/v1/auth/logout")
    _login(admin_client, "student01", "Student@123")
    _consume(admin_client, consumable["id"], 1)

    admin_client.post("/api/v1/auth/logout")
    _login(admin_client, "admin01", "Admin@123")
    reopened = _alerts(admin_client)["items"][0]
    assert reopened["status"] == "open"
    assert reopened["occurrence"] == 2
    assert reopened["detection_count"] == 1
    assert _row_count(db_session) == 1

    _receive(admin_client, consumable["id"], 5)
    assert _alerts(admin_client)["items"] == []
    assert _row_count(db_session) == 1


def test_alert_acknowledged_survives_partial_receipt(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(
        admin_client, name="口罩", safety_stock=8, current_stock=0
    )
    response = admin_client.patch(
        f"{ALERTS_URL}/{consumable['id']}",
        json={"action": "acknowledge", "note": "已联系采购"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "acknowledged"
    assert response.json()["acknowledged_by_name"] == "李班委"

    _receive(admin_client, consumable["id"], 2)
    item = _alerts(admin_client)["items"][0]
    assert item["status"] == "acknowledged"
    assert item["partial_arrival"] is True
    assert item["note"] == "已联系采购"


def test_alert_permissions_and_actions(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(
        admin_client, name="锉刀", safety_stock=6, current_stock=0
    )

    admin_client.post("/api/v1/auth/logout")
    _login(admin_client, "student01", "Student@123")
    assert admin_client.get(ALERTS_URL).status_code == 403
    assert (
        admin_client.patch(
            f"{ALERTS_URL}/{consumable['id']}", json={"action": "acknowledge"}
        ).status_code
        == 403
    )

    admin_client.post("/api/v1/auth/logout")
    _login(admin_client, "teacher01", "Teacher@123")
    teacher_view = admin_client.get(ALERTS_URL)
    assert teacher_view.status_code == 200
    assert len(teacher_view.json()["items"]) == 1
    assert (
        admin_client.patch(
            f"{ALERTS_URL}/{consumable['id']}", json={"action": "complete"}
        ).status_code
        == 403
    )

    admin_client.post("/api/v1/auth/logout")
    _login(admin_client, "admin01", "Admin@123")
    completed = admin_client.patch(
        f"{ALERTS_URL}/{consumable['id']}",
        json={"action": "complete", "note": "已人工处理"},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert completed.json()["completed_by_name"] == "李班委"
    assert _alerts(admin_client)["items"] == []

    retriggered = admin_client.patch(
        f"{ALERTS_URL}/{consumable['id']}", json={"action": "retrigger"}
    )
    assert retriggered.status_code == 200, retriggered.text
    assert retriggered.json()["status"] == "open"
    assert retriggered.json()["occurrence"] == 2
    assert _row_count(db_session) == 1

    missing = admin_client.patch(
        f"{ALERTS_URL}/999999", json={"action": "complete"}
    )
    assert missing.status_code == 404