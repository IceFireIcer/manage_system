from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

OVERVIEW_URL = "/api/v1/inventory/overview"
CONSUMABLES_URL = "/api/v1/consumables"
REPO_ROOT = Path(__file__).resolve().parents[2]
VIEWS = REPO_ROOT / "frontend" / "src" / "views"


def _create_consumable(admin_client: TestClient, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "焊丝",
        "spec": "φ1.2mm",
        "unit": "卷",
        "location": "一号柜",
        "safety_stock": 5,
        "current_stock": 100,
    }
    payload.update(overrides)
    response = admin_client.post(CONSUMABLES_URL, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_out_sorts_before_low_stock(admin_client: TestClient) -> None:
    _create_consumable(admin_client, name="有预警低库存", safety_stock=5, current_stock=2)
    _create_consumable(admin_client, name="零安全线缺货", safety_stock=0, current_stock=0)
    _create_consumable(admin_client, name="有预警缺货", safety_stock=5, current_stock=0)
    _create_consumable(admin_client, name="充足库存", safety_stock=5, current_stock=100)

    data = admin_client.get(OVERVIEW_URL).json()
    names = [item["name"] for item in data["items"]]

    assert names == ["有预警缺货", "零安全线缺货", "有预警低库存", "充足库存"]


def test_zero_safety_out_has_priority_and_no_warning(admin_client: TestClient) -> None:
    _create_consumable(admin_client, name="有预警低库存", safety_stock=5, current_stock=2)
    _create_consumable(admin_client, name="零安全线缺货", safety_stock=0, current_stock=0)

    data = admin_client.get(OVERVIEW_URL).json()
    first = data["items"][0]

    assert first["name"] == "零安全线缺货"
    assert first["safety_stock"] == 0
    assert first["low_stock"] is False
    assert first["is_out"] is True
    assert data["summary"]["low_stock_count"] == 1


def test_overview_after_restock_clears_risk(admin_client: TestClient) -> None:
    created = _create_consumable(admin_client, safety_stock=5, current_stock=1)
    response = admin_client.post(
        "/api/v1/inventory/restock",
        json={"consumable_id": created["id"], "quantity": 6, "request_id": "t14-restock"},
    )
    assert response.status_code == 200, response.text

    data = admin_client.get(OVERVIEW_URL).json()
    item = next(row for row in data["items"] if row["id"] == created["id"])

    assert item["current_stock"] == 7
    assert item["low_stock"] is False
    assert item["is_out"] is False
    assert data["summary"]["low_stock_count"] == 0


def test_overview_navigation_query_contract() -> None:
    overview = (VIEWS / "Overview.vue").read_text(encoding="utf-8")
    restock = (VIEWS / "Restock.vue").read_text(encoding="utf-8")
    consumables = (VIEWS / "Consumables.vue").read_text(encoding="utf-8")

    assert "path: '/restock', query: { consumable_id: String(row.id) }" in overview
    assert "path: '/consumables', query: { consumable_id: String(row.id) }" in overview
    assert "route.query.consumable_id" in restock
    assert "`restock-suggestion-${item.consumable_id}`" in restock
    assert "route.query.consumable_id" in consumables


def test_overview_stale_response_guard_contract() -> None:
    overview = (VIEWS / "Overview.vue").read_text(encoding="utf-8")

    assert "let overviewSeq = 0" in overview
    assert "const seq = ++overviewSeq" in overview
    assert "if (seq !== overviewSeq || !active) return" in overview
    assert "pollFailed.value = true" in overview
    assert "lastUpdated.value = new Date()" in overview
