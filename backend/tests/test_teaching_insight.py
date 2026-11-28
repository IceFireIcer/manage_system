from datetime import datetime, timedelta

from app import consumption as consumption_core
from app.business_time import today
from app.models import (
    Consumable,
    Course,
    CourseSession,
    HandoverChecklist,
    IncidentReport,
    InventoryCount,
    InventoryCountLine,
    Requisition,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

INSIGHT_URL = "/api/v1/consumption/teaching-insight"
PASSWORD = "Student@123"
NOW = datetime(2026, 11, 28, 10, 0, 0)
WINDOW = 7


def _user(db_session: Session, account: str) -> User:
    return db_session.scalars(select(User).where(User.account == account)).one()


def _consumable(
    db_session: Session, *, name: str = "焊丝", class_id: int = 1, stock: int = 50
) -> Consumable:
    item = Consumable(
        name=name,
        spec="φ1.2mm",
        unit="卷",
        location="一号柜",
        safety_stock=0,
        current_stock=stock,
        class_id=class_id,
        status="enabled",
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(item)
    db_session.commit()
    return item


def _flow(
    db_session: Session,
    consumable: Consumable,
    quantity: int,
    *,
    created_at: datetime,
    flow_type: str = "normal",
    flow_status: str = "active",
    class_id: int = 1,
) -> Requisition:
    record = Requisition(
        consumable_id=consumable.id,
        user_id=_user(db_session, "student01").id,
        quantity=quantity,
        stock_after=0,
        type=flow_type,
        status=flow_status,
        class_id=class_id,
        created_at=created_at,
    )
    db_session.add(record)
    db_session.commit()
    return record


def _current_time() -> datetime:
    return consumption_core.window_end() - timedelta(minutes=1)


def _previous_time() -> datetime:
    return consumption_core.window_start(WINDOW) - timedelta(minutes=1)


def _seed_flows(
    db_session: Session,
    *,
    current: int,
    previous: int,
    class_id: int = 1,
) -> Consumable:
    consumable = _consumable(db_session, class_id=class_id)
    if previous:
        _flow(
            db_session,
            consumable,
            previous,
            created_at=_previous_time(),
            class_id=class_id,
        )
    if current:
        _flow(
            db_session,
            consumable,
            current,
            created_at=_current_time(),
            class_id=class_id,
        )
    return consumable


def _course(db_session: Session, *, class_id: int = 1, name: str = "实训课") -> Course:
    teacher = _user(db_session, "teacher01")
    course = Course(
        class_id=class_id,
        name=name,
        class_label="计算机 2301",
        semester="2026 秋",
        status="enabled",
        created_by_id=teacher.id,
        created_by_name=teacher.name,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(course)
    db_session.commit()
    return course


def _session(
    db_session: Session,
    course: Course,
    *,
    seq: int,
    session_date,
    status: str = "planned",
) -> CourseSession:
    session = CourseSession(
        course_id=course.id,
        class_id=course.class_id,
        seq=seq,
        session_date=session_date,
        headcount=30,
        class_label=course.class_label,
        status=status,
        created_by_id=_user(db_session, "teacher01").id,
        created_by_name="王老师",
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(session)
    db_session.commit()
    return session


def _seed_calendar(
    db_session: Session,
    *,
    in_window: bool,
    class_id: int = 1,
) -> Course:
    course = _course(db_session, class_id=class_id)
    session_date = today() if in_window else today() + timedelta(days=30)
    _session(db_session, course, seq=1, session_date=session_date)
    return course


def _posted_count(
    db_session: Session,
    *,
    diffs: list[int | None],
    class_id: int = 1,
    order_no: str = "PD-INS-001",
) -> InventoryCount:
    operator = _user(db_session, "admin01")
    count = InventoryCount(
        order_no=order_no,
        request_id=f"req-{order_no}",
        status="posted",
        count_at=NOW,
        counter_id=operator.id,
        counter_name=operator.name,
        counter_role=operator.role,
        reviewer_id=operator.id,
        reviewer_name=operator.name,
        reviewer_role=operator.role,
        operator_id=operator.id,
        operator_name=operator.name,
        operator_role=operator.role,
        class_id=class_id,
        posted_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(count)
    db_session.flush()
    for index, diff in enumerate(diffs, start=1):
        item = _consumable(
            db_session, name=f"{order_no}-耗材{index}", class_id=class_id
        )
        db_session.add(
            InventoryCountLine(
                count_id=count.id,
                line_no=index,
                consumable_id=item.id,
                consumable_name=item.name,
                unit=item.unit,
                baseline_stock=10,
                baseline_seq=0,
                counted_qty=None if diff is None else 10 + diff,
                period_delta=0,
                diff=diff,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    db_session.commit()
    return count


def _confirmed_handover(
    db_session: Session, *, version: int = 1, class_id: int = 1
) -> HandoverChecklist:
    operator = _user(db_session, "admin01")
    checklist = HandoverChecklist(
        class_id=class_id,
        version=version,
        status="confirmed",
        outgoing_id=operator.id,
        outgoing_name=operator.name,
        outgoing_account=operator.account,
        incoming_id=_user(db_session, "teacher01").id,
        incoming_name="王老师",
        incoming_account="teacher01",
        snapshot_at=NOW,
        confirmed_at=NOW,
        confirmed_by_id=operator.id,
        confirmed_by_name=operator.name,
        confirmed_by_account=operator.account,
        created_at=NOW,
    )
    db_session.add(checklist)
    db_session.commit()
    return checklist


def _incident(
    db_session: Session,
    consumable: Consumable,
    *,
    status: str = "open",
    class_id: int = 1,
) -> IncidentReport:
    reporter = _user(db_session, "student01")
    report = IncidentReport(
        class_id=class_id,
        consumable_id=consumable.id,
        reporter_id=reporter.id,
        reporter_name=reporter.name,
        reporter_role=reporter.role,
        location="一号柜",
        description="账实不符",
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(report)
    db_session.commit()
    return report


def _get(client: TestClient, window: int = WINDOW) -> dict:
    response = client.get(INSIGHT_URL, params={"window": window})
    assert response.status_code == 200, response.text
    return response.json()


def test_requires_login_and_staff(client: TestClient) -> None:
    assert client.get(INSIGHT_URL).status_code == 401


def test_student_forbidden(student_client: TestClient) -> None:
    response = student_client.get(INSIGHT_URL)
    assert response.status_code == 403
    assert response.json()["detail"] == "无权限访问"


def test_teacher_and_admin_can_read(
    teacher_client: TestClient, admin_client: TestClient
) -> None:
    for current in (teacher_client, admin_client):
        body = _get(current)
        assert body["class_id"] == 1
        assert body["window"] == WINDOW


def test_invalid_window_rejected(teacher_client: TestClient) -> None:
    response = teacher_client.get(INSIGHT_URL, params={"window": 14})
    assert response.status_code == 422


def test_no_calendar_when_no_sessions(
    admin_client: TestClient, db_session: Session
) -> None:
    _seed_flows(db_session, current=30, previous=10)

    body = _get(admin_client)
    assert body["state"] == "no_calendar"
    assert body["calendar_available"] is False
    assert body["training_session_count"] == 0
    assert body["current_net_quantity"] == 30
    assert body["previous_net_quantity"] == 10


def test_training_growth_when_planned_session_in_window(
    admin_client: TestClient, db_session: Session
) -> None:
    _seed_flows(db_session, current=30, previous=10)
    _seed_calendar(db_session, in_window=True)

    body = _get(admin_client)
    assert body["state"] == "training_growth"
    assert body["calendar_available"] is True
    assert body["training_session_count"] == 1
    assert "计划课次" in body["message"]


def test_non_training_growth_when_rising_without_session(
    admin_client: TestClient, db_session: Session
) -> None:
    _seed_flows(db_session, current=30, previous=10)
    _seed_calendar(db_session, in_window=False)

    body = _get(admin_client)
    assert body["state"] == "non_training_growth"
    assert body["training_session_count"] == 0
    assert body["change_ratio"] == 2.0
    assert "无计划课次" in body["message"]


def test_non_training_growth_requires_threshold(
    admin_client: TestClient, db_session: Session
) -> None:
    _seed_flows(db_session, current=12, previous=10)
    _seed_calendar(db_session, in_window=False)

    body = _get(admin_client)
    assert body["state"] == "stable"
    assert body["change_ratio"] == 0.2


def test_decline_when_consumption_falls(
    admin_client: TestClient, db_session: Session
) -> None:
    _seed_flows(db_session, current=5, previous=10)
    _seed_calendar(db_session, in_window=True)

    body = _get(admin_client)
    assert body["state"] == "decline"
    assert body["change_quantity"] == -5


def test_stable_when_consumption_flat(
    admin_client: TestClient, db_session: Session
) -> None:
    _seed_flows(db_session, current=10, previous=10)
    _seed_calendar(db_session, in_window=True)

    body = _get(admin_client)
    assert body["state"] == "stable"
    assert body["change_quantity"] == 0


def test_other_class_flows_excluded(admin_client: TestClient, db_session: Session) -> None:
    _seed_flows(db_session, current=10, previous=10)
    _seed_calendar(db_session, in_window=True)
    other = _consumable(db_session, name="外班耗材", class_id=2)
    _flow(
        db_session,
        other,
        100,
        created_at=_current_time(),
        class_id=2,
    )
    _flow(
        db_session,
        other,
        100,
        created_at=_previous_time(),
        class_id=2,
    )

    body = _get(admin_client)
    assert body["class_id"] == 1
    assert body["current_net_quantity"] == 10
    assert body["previous_net_quantity"] == 10


def test_positive_feedback_requires_posted_count(
    admin_client: TestClient, db_session: Session
) -> None:
    _seed_flows(db_session, current=10, previous=10)
    _seed_calendar(db_session, in_window=True)

    body = _get(admin_client)
    feedback = body["positive_feedback"]
    assert feedback["eligible"] is False
    assert feedback["reason"] == "stock_diff_unavailable"
    assert feedback["message"] is None


def test_positive_feedback_requires_zero_diff(
    admin_client: TestClient, db_session: Session
) -> None:
    _seed_flows(db_session, current=10, previous=10)
    _seed_calendar(db_session, in_window=True)
    _posted_count(db_session, diffs=[1, 0])

    body = _get(admin_client)
    assert body["positive_feedback"] == {
        "eligible": False,
        "reason": "stock_diff_mismatch",
        "message": None,
    }


def test_positive_feedback_requires_confirmed_handover(
    admin_client: TestClient, db_session: Session
) -> None:
    _seed_flows(db_session, current=10, previous=10)
    _seed_calendar(db_session, in_window=True)
    _posted_count(db_session, diffs=[0, None])

    body = _get(admin_client)
    assert body["positive_feedback"]["eligible"] is False
    assert body["positive_feedback"]["reason"] == "no_confirmed_handover"


def test_positive_feedback_requires_no_open_incident(
    admin_client: TestClient, db_session: Session
) -> None:
    consumable = _seed_flows(db_session, current=10, previous=10)
    _seed_calendar(db_session, in_window=True)
    _posted_count(db_session, diffs=[0])
    _confirmed_handover(db_session)
    _incident(db_session, consumable, status="confirmed")

    body = _get(admin_client)
    assert body["positive_feedback"]["eligible"] is False
    assert body["positive_feedback"]["reason"] == "open_incidents"


def test_positive_feedback_eligible_when_all_conditions_met(
    admin_client: TestClient, db_session: Session
) -> None:
    _seed_flows(db_session, current=10, previous=10)
    _seed_calendar(db_session, in_window=True)
    _posted_count(db_session, diffs=[0, None])
    _confirmed_handover(db_session)
    _incident(db_session, _consumable(db_session, name="已关闭"), status="rejected")

    body = _get(admin_client)
    feedback = body["positive_feedback"]
    assert feedback["eligible"] is True
    assert feedback["reason"] is None
    assert feedback["message"]


def test_response_has_no_ranking_or_personal_fields(
    admin_client: TestClient, db_session: Session
) -> None:
    _seed_flows(db_session, current=10, previous=10)
    _seed_calendar(db_session, in_window=True)
    _seed_calendar(db_session, in_window=False, class_id=2)

    body = _get(admin_client)
    assert set(body) == {
        "window",
        "generated_at",
        "class_id",
        "state",
        "message",
        "calendar_available",
        "training_session_count",
        "current_net_quantity",
        "previous_net_quantity",
        "change_quantity",
        "change_ratio",
        "positive_feedback",
    }
    assert set(body["positive_feedback"]) == {"eligible", "reason", "message"}
    serialized = str(body)
    for forbidden in ("ranking", "rank", "user_id", "user_name", "other_class"):
        assert forbidden not in serialized
    assert "计算机 2302" not in serialized


def test_insight_is_read_only(admin_client: TestClient, db_session: Session) -> None:
    consumable = _seed_flows(db_session, current=30, previous=10)
    _seed_calendar(db_session, in_window=True)
    _posted_count(db_session, diffs=[0])
    _confirmed_handover(db_session)

    def snapshot() -> tuple:
        db_session.expire_all()
        return (
            db_session.scalar(select(func.count()).select_from(Requisition)),
            db_session.scalar(select(func.count()).select_from(CourseSession)),
            db_session.scalar(select(func.count()).select_from(InventoryCount)),
            db_session.scalar(select(func.count()).select_from(HandoverChecklist)),
        )

    before = snapshot()
    assert _get(admin_client)["positive_feedback"]["eligible"] is True
    assert _get(admin_client)["state"] == "training_growth"
    assert snapshot() == before
    db_session.refresh(consumable)
    assert consumable.current_stock == 50
