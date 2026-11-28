import time
from datetime import timedelta
from typing import Any

import pytest
from app import business_time
from app.agent import llm, service
from app.models import Consumable, InventoryEvent
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

SUGGESTIONS_URL = "/api/v1/inventory/suggestions"
CONSUMABLES_URL = "/api/v1/consumables"
COURSES_URL = "/api/v1/courses"


class _TeachingStub:
    def __init__(
        self,
        teaching_text: str = "",
        teaching_error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.teaching_text = teaching_text
        self.teaching_error = teaching_error
        self.delay = delay
        self.contexts: list[llm.TeachingConsequenceContext] = []

    def generate(self, context) -> str:
        return ""

    def generate_teaching(self, context: llm.TeachingConsequenceContext) -> str:
        self.contexts.append(context)
        if self.delay:
            time.sleep(self.delay)
        if self.teaching_error is not None:
            raise self.teaching_error
        return self.teaching_text


@pytest.fixture(autouse=True)
def _isolate_agent_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    service.clear_cache()
    yield
    service.clear_cache()


def _consumable(
    db: Session, name: str = "焊丝", stock: int = 3, safety_stock: int = 10
) -> Consumable:
    item = Consumable(
        name=name,
        spec="φ1.2mm",
        unit="卷",
        location="一号柜",
        safety_stock=safety_stock,
        current_stock=stock,
        class_id=1,
        status="enabled",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _course(client: TestClient, name: str = "焊接实训") -> dict[str, Any]:
    response = client.post(
        COURSES_URL,
        json={"name": name, "class_label": "计算机 2301", "semester": "2026 秋"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _recipe(
    client: TestClient, course_id: int, items: list[tuple[int, int]]
) -> None:
    response = client.post(
        f"{COURSES_URL}/{course_id}/recipes",
        json={
            "items": [
                {"consumable_id": consumable_id, "quantity_per_person": quantity}
                for consumable_id, quantity in items
            ]
        },
    )
    assert response.status_code == 201, response.text


def _session(
    client: TestClient,
    course_id: int,
    seq: int,
    headcount: int,
    *,
    days_ahead: int = 7,
    status: str = "planned",
) -> dict[str, Any]:
    session_date = (business_time.today() + timedelta(days=days_ahead)).isoformat()
    response = client.post(
        f"{COURSES_URL}/{course_id}/sessions",
        json={
            "seq": seq,
            "session_date": session_date,
            "headcount": headcount,
            "status": status,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _suggestion(client: TestClient) -> dict[str, Any]:
    response = client.get(SUGGESTIONS_URL)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1, items
    return items[0]


def test_teaching_context_joins_planned_sessions(
    teacher_client: TestClient, db_session: Session
) -> None:
    consumable = _consumable(db_session)
    course = _course(teacher_client)
    _recipe(teacher_client, course["id"], [(consumable.id, 2)])
    _session(teacher_client, course["id"], 1, 10, days_ahead=7)
    _session(teacher_client, course["id"], 2, 5, days_ahead=14)

    item = _suggestion(teacher_client)
    teaching = item["teaching_context"]

    assert teaching["unlinked"] is False
    assert teaching["session_count"] == 2
    assert teaching["headcount_total"] == 15
    assert teaching["expected_demand"] == 30
    assert teaching["expected_gap"] == 27
    assert [entry["course_name"] for entry in teaching["sessions"]] == [
        "焊接实训",
        "焊接实训",
    ]
    assert [entry["session_seq"] for entry in teaching["sessions"]] == [1, 2]
    assert teaching["sessions"][0]["required_quantity"] == 20
    assert teaching["consequence"] == (
        "不补货将影响 2 个课次、15 名学生的实训，预计缺口 27卷。"
    )
    assert teaching["consequence_source"] == "rules"
    assert teaching["consequence_degraded"] is False
    assert item["recommended_quantity"] == 7


def test_teaching_context_unlinked_without_sessions(
    teacher_client: TestClient, db_session: Session
) -> None:
    _consumable(db_session)

    teaching = _suggestion(teacher_client)["teaching_context"]

    assert teaching["unlinked"] is True
    assert teaching["sessions"] == []
    assert teaching["session_count"] == 0
    assert teaching["headcount_total"] == 0
    assert teaching["expected_demand"] == 0
    assert teaching["expected_gap"] == 0
    assert teaching["consequence"] == (
        "焊丝未关联课次，暂无法评估补货对已排课次的影响。"
    )


def test_teaching_context_ignores_missing_recipe_and_inactive_sessions(
    teacher_client: TestClient, db_session: Session
) -> None:
    consumable = _consumable(db_session)
    course = _course(teacher_client)

    assert _suggestion(teacher_client)["teaching_context"]["unlinked"] is True

    _recipe(teacher_client, course["id"], [(consumable.id, 2)])
    service.clear_cache()
    assert _suggestion(teacher_client)["teaching_context"]["unlinked"] is True

    _session(teacher_client, course["id"], 1, 10, status="cancelled")
    _session(teacher_client, course["id"], 2, 10, days_ahead=-7)
    service.clear_cache()
    teaching = _suggestion(teacher_client)["teaching_context"]
    assert teaching["unlinked"] is True
    assert teaching["session_count"] == 0


def test_llm_teaching_consequence_uses_structured_input(
    teacher_client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _TeachingStub(
        teaching_text="不补货将影响 2 个课次、15 名学生的实训，预计缺口 27卷。"
    )
    monkeypatch.setattr(llm, "get_generator", lambda: stub)
    consumable = _consumable(db_session)
    course = _course(teacher_client)
    _recipe(teacher_client, course["id"], [(consumable.id, 2)])
    _session(teacher_client, course["id"], 1, 10)
    _session(teacher_client, course["id"], 2, 5)

    teaching = _suggestion(teacher_client)["teaching_context"]

    assert len(stub.contexts) == 1
    assert stub.contexts[0].session_count == 2
    assert stub.contexts[0].headcount_total == 15
    assert stub.contexts[0].expected_demand == 30
    assert stub.contexts[0].expected_gap == 27
    assert teaching["consequence_source"] == "llm"
    assert teaching["consequence_degraded"] is False
    assert teaching["consequence"] == stub.teaching_text


def test_llm_teaching_consequence_inconsistent_falls_back(
    teacher_client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _TeachingStub(
        teaching_text="不补货将影响 9 个课次、99 名学生的实训，预计缺口 999卷。"
    )
    monkeypatch.setattr(llm, "get_generator", lambda: stub)
    consumable = _consumable(db_session)
    course = _course(teacher_client)
    _recipe(teacher_client, course["id"], [(consumable.id, 2)])
    _session(teacher_client, course["id"], 1, 10)
    _session(teacher_client, course["id"], 2, 5)

    teaching = _suggestion(teacher_client)["teaching_context"]

    assert teaching["consequence_source"] == "rules"
    assert teaching["consequence_degraded"] is True
    assert teaching["consequence"] == (
        "不补货将影响 2 个课次、15 名学生的实训，预计缺口 27卷。"
    )
    assert teaching["session_count"] == 2
    assert teaching["headcount_total"] == 15
    assert teaching["expected_gap"] == 27


def test_llm_teaching_consequence_failure_falls_back(
    teacher_client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _TeachingStub(teaching_error=TimeoutError("llm timeout"))
    monkeypatch.setattr(llm, "get_generator", lambda: stub)
    consumable = _consumable(db_session)
    course = _course(teacher_client)
    _recipe(teacher_client, course["id"], [(consumable.id, 2)])
    _session(teacher_client, course["id"], 1, 10)

    teaching = _suggestion(teacher_client)["teaching_context"]

    assert teaching["consequence_source"] == "rules"
    assert teaching["consequence_degraded"] is True
    assert teaching["consequence"] == (
        "不补货将影响 1 个课次、10 名学生的实训，预计缺口 17卷。"
    )
    assert teaching["sessions"][0]["required_quantity"] == 20


def test_llm_teaching_consequence_budget_timeout_falls_back(
    teacher_client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "LLM_BUDGET_SECONDS", 0.02)
    stub = _TeachingStub(
        teaching_text="不补货将影响 1 个课次、10 名学生的实训，预计缺口 17卷。",
        delay=0.2,
    )
    monkeypatch.setattr(llm, "get_generator", lambda: stub)
    first = _consumable(db_session, name="焊丝")
    second = _consumable(db_session, name="焊条")
    course = _course(teacher_client)
    _recipe(
        teacher_client,
        course["id"],
        [(first.id, 2), (second.id, 2)],
    )
    _session(teacher_client, course["id"], 1, 10)

    response = teacher_client.get(SUGGESTIONS_URL)
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2
    for item in items:
        teaching = item["teaching_context"]
        assert teaching["consequence_source"] == "rules"
        assert teaching["consequence_degraded"] is True
        assert teaching["unlinked"] is False
        assert teaching["session_count"] == 1
        assert teaching["headcount_total"] == 10
        assert teaching["expected_gap"] == 17


def test_suggestions_with_teaching_context_are_read_only(
    teacher_client: TestClient, db_session: Session
) -> None:
    consumable = _consumable(db_session)
    course = _course(teacher_client)
    _recipe(teacher_client, course["id"], [(consumable.id, 2)])
    _session(teacher_client, course["id"], 1, 10)
    events_before = (
        db_session.scalar(select(func.count()).select_from(InventoryEvent)) or 0
    )

    _suggestion(teacher_client)

    db_session.expire_all()
    db_session.refresh(consumable)
    events_after = (
        db_session.scalar(select(func.count()).select_from(InventoryEvent)) or 0
    )
    assert consumable.current_stock == 3
    assert events_after == events_before == 0


def test_teaching_context_requires_login(client: TestClient) -> None:
    assert client.get(SUGGESTIONS_URL).status_code == 401


def test_teaching_context_forbidden_for_student(student_client: TestClient) -> None:
    assert student_client.get(SUGGESTIONS_URL).status_code == 403
