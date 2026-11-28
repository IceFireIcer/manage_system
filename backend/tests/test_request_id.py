from __future__ import annotations

import logging

import pytest
from app.agent import llm
from app.agent.llm import (
    RationaleContext,
    RationaleResult,
    generate_rationale_result,
)
from app.agent.rules import Evaluation
from app.database import get_db
from app.main import app
from app.request_context import reset_request_id, set_request_id
from sqlalchemy.exc import OperationalError

READINESS_PATH = "/api/v1/bootstrap/readiness"


class BrokenGenerator:
    def generate(self, context: RationaleContext) -> str:
        raise RuntimeError("model timeout")


def _evaluation() -> Evaluation:
    return Evaluation(
        current_stock=0,
        safety_stock=5,
        q7=10,
        prev_q7=8,
        gap=5,
        rate=10 / 7,
        days_of_supply=0.0,
        tier="urgent",
        recommended_quantity=12,
        reasons=("测试",),
        consumption_state="normal",
        consumption_note="",
    )



def test_response_carries_generated_request_id(client) -> None:
    response = client.get("/api/v1/health")

    request_id = response.headers.get("x-request-id")
    assert request_id is not None
    assert len(request_id) == 32
    assert all(character in "0123456789abcdef" for character in request_id)


def test_incoming_request_id_is_forwarded(client) -> None:
    response = client.get("/api/v1/health", headers={"X-Request-Id": "ops-check-001"})

    assert response.headers["x-request-id"] == "ops-check-001"


def test_invalid_request_id_is_replaced(client) -> None:
    response = client.get("/api/v1/health", headers={"X-Request-Id": "bad id with spaces"})

    assert response.headers["x-request-id"] != "bad id with spaces"
    assert len(response.headers["x-request-id"]) == 32


def test_database_failure_returns_request_id(client) -> None:
    def broken_db():
        class _Broken:
            def scalar(self, *args: object, **kwargs: object) -> None:
                raise OperationalError("SELECT 1", {}, Exception("database is down"))

            def execute(self, *args: object, **kwargs: object) -> None:
                raise OperationalError("SELECT 1", {}, Exception("database is down"))

            def rollback(self) -> None:
                return None

            def close(self) -> None:
                return None

        yield _Broken()

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = broken_db
    try:
        response = client.get("/api/v1/bootstrap/status")
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous

    assert response.status_code == 503
    body = response.json()
    assert body["detail"] == "数据库暂时不可用，请稍后重试"
    assert body["request_id"] == response.headers["x-request-id"]


def test_readiness_failure_carries_request_id(client) -> None:
    def broken_db():
        class _Broken:
            def execute(self, *args: object, **kwargs: object) -> None:
                raise OperationalError("SELECT 1", {}, Exception("database is down"))

            def rollback(self) -> None:
                return None

            def close(self) -> None:
                return None

        yield _Broken()

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = broken_db
    try:
        response = client.get(READINESS_PATH)
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous

    assert response.status_code == 503
    assert response.json()["request_id"] == response.headers["x-request-id"]


def test_access_log_contains_request_id(client, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.request")

    client.get("/api/v1/health", headers={"X-Request-Id": "trace-abc"})

    assert any(
        "trace-abc" in record.message and "/api/v1/health" in record.message
        for record in caplog.records
    )


def test_model_fallback_log_carries_request_id(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(llm, "get_generator", lambda settings=None: BrokenGenerator())
    caplog.set_level(logging.WARNING, logger="app.agent.llm")
    token = set_request_id("model-trace-9")
    try:
        result: RationaleResult = generate_rationale_result(
            RationaleContext(name="焊丝", unit="卷", evaluation=_evaluation())
        )
    finally:
        reset_request_id(token)

    assert result.degraded is True
    assert any(
        "request_id=model-trace-9" in record.message and "回退规则模板" in record.message
        for record in caplog.records
    )
