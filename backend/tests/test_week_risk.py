from datetime import date, timedelta

from app import business_time
from app.business_time import today
from app.models import (
    Consumable,
    CourseRecipeSnapshot,
    InventoryEvent,
    RestockOrder,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

WEEK_URL = "/api/v1/courses/risk/week"
COVERAGE_URL = "/api/v1/courses/risk/arrival-coverage"
RISK_DATE = date(2026, 12, 10)


def _consumable(
    db: Session,
    name: str = "焊丝",
    unit: str = "卷",
    stock: int = 10,
    safety_stock: int = 0,
) -> Consumable:
    item = Consumable(
        name=name,
        spec="φ1.2mm",
        unit=unit,
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


def _course(client: TestClient, name: str) -> dict:
    response = client.post(
        "/api/v1/courses",
        json={"name": name, "class_label": "计算机 2301", "semester": "2026 秋"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _save_recipe(
    client: TestClient,
    course_id: int,
    items: list[dict],
) -> dict:
    response = client.post(
        f"/api/v1/courses/{course_id}/recipes",
        json={"items": items},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _single_recipe(consumable_id: int, quantity: int = 1) -> list[dict]:
    return [{"consumable_id": consumable_id, "quantity_per_person": quantity}]


def _create_session(
    client: TestClient,
    course_id: int,
    seq: int,
    session_date: str,
    headcount: int,
    status: str = "planned",
) -> dict:
    response = client.post(
        f"/api/v1/courses/{course_id}/sessions",
        json={
            "seq": seq,
            "session_date": session_date,
            "headcount": headcount,
            "status": status,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _teacher(db: Session) -> User:
    return db.scalars(select(User).where(User.account == "teacher01")).first()


def _event_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(InventoryEvent)) or 0


def _snapshot_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(CourseRecipeSnapshot)) or 0


def test_week_aggregates_same_day_sessions(
    teacher_client: TestClient, db_session: Session
) -> None:
    consumable = _consumable(db_session, stock=10)
    first = _course(teacher_client, "周聚合焊接 A")
    second = _course(teacher_client, "周聚合焊接 B")
    _save_recipe(teacher_client, first["id"], _single_recipe(consumable.id))
    _save_recipe(teacher_client, second["id"], _single_recipe(consumable.id))
    day = today() + timedelta(days=1)
    day_text = day.isoformat()
    session_a = _create_session(teacher_client, first["id"], 1, day_text, 6)
    session_b = _create_session(teacher_client, second["id"], 1, day_text, 6)

    for course_id, session_id in (
        (first["id"], session_a["id"]),
        (second["id"], session_b["id"]),
    ):
        single = teacher_client.get(
            f"/api/v1/courses/{course_id}/sessions/{session_id}/risk"
        )
        assert single.status_code == 200, single.text
        assert single.json()["level"] == "sufficient"

    response = teacher_client.get(WEEK_URL, params={"days": 7})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["days"] == 7
    assert body["lead_days"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert body["total"] == 1
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["session_date"] == day_text
    assert item["consumable_id"] == consumable.id
    assert item["required_quantity"] == 12
    assert item["available_quantity"] == 10
    assert item["gap"] == 2
    assert item["level"] == "insufficient"
    assert item["arrival_deadline"] == (day - timedelta(days=1)).isoformat()
    assert {row["session_id"] for row in item["sessions"]} == {
        session_a["id"],
        session_b["id"],
    }
    assert sum(row["required_quantity"] for row in item["sessions"]) == 12
    assert body["unconfigured"] == []


def test_week_stacks_multiple_sessions_same_day(
    teacher_client: TestClient, db_session: Session
) -> None:
    consumable = _consumable(db_session, stock=5)
    course = _course(teacher_client, "周聚合叠加课程")
    _save_recipe(teacher_client, course["id"], _single_recipe(consumable.id))
    day_text = (today() + timedelta(days=2)).isoformat()
    for seq, headcount in ((1, 4), (2, 4), (3, 4)):
        _create_session(teacher_client, course["id"], seq, day_text, headcount)

    body = teacher_client.get(WEEK_URL, params={"days": 7}).json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["required_quantity"] == 12
    assert item["available_quantity"] == 5
    assert item["gap"] == 7
    assert item["level"] == "insufficient"
    assert len(item["sessions"]) == 3
    assert {row["seq"] for row in item["sessions"]} == {1, 2, 3}


def test_week_separates_dates_and_reports_unconfigured(
    teacher_client: TestClient, db_session: Session
) -> None:
    consumable = _consumable(db_session, stock=10)
    course = _course(teacher_client, "周聚合跨日课程")
    _save_recipe(teacher_client, course["id"], _single_recipe(consumable.id))
    day_one = today() + timedelta(days=1)
    day_two = today() + timedelta(days=2)
    _create_session(teacher_client, course["id"], 1, day_one.isoformat(), 5)
    _create_session(teacher_client, course["id"], 2, day_two.isoformat(), 5)

    missing = _course(teacher_client, "周聚合未配置课程")
    _create_session(teacher_client, missing["id"], 1, day_one.isoformat(), 8)

    zero = _course(teacher_client, "周聚合零人数课程")
    _save_recipe(teacher_client, zero["id"], _single_recipe(consumable.id))
    _create_session(teacher_client, zero["id"], 1, day_one.isoformat(), 0)

    body = teacher_client.get(WEEK_URL, params={"days": 7}).json()
    assert body["total"] == 2
    assert [item["session_date"] for item in body["items"]] == [
        day_one.isoformat(),
        day_two.isoformat(),
    ]
    assert {item["consumable_id"] for item in body["items"]} == {consumable.id}
    reasons = {
        (row["course_name"], row["reason"]) for row in body["unconfigured"]
    }
    assert ("周聚合未配置课程", "missing_recipe") in reasons
    assert ("周聚合零人数课程", "missing_headcount") in reasons


def test_week_pagination_and_query_limits(
    teacher_client: TestClient, db_session: Session
) -> None:
    first = _consumable(db_session, name="耗材甲", stock=100)
    second = _consumable(db_session, name="耗材乙", stock=100)
    course = _course(teacher_client, "周聚合分页课程")
    _save_recipe(
        teacher_client,
        course["id"],
        _single_recipe(first.id) + _single_recipe(second.id),
    )
    _create_session(
        teacher_client, course["id"], 1, (today() + timedelta(days=1)).isoformat(), 3
    )

    first_page = teacher_client.get(
        WEEK_URL, params={"days": 7, "page": 1, "page_size": 1}
    )
    assert first_page.status_code == 200, first_page.text
    first_body = first_page.json()
    assert first_body["total"] == 2
    assert first_body["page"] == 1
    assert first_body["page_size"] == 1
    assert len(first_body["items"]) == 1

    second_page = teacher_client.get(
        WEEK_URL, params={"days": 7, "page": 2, "page_size": 1}
    ).json()
    assert len(second_page["items"]) == 1
    assert second_page["items"][0]["consumable_id"] != first_body["items"][0][
        "consumable_id"
    ]

    assert (
        teacher_client.get(WEEK_URL, params={"page_size": 101}).status_code == 422
    )
    assert teacher_client.get(WEEK_URL, params={"days": 0}).status_code == 422
    assert teacher_client.get(WEEK_URL, params={"days": 61}).status_code == 422


def test_arrival_coverage_matches_upcoming_sessions(
    admin_client: TestClient, db_session: Session
) -> None:
    consumable = _consumable(db_session, stock=0)
    course = _course(admin_client, "到货覆盖课程")
    _save_recipe(admin_client, course["id"], _single_recipe(consumable.id))
    day = today() + timedelta(days=2)
    session = _create_session(admin_client, course["id"], 1, day.isoformat(), 5)

    first = admin_client.post(
        "/api/v1/inventory/restock/orders",
        json={
            "items": [{"consumable_id": consumable.id, "quantity": 4}],
            "source": "采购回库",
        },
    )
    assert first.status_code == 201, first.text
    first_order = first.json()["order_no"]

    db_session.expire_all()
    before_events = _event_count(db_session)
    before_stock = db_session.get(Consumable, consumable.id).current_stock
    first_body = admin_client.get(
        COVERAGE_URL, params={"order_no": first_order, "days": 7}
    )
    assert first_body.status_code == 200, first_body.text
    db_session.expire_all()
    assert db_session.get(Consumable, consumable.id).current_stock == before_stock
    assert _event_count(db_session) == before_events
    first_data = first_body.json()
    assert first_data["matched"] is True
    assert first_data["status"] == "matched"
    assert first_data["order_no"] == first_order
    assert first_data["total"] == 1
    assert len(first_data["items"]) == 1
    row = first_data["items"][0]
    assert row["session_id"] == session["id"]
    assert row["covered"] is False
    line = row["items"][0]
    assert line["required_quantity"] == 5
    assert line["covered_quantity"] == 4
    assert line["level_before"] == "insufficient"
    assert line["level_after"] == "insufficient"

    second = admin_client.post(
        "/api/v1/inventory/restock/orders",
        json={
            "items": [{"consumable_id": consumable.id, "quantity": 1}],
            "source": "采购回库",
        },
    )
    assert second.status_code == 201, second.text
    second_order = second.json()["order_no"]

    second_data = admin_client.get(
        COVERAGE_URL, params={"order_no": second_order, "days": 7}
    ).json()
    assert second_data["matched"] is True
    assert second_data["total"] == 1
    covered_row = second_data["items"][0]
    assert covered_row["covered"] is True
    covered_line = covered_row["items"][0]
    assert covered_line["covered_quantity"] == 5
    assert covered_line["level_before"] == "insufficient"
    assert covered_line["level_after"] == "sufficient"


def test_arrival_coverage_unmatched_and_not_arrived(
    teacher_client: TestClient, db_session: Session
) -> None:
    unmatched = teacher_client.get(
        COVERAGE_URL, params={"order_no": "RSK-NOT-EXIST"}
    )
    assert unmatched.status_code == 200, unmatched.text
    unmatched_body = unmatched.json()
    assert unmatched_body["matched"] is False
    assert unmatched_body["status"] == "unmatched"
    assert unmatched_body["items"] == []
    assert "未匹配" in unmatched_body["message"]

    user = _teacher(db_session)
    order = RestockOrder(
        order_no="RSK-PENDING-1",
        request_id="week-risk-pending-1",
        status="processing",
        operator_id=user.id,
        operator_name=user.name,
        operator_role=user.role,
        class_id=1,
        business_at=business_time.now(),
        items_signature="[]",
    )
    db_session.add(order)
    db_session.commit()

    pending = teacher_client.get(
        COVERAGE_URL, params={"order_no": "RSK-PENDING-1"}
    )
    assert pending.status_code == 200, pending.text
    pending_body = pending.json()
    assert pending_body["matched"] is False
    assert pending_body["status"] == "not_arrived"
    assert pending_body["items"] == []


def test_week_and_coverage_are_read_only(
    teacher_client: TestClient, db_session: Session
) -> None:
    consumable = _consumable(db_session, stock=3)
    course = _course(teacher_client, "周聚合只读课程")
    _save_recipe(teacher_client, course["id"], _single_recipe(consumable.id, 2))
    _create_session(
        teacher_client, course["id"], 1, (today() + timedelta(days=1)).isoformat(), 10
    )
    before_events = _event_count(db_session)
    before_snapshots = _snapshot_count(db_session)

    assert teacher_client.get(WEEK_URL, params={"days": 7}).status_code == 200
    assert (
        teacher_client.get(
            COVERAGE_URL, params={"order_no": "RSK-NOT-EXIST"}
        ).status_code
        == 200
    )

    db_session.expire_all()
    assert db_session.get(Consumable, consumable.id).current_stock == 3
    assert _event_count(db_session) == before_events == 0
    assert _snapshot_count(db_session) == before_snapshots == 1


def test_admin_can_read_week_risk(
    admin_client: TestClient, db_session: Session
) -> None:
    _consumable(db_session, stock=10)
    course = _course(admin_client, "周聚合管理员课程")
    _create_session(
        admin_client, course["id"], 1, (today() + timedelta(days=1)).isoformat(), 4
    )

    week = admin_client.get(WEEK_URL, params={"days": 7})
    assert week.status_code == 200, week.text
    assert week.json()["total"] == 0
    assert len(week.json()["unconfigured"]) == 1
    assert week.json()["unconfigured"][0]["reason"] == "missing_recipe"


def test_student_cannot_read_week_risk_or_coverage(
    student_client: TestClient,
) -> None:
    assert student_client.get(WEEK_URL, params={"days": 7}).status_code == 403
    assert (
        student_client.get(
            COVERAGE_URL, params={"order_no": "RSK-NOT-EXIST"}
        ).status_code
        == 403
    )
