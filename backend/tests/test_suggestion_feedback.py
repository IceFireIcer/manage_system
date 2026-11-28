import json
from datetime import timedelta

import pytest
from app import business_time
from app.agent import service
from app.models import (
    Consumable,
    InventoryEvent,
    OperationLog,
    RestockDisposition,
    SuggestionFeedback,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

SUGGESTIONS_URL = "/api/v1/inventory/suggestions"
FEEDBACK_URL = f"{SUGGESTIONS_URL}/feedback"
CONSUMABLES_URL = "/api/v1/consumables"
ADMIN = ("admin01", "Admin@123")
TEACHER = ("teacher01", "Teacher@123")


@pytest.fixture(autouse=True)
def _reset_agent_cache():
    service.clear_cache()
    yield
    service.clear_cache()


def _login(client: TestClient, credentials: tuple[str, str]) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"account": credentials[0], "password": credentials[1]},
    )
    assert response.status_code == 200, response.text


def _create_consumable(client: TestClient, **overrides) -> dict:
    payload = {
        "name": "焊丝",
        "spec": "φ1.2mm",
        "unit": "卷",
        "location": "一号柜",
        "safety_stock": 5,
        "current_stock": 0,
    }
    payload.update(overrides)
    response = client.post(CONSUMABLES_URL, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _feedback(
    client: TestClient,
    consumable_id: int,
    reason: str = "schedule_adjust",
    note: str | None = "课时顺延",
):
    return client.post(
        f"{SUGGESTIONS_URL}/{consumable_id}/feedback",
        json={"reason": reason, "note": note},
    )


def _suggestion(client: TestClient, consumable_id: int) -> dict:
    items = client.get(SUGGESTIONS_URL).json()["items"]
    return next(item for item in items if item["consumable_id"] == consumable_id)


def _logs(db_session) -> list[OperationLog]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(OperationLog)
            .where(OperationLog.action == "suggestion_feedback")
            .order_by(OperationLog.id)
        ).all()
    )


def _stock(db_session, consumable_id: int) -> int:
    db_session.expire_all()
    return db_session.get(Consumable, consumable_id).current_stock


def test_feedback_rejects_anonymous(client: TestClient) -> None:
    assert client.get(FEEDBACK_URL).status_code == 401
    assert _feedback(client, 1).status_code == 401


def test_feedback_rejects_student(student_client: TestClient) -> None:
    assert student_client.get(FEEDBACK_URL).status_code == 403
    assert _feedback(student_client, 1).status_code == 403


def test_feedback_accepts_teacher(client: TestClient) -> None:
    _login(client, ADMIN)
    consumable = _create_consumable(client)
    _login(client, TEACHER)

    response = _feedback(client, consumable["id"], "substitute", "已有铝板替代")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["feedback_name"] == "王老师"
    assert body["reason"] == "substitute"
    assert body["note"] == "已有铝板替代"


def test_feedback_reason_and_note_validation(client: TestClient) -> None:
    _login(client, ADMIN)
    consumable = _create_consumable(client)

    assert _feedback(client, consumable["id"], "other", None).status_code == 422
    assert _feedback(client, consumable["id"], "other", "   ").status_code == 422
    assert _feedback(client, consumable["id"], "unknown", "说明").status_code == 422

    accepted = _feedback(client, consumable["id"], "low_usage", None)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["note"] is None


def test_feedback_is_idempotent_and_logs_changes(
    client: TestClient, db_session
) -> None:
    _login(client, ADMIN)
    consumable = _create_consumable(client)
    _suggestion(client, consumable["id"])

    first = _feedback(client, consumable["id"], "substitute", "已用铝板替代")
    assert first.status_code == 200, first.text
    body = first.json()

    repeated = _feedback(client, consumable["id"], "substitute", "已用铝板替代")
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["id"] == body["id"]
    assert len(_logs(db_session)) == 1

    changed = _feedback(client, consumable["id"], "other", "改用木料")
    assert changed.status_code == 200, changed.text
    assert changed.json()["id"] == body["id"]
    assert changed.json()["reason"] == "other"
    assert changed.json()["note"] == "改用木料"

    logs = _logs(db_session)
    assert len(logs) == 2
    assert logs[0].target_type == "consumable"
    assert logs[0].target_id == consumable["id"]
    before = json.loads(logs[-1].before_value)
    after = json.loads(logs[-1].after_value)
    assert before == {
        "suggestion_key": body["suggestion_key"],
        "reason": "substitute",
        "note": "已用铝板替代",
    }
    assert after["reason"] == "other"
    assert after["note"] == "改用木料"


