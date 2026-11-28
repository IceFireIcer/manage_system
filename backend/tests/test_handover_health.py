from datetime import datetime, timedelta

from app.business_time import today
from app.models import (
    Consumable,
    Course,
    CourseRecipeItem,
    CourseRecipeSnapshot,
    CourseSession,
    IncidentReport,
    InventoryCount,
    InventoryCountLine,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

HEALTH_URL = "/api/v1/handover/health"
RISK_URL = "/api/v1/courses/risk"


def _now(offset_minutes: int = 0) -> datetime:
    return datetime(2026, 11, 28, 10, 0, 0) + timedelta(minutes=offset_minutes)


def _user(db_session: Session, account: str) -> User:
    return db_session.scalars(select(User).where(User.account == account)).one()


def _consumable(
    db_session: Session,
    *,
    name: str = "焊丝",
    current_stock: int = 2,
    safety_stock: int = 5,
    class_id: int = 1,
) -> Consumable:
    consumable = Consumable(
        name=name,
        spec="φ1.2mm",
        unit="卷",
        location="一号柜",
        safety_stock=safety_stock,
        current_stock=current_stock,
        class_id=class_id,
        status="enabled",
        created_at=_now(),
        updated_at=_now(),
    )
    db_session.add(consumable)
    db_session.commit()
    return consumable


def _posted_count(
    db_session: Session,
    operator: User,
    *,
    order_no: str,
    posted_at: datetime,
    diffs: list[int | None],
    status: str = "posted",
) -> InventoryCount:
    count = InventoryCount(
        order_no=order_no,
        request_id=f"req-{order_no}",
        status=status,
        count_at=posted_at,
        counter_id=operator.id,
        counter_name=operator.name,
        counter_role=operator.role,
        reviewer_id=operator.id,
        reviewer_name=operator.name,
        reviewer_role=operator.role,
        operator_id=operator.id,
        operator_name=operator.name,
        operator_role=operator.role,
        class_id=operator.class_id,
        posted_at=posted_at if status == "posted" else None,
        created_at=posted_at,
        updated_at=posted_at,
    )
    db_session.add(count)
    db_session.flush()
    for line_no, diff in enumerate(diffs, start=1):
        consumable = _consumable(
            db_session, name=f"{order_no}-耗材{line_no}", class_id=operator.class_id
        )
        db_session.add(
            InventoryCountLine(
                count_id=count.id,
                line_no=line_no,
                consumable_id=consumable.id,
                consumable_name=consumable.name,
                unit=consumable.unit,
                baseline_stock=10,
                baseline_seq=0,
                counted_qty=None if diff is None else 10 + diff,
                period_delta=0,
                diff=diff,
                created_at=posted_at,
                updated_at=posted_at,
            )
        )
    db_session.commit()
    return count


def _incident(
    db_session: Session,
    consumable: Consumable,
    reporter: User,
    *,
    status: str,
    class_id: int = 1,
) -> IncidentReport:
    report = IncidentReport(
        class_id=class_id,
        consumable_id=consumable.id,
        reporter_id=reporter.id,
        reporter_name=reporter.name,
        reporter_role=reporter.role,
        location="一号柜",
        description="账实不符",
        status=status,
        created_at=_now(),
        updated_at=_now(),
    )
    db_session.add(report)
    db_session.commit()
    return report


def _create_course(client: TestClient, name: str) -> dict:
    response = client.post(
        "/api/v1/courses",
        json={"name": name, "class_label": "计算机 2301", "semester": "2026 秋"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _save_recipe(
    client: TestClient, course_id: int, items: list[tuple[int, int]]
) -> None:
    response = client.post(
        f"/api/v1/courses/{course_id}/recipes",
        json={
            "items": [
                {"consumable_id": consumable_id, "quantity_per_person": quantity}
                for consumable_id, quantity in items
            ]
        },
    )
    assert response.status_code == 201, response.text


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


def _seed_other_class_course(db_session: Session) -> None:
    teacher = _user(db_session, "teacher01")
    consumable = _consumable(
        db_session, name="外班耗材", current_stock=0, safety_stock=0, class_id=2
    )
    course = Course(
        class_id=2,
        name="外班课程",
        class_label="计算机 2302",
        semester="2026 秋",
        status="enabled",
        created_by_id=teacher.id,
        created_by_name=teacher.name,
    )
    db_session.add(course)
    db_session.flush()
    snapshot = CourseRecipeSnapshot(
        course_id=course.id,
        class_id=2,
        version=1,
        created_by_id=teacher.id,
        created_by_name=teacher.name,
    )
    db_session.add(snapshot)
    db_session.flush()
    db_session.add(
        CourseRecipeItem(
            snapshot_id=snapshot.id,
            consumable_id=consumable.id,
            consumable_name=consumable.name,
            unit=consumable.unit,
            quantity_per_person=1,
        )
    )
    db_session.add(
        CourseSession(
            course_id=course.id,
            class_id=2,
            seq=1,
            session_date=today() + timedelta(days=1),
            headcount=10,
            class_label=course.class_label,
            status="planned",
            recipe_snapshot_id=snapshot.id,
            created_by_id=teacher.id,
            created_by_name=teacher.name,
        )
    )
    db_session.commit()


def test_requires_login_and_staff(client: TestClient) -> None:
    assert client.get(HEALTH_URL).status_code == 401


def test_student_forbidden(student_client: TestClient) -> None:
    assert student_client.get(HEALTH_URL).status_code == 403


def test_teacher_and_admin_can_read(
    teacher_client: TestClient, admin_client: TestClient
) -> None:
    for current in (teacher_client, admin_client):
        response = current.get(HEALTH_URL)
        assert response.status_code == 200, response.text
        assert response.json()["class_id"] == 1


def test_stock_diff_without_posted_count(
    admin_client: TestClient, db_session: Session
) -> None:
    operator = _user(db_session, "admin01")
    _posted_count(
        db_session,
        operator,
        order_no="PD-OPEN-001",
        posted_at=_now(),
        diffs=[1, 0],
        status="open",
    )

    body = admin_client.get(HEALTH_URL).json()
    assert body["stock_diff_count"] == 0
    assert body["stock_diff_available"] is False


def test_stock_diff_counts_latest_posted_count(
    admin_client: TestClient, db_session: Session
) -> None:
    operator = _user(db_session, "admin01")
    _posted_count(
        db_session,
        operator,
        order_no="PD-OLD-001",
        posted_at=_now(),
        diffs=[1, 2, 0],
    )
    _posted_count(
        db_session,
        operator,
        order_no="PD-NEW-001",
        posted_at=_now(30),
        diffs=[0, 3, None],
    )
    _posted_count(
        db_session,
        operator,
        order_no="PD-OPEN-002",
        posted_at=_now(60),
        diffs=[5, 6],
        status="open",
    )

    body = admin_client.get(HEALTH_URL).json()
    assert body["stock_diff_count"] == 1
    assert body["stock_diff_available"] is True


def test_open_incident_count_matches_queue(
    admin_client: TestClient, db_session: Session
) -> None:
    reporter = _user(db_session, "student01")
    consumable = _consumable(db_session)
    for status, class_id in (
        ("open", 1),
        ("confirmed", 1),
        ("rejected", 1),
        ("converted_to_count", 1),
        ("open", 2),
    ):
        _incident(
            db_session, consumable, reporter, status=status, class_id=class_id
        )

    body = admin_client.get(HEALTH_URL).json()
    assert body["open_incident_count"] == 2


def test_course_risk_matches_risk_endpoint(
    admin_client: TestClient, db_session: Session
) -> None:
    low = _consumable(db_session, name="低库存耗材", current_stock=0, safety_stock=0)
    enough = _consumable(
        db_session, name="充足耗材", current_stock=100, safety_stock=0
    )
    course = _create_course(admin_client, "风险课程")
    _save_recipe(admin_client, course["id"], [(low.id, 1), (enough.id, 1)])
    soon = today() + timedelta(days=1)
    _create_session(admin_client, course["id"], 1, soon.isoformat(), 10)
    _create_session(
        admin_client, course["id"], 2, (today() + timedelta(days=2)).isoformat(), 10
    )
    _create_session(
        admin_client,
        course["id"],
        3,
        (today() + timedelta(days=3)).isoformat(),
        10,
        status="cancelled",
    )
    _create_session(
        admin_client,
        course["id"],
        4,
        (today() + timedelta(days=30)).isoformat(),
        10,
    )
    _seed_other_class_course(db_session)

    risks = admin_client.get(RISK_URL, params={"days": 14}).json()["items"]
    expected = sum(
        1 for risk in risks if risk["level"] in ("insufficient", "critical")
    )
    assert expected == 2

    body = admin_client.get(HEALTH_URL).json()
    assert body["course_risk_count"] == expected


def test_expiry_is_not_enabled(admin_client: TestClient) -> None:
    body = admin_client.get(HEALTH_URL).json()
    expiry = body["expiry"]
    assert expiry["supported"] is False
    assert expiry["label"] == "未启用"
    assert expiry["count"] is None


def test_health_is_read_only(
    admin_client: TestClient, db_session: Session
) -> None:
    operator = _user(db_session, "admin01")
    reporter = _user(db_session, "student01")
    consumable = _consumable(db_session, current_stock=7)
    _posted_count(
        db_session, operator, order_no="PD-RO-001", posted_at=_now(), diffs=[1]
    )
    _incident(db_session, consumable, reporter, status="open")

    def snapshot() -> tuple:
        return (
            db_session.scalar(select(func.count()).select_from(InventoryCount)),
            db_session.scalar(select(func.count()).select_from(InventoryCountLine)),
            db_session.scalar(select(func.count()).select_from(IncidentReport)),
            db_session.scalar(select(func.count()).select_from(CourseSession)),
            db_session.scalar(select(func.count()).select_from(Course)),
        )

    before = snapshot()
    assert admin_client.get(HEALTH_URL).status_code == 200
    assert admin_client.get(HEALTH_URL).status_code == 200
    db_session.expire_all()
    assert snapshot() == before
    db_session.refresh(consumable)
    assert consumable.current_stock == 7
