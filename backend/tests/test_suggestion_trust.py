import time
from typing import Any

import pytest
from app.agent import llm, service
from app.models import Consumable
from fastapi.testclient import TestClient

SUGGESTIONS_URL = "/api/v1/inventory/suggestions"
REFRESH_URL = "/api/v1/inventory/suggestions/refresh"
CONSUMABLES_URL = "/api/v1/consumables"


class _StubGenerator:
    def __init__(
        self,
        text: str = "",
        error: Exception | None = None,
        on_generate=None,
    ) -> None:
        self.text = text
        self.error = error
        self.on_generate = on_generate
        self.calls = 0

    def generate(self, context) -> str:
        self.calls += 1
        if self.on_generate is not None:
            self.on_generate(context)
        if self.error is not None:
            raise self.error
        return self.text


@pytest.fixture(autouse=True)
def _isolate_agent_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    service.clear_cache()
    yield
    service.clear_cache()


def _create_consumable(
    admin_client: TestClient, **overrides: Any
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "焊丝",
        "spec": "φ1.2mm",
        "unit": "卷",
        "location": "一号柜",
        "safety_stock": 5,
        "current_stock": 3,
    }
    payload.update(overrides)
    response = admin_client.post(CONSUMABLES_URL, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_inspection_discards_result_when_stock_changes_during_llm(
    admin_client: TestClient, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    consumable = _create_consumable(admin_client, safety_stock=5, current_stock=1)
    target_id = consumable["id"]

    def mutate(context) -> None:
        row = db_session.get(Consumable, target_id)
        row.current_stock = 40
        db_session.commit()
        service.clear_cache(target_id)

    stub = _StubGenerator(
        text="当前库存 1 卷，安全库存线 5 卷，建议补货 4 卷。",
        on_generate=mutate,
    )
    monkeypatch.setattr(llm, "get_generator", lambda: stub)

    data = admin_client.get(SUGGESTIONS_URL).json()
    assert stub.calls == 1
    item = data["items"][0]
    assert item["current_stock"] == 1
    assert item["rationale_source"] == "rules"
    assert item["rationale_degraded"] is True
    assert "当前库存 1" in item["rationale"]
    assert service._cache == {}

    assert admin_client.get(SUGGESTIONS_URL).json()["items"] == []


def test_llm_numbers_conflicting_with_rules_fall_back_to_template(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubGenerator(text="当前库存 3 卷，安全库存线 5 卷，建议补货 999 卷。")
    monkeypatch.setattr(llm, "get_generator", lambda: stub)
    _create_consumable(admin_client, safety_stock=5, current_stock=3)

    item = admin_client.get(SUGGESTIONS_URL).json()["items"][0]
    assert item["rationale"] != stub.text
    assert "补货 2" in item["rationale"]
    assert item["rationale_source"] == "rules"
    assert item["rationale_degraded"] is True


def test_llm_numbers_matching_rules_are_accepted(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = "当前库存 3 卷，安全库存线 5 卷，库存缺口 2 卷，建议补货 2 卷。"
    stub = _StubGenerator(text=text)
    monkeypatch.setattr(llm, "get_generator", lambda: stub)
    _create_consumable(admin_client, safety_stock=5, current_stock=3)

    item = admin_client.get(SUGGESTIONS_URL).json()["items"][0]
    assert item["rationale"] == text
    assert item["rationale_source"] == "llm"
    assert item["rationale_degraded"] is False


def test_unconfigured_model_reports_rule_template_source(
    admin_client: TestClient,
) -> None:
    _create_consumable(admin_client, safety_stock=5, current_stock=3)

    item = admin_client.get(SUGGESTIONS_URL).json()["items"][0]
    assert item["rationale_source"] == "rules"
    assert item["rationale_degraded"] is False
    assert "当前库存 3" in item["rationale"]


def test_llm_timeout_degrades_to_shared_rule_template(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubGenerator(error=TimeoutError("timeout"))
    monkeypatch.setattr(llm, "get_generator", lambda: stub)
    _create_consumable(admin_client, safety_stock=5, current_stock=3)

    item = admin_client.get(SUGGESTIONS_URL).json()["items"][0]
    assert item["rationale_source"] == "rules"
    assert item["rationale_degraded"] is True
    assert "当前库存 3" in item["rationale"]


def test_each_card_keeps_its_own_computed_at(admin_client: TestClient) -> None:
    older = _create_consumable(
        admin_client, name="焊丝", safety_stock=5, current_stock=0
    )
    newer = _create_consumable(
        admin_client, name="焊条", safety_stock=10, current_stock=0
    )

    first = admin_client.get(SUGGESTIONS_URL).json()
    first_times = {
        item["consumable_id"]: item["computed_at"] for item in first["items"]
    }
    assert first_times[older["id"]] == first_times[newer["id"]]

    service.clear_cache(newer["id"])
    time.sleep(0.02)
    second = admin_client.get(SUGGESTIONS_URL).json()
    second_times = {
        item["consumable_id"]: item["computed_at"] for item in second["items"]
    }
    assert second_times[older["id"]] == first_times[older["id"]]
    assert second_times[newer["id"]] > first_times[newer["id"]]
    assert second["generated_at"] >= first["generated_at"]
    assert isinstance(second["generation"], int)


def test_cache_invalidated_when_stock_changes_without_explicit_clear(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, safety_stock=5, current_stock=3)
    assert admin_client.get(SUGGESTIONS_URL).json()["items"][0]["current_stock"] == 3
    assert service._cache

    row = db_session.get(Consumable, consumable["id"])
    row.current_stock = 0
    db_session.commit()

    data = admin_client.get(SUGGESTIONS_URL).json()
    assert data["items"][0]["current_stock"] == 0
