import time
from datetime import datetime
from typing import Any

import pytest
from app.agent import llm, service
from app.models import OperationLog, Requisition, User
from fastapi.testclient import TestClient
from sqlalchemy import select

SUGGESTIONS_URL = "/api/v1/inventory/suggestions"
REFRESH_URL = "/api/v1/inventory/suggestions/refresh"
RESTOCK_URL = "/api/v1/inventory/restock"
CONSUMABLES_URL = "/api/v1/consumables"


class _StubGenerator:
    def __init__(self, text: str = "规则判定 + AI 润色", error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls = 0

    def generate(self, context) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.text


class _SlowStubGenerator:
    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.calls = 0

    def generate(self, context) -> str:
        self.calls += 1
        time.sleep(self.delay)
        return "并发润色后的判断依据"


@pytest.fixture(autouse=True)
def _isolate_agent_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    service.clear_cache()
    yield
    service.clear_cache()


def _consumable_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "焊丝",
        "spec": "φ1.2mm",
        "unit": "卷",
        "location": "一号柜",
        "safety_stock": 5,
        "current_stock": 3,
    }
    payload.update(overrides)
    return payload


def _create_consumable(admin_client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = admin_client.post(CONSUMABLES_URL, json=_consumable_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def _student_id(db_session) -> int:
    return db_session.scalars(select(User).where(User.account == "student01")).one().id


def _insert_requisition(
    db_session,
    consumable_id: int,
    quantity: int,
    *,
    created_at: datetime | None = None,
    req_type: str = "normal",
    status: str = "active",
) -> None:

    db_session.add(
        Requisition(
            consumable_id=consumable_id,
            user_id=_student_id(db_session),
            quantity=quantity,
            stock_after=0,
            type=req_type,
            status=status,
            created_at=created_at or datetime.now().replace(microsecond=0),
        )
    )
    db_session.commit()


def _stock(client: TestClient, consumable_id: int) -> int:
    data = client.get(CONSUMABLES_URL).json()
    return next(item["current_stock"] for item in data if item["id"] == consumable_id)


def test_suggestions_require_login(client: TestClient) -> None:
    assert client.get(SUGGESTIONS_URL).status_code == 401
    assert client.post(REFRESH_URL).status_code == 401


def test_suggestions_forbidden_for_student(student_client: TestClient) -> None:
    assert student_client.get(SUGGESTIONS_URL).status_code == 403
    assert student_client.post(REFRESH_URL).status_code == 403


def test_suggestions_available_for_teacher(teacher_client: TestClient) -> None:
    response = teacher_client.get(SUGGESTIONS_URL)
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["generated_at"]


def test_refresh_updates_generated_at(teacher_client: TestClient) -> None:
    first = teacher_client.get(SUGGESTIONS_URL).json()
    refreshed = teacher_client.post(REFRESH_URL).json()

    assert refreshed["generated_at"] >= first["generated_at"]


def test_suggestions_sorted_tiers_with_metrics(
    admin_client: TestClient, db_session
) -> None:
    _create_consumable(admin_client, name="铝板", safety_stock=8, current_stock=0)
    _create_consumable(admin_client, name="焊丝", safety_stock=5, current_stock=0)
    moderate = _create_consumable(admin_client, name="焊条", safety_stock=10, current_stock=6)
    trend = _create_consumable(admin_client, name="砂纸", safety_stock=5, current_stock=10)
    _create_consumable(admin_client, name="手套", safety_stock=5, current_stock=100)
    _create_consumable(admin_client, name="胶带", safety_stock=0, current_stock=0)

    _insert_requisition(db_session, moderate["id"], 14)
    _insert_requisition(db_session, trend["id"], 15)
    _insert_requisition(
        db_session,
        trend["id"],
        10,
        created_at=service.local_day_start(8),
    )

    data = admin_client.get(SUGGESTIONS_URL).json()
    items = data["items"]
    assert data["generated_at"]
    assert [item["tier"] for item in items] == [
        "urgent",
        "urgent",
        "moderate",
        "observe",
    ]
    assert [item["name"] for item in items][:2] == ["铝板", "焊丝"]
    assert {item["name"] for item in items} == {"铝板", "焊丝", "焊条", "砂纸"}

    urgent = items[0]
    assert set(urgent) >= {
        "consumable_id",
        "name",
        "spec",
        "unit",
        "current_stock",
        "safety_stock",
        "gap",
        "q7",
        "rate",
        "days_of_supply",
        "tier",
        "recommended_quantity",
        "rationale",
    }
    assert urgent["gap"] == 8
    assert urgent["q7"] == 0
    assert urgent["rate"] == 0
    assert urgent["days_of_supply"] is None
    assert urgent["recommended_quantity"] == 8
    assert "当前库存 0" in urgent["rationale"]
    assert "安全库存线 8" in urgent["rationale"]

    moderate_item = items[2]
    assert moderate_item["q7"] == 14
    assert moderate_item["rate"] == 2
    assert moderate_item["days_of_supply"] == 3
    assert moderate_item["recommended_quantity"] == 18

    trend_item = items[3]
    assert trend_item["q7"] == 15
    assert trend_item["days_of_supply"] == 4.7
    assert trend_item["recommended_quantity"] == 15


def test_rationale_falls_back_without_api_key(admin_client: TestClient) -> None:
    _create_consumable(admin_client, name="焊丝", safety_stock=5, current_stock=3)

    items = admin_client.get(SUGGESTIONS_URL).json()["items"]
    rationale = items[0]["rationale"]
    assert "当前库存 3" in rationale
    assert "安全库存线 5" in rationale
    assert "缺口 2" in rationale
    assert "补货 2" in rationale


def test_rationale_uses_llm_polish(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubGenerator(text="模型润色后的判断依据")
    monkeypatch.setattr(llm, "get_generator", lambda: stub)
    _create_consumable(admin_client, name="焊丝", safety_stock=5, current_stock=0)

    items = admin_client.get(SUGGESTIONS_URL).json()["items"]
    assert items[0]["rationale"] == "模型润色后的判断依据"
    assert stub.calls == 1


def test_rationale_falls_back_on_llm_timeout(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubGenerator(error=TimeoutError("timeout"))
    monkeypatch.setattr(llm, "get_generator", lambda: stub)
    _create_consumable(admin_client, name="焊丝", safety_stock=5, current_stock=3)

    items = admin_client.get(SUGGESTIONS_URL).json()["items"]
    assert "当前库存 3" in items[0]["rationale"]
    assert stub.calls == 1


def test_normal_tier_does_not_call_llm(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubGenerator()
    monkeypatch.setattr(llm, "get_generator", lambda: stub)
    _create_consumable(admin_client, name="手套", safety_stock=5, current_stock=100)

    data = admin_client.get(SUGGESTIONS_URL).json()
    assert data["items"] == []
    assert stub.calls == 0


def test_cache_reused_until_refresh(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubGenerator()
    monkeypatch.setattr(llm, "get_generator", lambda: stub)
    _create_consumable(admin_client, name="焊丝", safety_stock=5, current_stock=0)
    _create_consumable(admin_client, name="焊条", safety_stock=10, current_stock=1)

    first = admin_client.get(SUGGESTIONS_URL).json()
    assert stub.calls == 2
    second = admin_client.get(SUGGESTIONS_URL).json()
    assert stub.calls == 2
    assert second["items"] == first["items"]
    assert second["generated_at"] == first["generated_at"]

    refreshed = admin_client.post(REFRESH_URL).json()
    assert stub.calls == 4
    assert refreshed["items"] == first["items"]


def test_suggestions_generate_rationales_concurrently(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _SlowStubGenerator(delay=0.6)
    monkeypatch.setattr(llm, "get_generator", lambda: stub)
    for index in range(5):
        _create_consumable(
            admin_client, name=f"待补耗材{index}", safety_stock=10, current_stock=0
        )

    started = time.perf_counter()
    items = admin_client.get(SUGGESTIONS_URL).json()["items"]
    elapsed = time.perf_counter() - started

    assert len(items) == 5
    assert stub.calls == 5
    assert all(item["rationale"] == "并发润色后的判断依据" for item in items)
    assert elapsed < 3.0


def test_cache_cleared_after_stock_mutations(admin_client: TestClient) -> None:
    consumable = _create_consumable(
        admin_client, name="焊丝", safety_stock=5, current_stock=3
    )
    assert admin_client.get(SUGGESTIONS_URL).status_code == 200
    assert service._cache

    updated = admin_client.put(
        f"{CONSUMABLES_URL}/{consumable['id']}", json={"safety_stock": 6}
    )
    assert updated.status_code == 200
    assert service._cache == {}

    admin_client.get(SUGGESTIONS_URL)
    assert service._cache
    created = admin_client.post(
        "/api/v1/requisitions",
        json={"items": [{"consumable_id": consumable["id"], "quantity": 1}]},
    )
    assert created.status_code == 201, created.text
    assert service._cache == {}

    admin_client.get(SUGGESTIONS_URL)
    assert service._cache
    record_id = admin_client.get("/api/v1/requisitions").json()["items"][0]["id"]
    reversed_response = admin_client.post(
        f"/api/v1/requisitions/{record_id}/reversal", json={"reason": "登记错误"}
    )
    assert reversed_response.status_code == 200, reversed_response.text
    assert service._cache == {}

    admin_client.get(SUGGESTIONS_URL)
    assert service._cache
    disabled = admin_client.patch(
        f"{CONSUMABLES_URL}/{consumable['id']}/status", json={"status": "disabled"}
    )
    assert disabled.status_code == 200
    assert service._cache == {}


def test_restock_success_updates_stock_logs_and_cache(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(
        admin_client, name="焊丝", safety_stock=5, current_stock=1
    )
    assert len(admin_client.get(SUGGESTIONS_URL).json()["items"]) == 1

    response = admin_client.post(
        RESTOCK_URL,
        json={"consumable_id": consumable["id"], "quantity": 20, "remark": "采购回库"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["consumable_id"] == consumable["id"]
    assert body["consumable_name"] == "焊丝"
    assert body["quantity"] == 20
    assert body["stock_before"] == 1
    assert body["stock_after"] == 21
    assert _stock(admin_client, consumable["id"]) == 21

    log = db_session.scalars(
        select(OperationLog).where(OperationLog.action == "restock")
    ).one()
    assert log.target_type == "consumable"
    assert log.target_id == consumable["id"]
    assert '"current_stock": 1' in log.before_value
    assert '"current_stock": 21' in log.after_value
    assert log.remark == "采购回库"

    assert admin_client.get(SUGGESTIONS_URL).json()["items"] == []


def test_restock_forbidden_for_teacher_and_student(
    teacher_client: TestClient, student_client: TestClient
) -> None:
    payload = {"consumable_id": 1, "quantity": 1}
    assert teacher_client.post(RESTOCK_URL, json=payload).status_code == 403
    assert student_client.post(RESTOCK_URL, json=payload).status_code == 403


@pytest.mark.parametrize(
    "payload",
    [
        {"consumable_id": 1, "quantity": 0},
        {"consumable_id": 1, "quantity": -3},
        {"consumable_id": 1, "quantity": 1.5},
        {"consumable_id": 1, "quantity": "abc"},
        {"consumable_id": 0, "quantity": 2},
        {"quantity": 2},
    ],
)
def test_restock_rejects_invalid_payload(
    admin_client: TestClient, payload: dict[str, Any]
) -> None:
    assert admin_client.post(RESTOCK_URL, json=payload).status_code == 422


def test_restock_rejects_disabled_consumable(admin_client: TestClient) -> None:
    consumable = _create_consumable(admin_client, name="焊丝", safety_stock=5, current_stock=1)
    disabled = admin_client.patch(
        f"{CONSUMABLES_URL}/{consumable['id']}/status",
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200, disabled.text

    response = admin_client.post(
        RESTOCK_URL, json={"consumable_id": consumable["id"], "quantity": 5}
    )
    assert response.status_code == 400


def test_restock_unknown_consumable_returns_404(admin_client: TestClient) -> None:
    response = admin_client.post(RESTOCK_URL, json={"consumable_id": 9999, "quantity": 5})
    assert response.status_code == 404
