from datetime import date, timedelta

import pytest
from app.business_time import today
from app.models import (
    Consumable,
    Course,
    CourseRecipeItem,
    CourseRecipeSnapshot,
    CourseSession,
    InventoryEvent,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

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


def _course(client: TestClient, name: str = "焊接实训") -> dict:
    response = client.post(
        "/api/v1/courses",
        json={"name": name, "class_label": "计算机 2301", "semester": "2026 秋"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _save_recipe(
    client: TestClient, course_id: int, consumable_id: int, quantity: int = 1
) -> dict:
    response = client.post(
        f"/api/v1/courses/{course_id}/recipes",
        json={"items": [{"consumable_id": consumable_id, "quantity_per_person": quantity}]},
    )
    assert response.status_code == 201, response.text
    return response.json()


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


def _seed_session(db: Session, status: str = "planned") -> tuple[Course, CourseSession]:
    user = _teacher(db)
    course = Course(
        class_id=1,
        name="风险课程",
        class_label="计算机 2301",
        semester="2026 秋",
        status="enabled",
        created_by_id=user.id,
        created_by_name=user.name,
    )
    db.add(course)
    db.commit()
    db.refresh(course)
    session = CourseSession(
        course_id=course.id,
        class_id=1,
        seq=1,
        session_date=today() + timedelta(days=1),
        headcount=10,
        class_label=course.class_label,
        status=status,
        created_by_id=user.id,
        created_by_name=user.name,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return course, session


@pytest.mark.parametrize(
    (
        "stock",
        "safety_stock",
        "quantity",
        "headcount",
        "expected_level",
        "expected_gap",
        "deadline_offset",
    ),
    [
        (20, 5, 1, 10, "sufficient", 0, None),
        (10, 15, 1, 10, "critical", 0, 1),
        (3, 0, 1, 10, "insufficient", 7, 1),
        (11, 0, 2, 6, "insufficient", 1, 1),
        (12, 0, 2, 6, "sufficient", 0, None),
        (5, 0, 1, 0, "unconfigured", 0, None),
    ],
    ids=[
        "sufficient",
        "critical-at-requirement",
        "insufficient",
        "insufficient-uses-headcount",
        "exact-requirement",
        "missing-headcount",
    ],
)
def test_session_risk_levels(
    teacher_client: TestClient,
    db_session: Session,
    stock: int,
    safety_stock: int,
    quantity: int,
    headcount: int,
    expected_level: str,
    expected_gap: int,
    deadline_offset: int | None,
) -> None:
    consumable = _consumable(
        db_session, stock=stock, safety_stock=safety_stock
    )
    course = _course(teacher_client, f"风险课程-{expected_level}")
    _save_recipe(teacher_client, course["id"], consumable.id, quantity)
    session = _create_session(
        teacher_client, course["id"], 1, RISK_DATE.isoformat(), headcount
    )

    response = teacher_client.get(
        f"/api/v1/courses/{course['id']}/sessions/{session['id']}/risk"
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["level"] == expected_level
    assert body["gap"] == expected_gap
    assert body["lead_days"] == 1
    if deadline_offset is None:
        assert body["arrival_deadline"] is None
    else:
        assert body["arrival_deadline"] == (
            RISK_DATE - timedelta(days=deadline_offset)
        ).isoformat()

    if expected_level == "unconfigured":
        assert body["configured"] is False
        assert body["items"] == []
        assert body["gap_consumable_name"] is None
    else:
        assert body["configured"] is True
        assert body["gap_consumable_name"] == "焊丝"
        item = body["items"][0]
        assert item["required_quantity"] == quantity * headcount
        assert item["available_quantity"] == stock
        assert item["safety_stock"] == safety_stock
        assert item["gap"] == expected_gap
        assert item["level"] == expected_level


def test_missing_snapshot_is_unconfigured(teacher_client: TestClient) -> None:
    course = _course(teacher_client, "无配方课程")
    session = _create_session(teacher_client, course["id"], 1, RISK_DATE.isoformat(), 20)

    response = teacher_client.get(
        f"/api/v1/courses/{course['id']}/sessions/{session['id']}/risk"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["level"] == "unconfigured"
    assert body["configured"] is False
    assert body["recipe_version"] is None
    assert body["arrival_deadline"] is None
    assert body["gap"] == 0
    assert body["items"] == []


def test_missing_consumable_is_unconfigured(
    teacher_client: TestClient, db_session: Session
) -> None:
    course = _course(teacher_client, "缺耗材课程")
    user = _teacher(db_session)
    snapshot = CourseRecipeSnapshot(
        course_id=course["id"],
        class_id=1,
        version=1,
        created_by_id=user.id,
        created_by_name=user.name,
    )
    db_session.add(snapshot)
    db_session.flush()
    db_session.add(
        CourseRecipeItem(
            snapshot_id=snapshot.id,
            consumable_id=999999,
            consumable_name="幽灵耗材",
            unit="个",
            quantity_per_person=1,
        )
    )
    db_session.commit()
    session = _create_session(teacher_client, course["id"], 1, RISK_DATE.isoformat(), 10)

    body = teacher_client.get(
        f"/api/v1/courses/{course['id']}/sessions/{session['id']}/risk"
    ).json()
    assert body["level"] == "unconfigured"
    assert body["recipe_version"] == 1
    assert body["gap"] == 0
    assert body["items"] == []


def test_lead_days_is_configurable(
    teacher_client: TestClient, db_session: Session
) -> None:
    consumable = _consumable(db_session, stock=2)
    course = _course(teacher_client, "提前期课程")
    _save_recipe(teacher_client, course["id"], consumable.id, 2)
    session = _create_session(teacher_client, course["id"], 1, RISK_DATE.isoformat(), 10)

    for lead_days in (0, 3):
        body = teacher_client.get(
            f"/api/v1/courses/{course['id']}/sessions/{session['id']}/risk",
            params={"lead_days": lead_days},
        ).json()
        assert body["level"] == "insufficient"
        assert body["gap"] == 18
        assert body["lead_days"] == lead_days
        assert body["arrival_deadline"] == (
            RISK_DATE - timedelta(days=lead_days)
        ).isoformat()


def test_course_risk_list_filters_upcoming_planned_sessions(
    teacher_client: TestClient, db_session: Session
) -> None:
    consumable = _consumable(db_session, stock=100)
    course = _course(teacher_client, "列表风险课程")
    _save_recipe(teacher_client, course["id"], consumable.id, 1)
    start = today()
    soon = _create_session(
        teacher_client, course["id"], 1, (start + timedelta(days=1)).isoformat(), 10
    )
    later = _create_session(
        teacher_client, course["id"], 2, (start + timedelta(days=30)).isoformat(), 10
    )
    _create_session(
        teacher_client,
        course["id"],
        3,
        (start - timedelta(days=1)).isoformat(),
        10,
        status="completed",
    )
    _create_session(
        teacher_client,
        course["id"],
        4,
        (start + timedelta(days=2)).isoformat(),
        10,
        status="cancelled",
    )

    narrow = teacher_client.get("/api/v1/courses/risk", params={"days": 14})
    assert narrow.status_code == 200, narrow.text
    assert narrow.json()["days"] == 14
    assert narrow.json()["lead_days"] == 1
    assert [item["session_id"] for item in narrow.json()["items"]] == [soon["id"]]

    wide = teacher_client.get(
        "/api/v1/courses/risk", params={"days": 40, "lead_days": 2}
    )
    assert wide.status_code == 200, wide.text
    assert [item["session_id"] for item in wide.json()["items"]] == [
        soon["id"],
        later["id"],
    ]
    assert wide.json()["lead_days"] == 2


def test_risk_is_read_only(
    teacher_client: TestClient, db_session: Session
) -> None:
    consumable = _consumable(db_session, stock=4)
    course = _course(teacher_client, "只读风险课程")
    _save_recipe(teacher_client, course["id"], consumable.id, 2)
    session = _create_session(teacher_client, course["id"], 1, RISK_DATE.isoformat(), 10)
    before_events = db_session.scalar(select(func.count()).select_from(InventoryEvent))
    before_snapshots = db_session.scalar(
        select(func.count()).select_from(CourseRecipeSnapshot)
    )

    assert (
        teacher_client.get(
            f"/api/v1/courses/{course['id']}/sessions/{session['id']}/risk"
        ).status_code
        == 200
    )
    assert teacher_client.get("/api/v1/courses/risk").status_code == 200

    db_session.refresh(consumable)
    assert consumable.current_stock == 4
    assert (
        db_session.scalar(select(func.count()).select_from(InventoryEvent))
        == before_events
        == 0
    )
    assert (
        db_session.scalar(select(func.count()).select_from(CourseRecipeSnapshot))
        == before_snapshots
        == 1
    )


def test_admin_can_read_course_risk(
    admin_client: TestClient, db_session: Session
) -> None:
    course, session = _seed_session(db_session)

    single = admin_client.get(
        f"/api/v1/courses/{course.id}/sessions/{session.id}/risk"
    )
    assert single.status_code == 200, single.text
    assert single.json()["level"] == "unconfigured"

    listing = admin_client.get("/api/v1/courses/risk")
    assert listing.status_code == 200, listing.text


def test_student_cannot_read_course_risk(
    student_client: TestClient, db_session: Session
) -> None:
    course, session = _seed_session(db_session)

    assert (
        student_client.get(
            f"/api/v1/courses/{course.id}/sessions/{session.id}/risk"
        ).status_code
        == 403
    )
    assert student_client.get("/api/v1/courses/risk").status_code == 403