def test_feedback_unique_per_member(client: TestClient, db_session) -> None:
    _login(client, ADMIN)
    consumable = _create_consumable(client)
    _suggestion(client, consumable["id"])
    admin_feedback = _feedback(client, consumable["id"]).json()

    _login(client, TEACHER)
    teacher_feedback = _feedback(client, consumable["id"], "substitute", "已有替代")
    assert teacher_feedback.status_code == 200, teacher_feedback.text
    assert teacher_feedback.json()["feedback_by"] != admin_feedback["feedback_by"]

    admin_id = db_session.scalars(
        select(User.id).where(User.account == "admin01")
    ).one()
    now = business_time.now()
    db_session.add(
        SuggestionFeedback(
            class_id=1,
            consumable_id=consumable["id"],
            suggestion_key=admin_feedback["suggestion_key"],
            reason="low_usage",
            note=None,
            feedback_by=admin_id,
            feedback_name="李班委",
            created_at=now,
            updated_at=now,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_feedback_search_filters_by_consumable_and_time(
    client: TestClient, db_session
) -> None:
    _login(client, ADMIN)
    wire = _create_consumable(client)
    plate = _create_consumable(client, name="铝板", spec="2mm")
    _suggestion(client, wire["id"])
    _suggestion(client, plate["id"])
    wire_feedback = _feedback(client, wire["id"], "schedule_adjust", "课时顺延").json()
    plate_feedback = _feedback(client, plate["id"], "other", "改用木料").json()

    all_rows = client.get(FEEDBACK_URL).json()
    assert all_rows["page"] == 1
    assert all_rows["page_size"] == 20
    assert all_rows["total"] == 2
    by_consumable = {row["consumable_id"]: row for row in all_rows["items"]}
    assert by_consumable[wire["id"]]["consumable_name"] == "焊丝"
    assert by_consumable[wire["id"]]["feedback_name"] == "李班委"
    assert by_consumable[wire["id"]]["reason"] == "schedule_adjust"
    assert by_consumable[wire["id"]]["created_at"]

    only_wire = client.get(
        FEEDBACK_URL, params={"consumable_id": wire["id"]}
    ).json()
    assert only_wire["total"] == 1
    assert only_wire["items"][0]["id"] == wire_feedback["id"]

    first_page = client.get(
        FEEDBACK_URL, params={"page": 1, "page_size": 1}
    ).json()
    second_page = client.get(
        FEEDBACK_URL, params={"page": 2, "page_size": 1}
    ).json()
    assert first_page["total"] == 2
    assert len(first_page["items"]) == 1
    assert len(second_page["items"]) == 1
    assert {first_page["items"][0]["id"], second_page["items"][0]["id"]} == {
        wire_feedback["id"],
        plate_feedback["id"],
    }

    record = db_session.get(SuggestionFeedback, wire_feedback["id"])
    record.created_at = business_time.now() - timedelta(days=10)
    db_session.commit()

    today = business_time.today().isoformat()
    recent = client.get(FEEDBACK_URL, params={"from": today, "to": today}).json()
    assert recent["total"] == 1
    assert recent["items"][0]["id"] == plate_feedback["id"]

    old_day = (business_time.today() - timedelta(days=10)).isoformat()
    old = client.get(FEEDBACK_URL, params={"from": old_day, "to": old_day}).json()
    assert old["total"] == 1
    assert old["items"][0]["id"] == wire_feedback["id"]

    reversed_range = client.get(
        FEEDBACK_URL, params={"from": today, "to": old_day}
    )
    assert reversed_range.status_code == 400


def test_feedback_does_not_change_stock_suggestion_or_disposition(
    client: TestClient, db_session
) -> None:
    _login(client, ADMIN)
    consumable = _create_consumable(client)
    before = _suggestion(client, consumable["id"])
    assert before["disposition_status"] == "pending"

    response = _feedback(client, consumable["id"], "schedule_adjust", "课时调整")
    assert response.status_code == 200, response.text

    after = _suggestion(client, consumable["id"])
    assert after["current_stock"] == before["current_stock"] == 0
    assert after["gap"] == before["gap"]
    assert after["tier"] == before["tier"]
    assert after["recommended_quantity"] == before["recommended_quantity"]
    assert after["disposition_status"] == "pending"
    assert after["latest_feedback"]["reason"] == "schedule_adjust"
    assert after["latest_feedback"]["note"] == "课时调整"
    assert after["latest_feedback"]["feedback_name"] == "李班委"

    assert _stock(db_session, consumable["id"]) == 0
    assert (
        db_session.scalars(
            select(InventoryEvent).where(
                InventoryEvent.consumable_id == consumable["id"]
            )
        ).all()
        == []
    )
    assert (
        db_session.scalars(
            select(RestockDisposition).where(
                RestockDisposition.consumable_id == consumable["id"]
            )
        ).all()
        == []
    )
