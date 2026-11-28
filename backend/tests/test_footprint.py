from datetime import date, datetime, timedelta

from app import consumption as consumption_core
from app.models import (
    Consumable,
    Course,
    CourseRecipeItem,
    CourseRecipeSnapshot,
    CourseSession,
    InventoryEvent,
    OperationLog,
    Requisition,
    User,
)
from app.routers.requisitions import estimate_supported_sessions
from app.security import hash_password
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

FOOTPRINT_URL = "/api/v1/requisitions/my-footprint"
REQUISITIONS_URL = "/api/v1/requisitions"
SESSION_DATE = date(2026, 12, 10)


def _login(client: TestClient, account: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"account": account, "password": password}
    )
    assert response.status_code == 200, response.text


def _login_student(client: TestClient) -> None:
    _login(client, "student01", "Student@123")


def _user(db: Session, account: str) -> User:
    return db.scalars(select(User).where(User.account == account)).one()


def _consumable(
    db: Session,
    *,
    name: str = "焊丝",
    spec: str = "φ1.2mm",
    unit: str = "卷",
    stock: int = 100,
    safety_stock: int = 5,
    class_id: int = 1,
) -> Consumable:
    item = Consumable(
        name=name,
        spec=spec,
        unit=unit,
        location="一号柜",
        safety_stock=safety_stock,
        current_stock=stock,
        class_id=class_id,
        status="enabled",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _flow(
    db: Session,
    *,
    user_id: int,
    consumable_id: int,
    quantity: int,
    flow_type: str = "normal",
    flow_status: str = "active",
    created_at: datetime | None = None,
    reversal_of: int | None = None,
    stock_after: int = 0,
    class_id: int = 1,
) -> Requisition:
    record = Requisition(
        consumable_id=consumable_id,
        user_id=user_id,
        quantity=quantity,
        stock_after=stock_after,
        type=flow_type,
        status=flow_status,
        reversal_of=reversal_of,
        class_id=class_id,
        created_at=created_at or datetime.now().replace(microsecond=0),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def _course(
    db: Session, *, name: str = "焊接实训", class_id: int = 1
) -> Course:
    teacher = _user(db, "teacher01")
    course = Course(
        class_id=class_id,
        name=name,
        class_label="计算机 2301",
        semester="2026 秋",
        status="enabled",
        created_by_id=teacher.id,
        created_by_name=teacher.name,
    )
    db.add(course)
    db.commit()
    db.refresh(course)
    return course


def _recipe(
    db: Session,
    *,
    course: Course,
    consumable: Consumable,
    quantity_per_person: int = 1,
    version: int = 1,
) -> CourseRecipeSnapshot:
    teacher = _user(db, "teacher01")
    snapshot = CourseRecipeSnapshot(
        course_id=course.id,
        class_id=course.class_id,
        version=version,
        created_by_id=teacher.id,
        created_by_name=teacher.name,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    db.add(
        CourseRecipeItem(
            snapshot_id=snapshot.id,
            consumable_id=consumable.id,
            consumable_name=consumable.name,
            unit=consumable.unit,
            quantity_per_person=quantity_per_person,
        )
    )
    db.commit()
    return snapshot


def _session(
    db: Session,
    *,
    course: Course,
    headcount: int = 10,
    status: str = "planned",
    seq: int = 1,
) -> CourseSession:
    teacher = _user(db, "teacher01")
    session = CourseSession(
        course_id=course.id,
        class_id=course.class_id,
        seq=seq,
        session_date=SESSION_DATE,
        headcount=headcount,
        class_label=course.class_label,
        status=status,
        created_by_id=teacher.id,
        created_by_name=teacher.name,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def test_footprint_requires_login(client: TestClient) -> None:
    assert client.get(FOOTPRINT_URL).status_code == 401


def test_footprint_rejects_unsupported_window(
    client: TestClient, db_session: Session
) -> None:
    _login_student(client)

    response = client.get(FOOTPRINT_URL, params={"window": 14})

    assert response.status_code == 422


def test_footprint_matches_own_flow_rows(client: TestClient, db_session: Session) -> None:
    student = _user(db_session, "student01")
    item = _consumable(db_session)
    now = datetime.now().replace(microsecond=0)
    origin = _flow(
        db_session,
        user_id=student.id,
        consumable_id=item.id,
        quantity=10,
        created_at=now - timedelta(minutes=2),
    )
    _flow(
        db_session,
        user_id=student.id,
        consumable_id=item.id,
        quantity=3,
        created_at=now - timedelta(minutes=1),
    )
    _flow(
        db_session,
        user_id=student.id,
        consumable_id=item.id,
        quantity=10,
        flow_type="reversal",
        reversal_of=origin.id,
        created_at=now,
    )
    _login_student(client)

    data = client.get(FOOTPRINT_URL, params={"window": 90}).json()
    listed = client.get(
        REQUISITIONS_URL, params={"consumable_id": item.id, "page_size": 100}
    ).json()["items"]

    normal_rows = [row for row in listed if row["type"] == "normal"]
    reversal_rows = [row for row in listed if row["type"] == "reversal"]
    row = data["items"][0]
    assert row["requisition_count"] == len(normal_rows) == 2
    assert row["issued_quantity"] == sum(item["quantity"] for item in normal_rows) == 13
    assert (
        row["reversed_quantity"]
        == sum(item["quantity"] for item in reversal_rows)
        == 10
    )
    assert row["net_quantity"] == max(13 - 10, 0) == 3
    assert row["last_at"].startswith(now.isoformat()[:16])
    assert data["requisition_count"] == 2
    assert data["user_id"] == student.id
    assert data["user_name"] == student.name


def test_footprint_unit_totals_stay_within_units(
    client: TestClient, db_session: Session
) -> None:
    student = _user(db_session, "student01")
    roll = _consumable(db_session, name="焊丝", unit="卷")
    sheet = _consumable(db_session, name="砂纸", unit="张")
    pen = _consumable(db_session, name="记号笔", unit="支")
    now = datetime.now().replace(microsecond=0)
    _flow(db_session, user_id=student.id, consumable_id=roll.id, quantity=6, created_at=now)
    _flow(db_session, user_id=student.id, consumable_id=sheet.id, quantity=4, created_at=now)
    _flow(db_session, user_id=student.id, consumable_id=pen.id, quantity=2, created_at=now)
    _login_student(client)

    data = client.get(FOOTPRINT_URL, params={"window": 7}).json()

    totals = {row["unit"]: row for row in data["unit_totals"]}
    assert set(totals) == {"卷", "张", "支"}
    assert totals["卷"]["net_quantity"] == 6
    assert totals["张"]["net_quantity"] == 4
    assert totals["支"]["net_quantity"] == 2
    assert totals["卷"]["consumables"] == 1
    assert totals["卷"]["requisition_count"] == 1
    assert data["requisition_count"] == 3
    assert [row["unit"] for row in data["unit_totals"]] == ["卷", "张", "支"]


def test_footprint_net_quantity_clamps_at_zero(
    client: TestClient, db_session: Session
) -> None:
    student = _user(db_session, "student01")
    item = _consumable(db_session)
    now = datetime.now().replace(microsecond=0)
    origin = _flow(
        db_session, user_id=student.id, consumable_id=item.id, quantity=5, created_at=now
    )
    _flow(
        db_session,
        user_id=student.id,
        consumable_id=item.id,
        quantity=6,
        flow_type="reversal",
        reversal_of=origin.id,
        created_at=now,
    )
    _login_student(client)

    data = client.get(FOOTPRINT_URL, params={"window": 7}).json()

    assert data["items"][0]["net_quantity"] == 0
    assert data["unit_totals"][0]["net_quantity"] == 0


def test_footprint_support_uses_latest_recipe_snapshot(
    client: TestClient, db_session: Session
) -> None:
    student = _user(db_session, "student01")
    item = _consumable(db_session, stock=100)
    _flow(
        db_session,
        user_id=student.id,
        consumable_id=item.id,
        quantity=8,
        created_at=datetime.now().replace(microsecond=0),
    )
    course = _course(db_session)
    _recipe(db_session, course=course, consumable=item, quantity_per_person=2, version=1)
    _session(db_session, course=course, headcount=10)
    _login_student(client)

    data = client.get(FOOTPRINT_URL, params={"window": 90}).json()

    support = data["items"][0]["support"]
    assert support["supported_sessions"] == 5
    assert support["reason_code"] is None
    assert support["course_id"] == course.id
    assert support["course_name"] == course.name
    assert support["recipe_version"] == 1
    assert support["headcount"] == 10
    assert support["quantity_per_person"] == 2
    assert support["required_quantity"] == 20


def test_footprint_support_without_recipe_returns_null(
    client: TestClient, db_session: Session
) -> None:
    student = _user(db_session, "student01")
    item = _consumable(db_session)
    _flow(
        db_session,
        user_id=student.id,
        consumable_id=item.id,
        quantity=1,
        created_at=datetime.now().replace(microsecond=0),
    )
    _login_student(client)

    data = client.get(FOOTPRINT_URL, params={"window": 90}).json()

    support = data["items"][0]["support"]
    assert support["supported_sessions"] is None
    assert support["reason_code"] == "no_recipe"
    assert support["course_id"] is None


def test_footprint_support_without_headcount_returns_null(
    client: TestClient, db_session: Session
) -> None:
    student = _user(db_session, "student01")
    item = _consumable(db_session)
    _flow(
        db_session,
        user_id=student.id,
        consumable_id=item.id,
        quantity=1,
        created_at=datetime.now().replace(microsecond=0),
    )
    course = _course(db_session)
    _recipe(db_session, course=course, consumable=item, quantity_per_person=2)
    _session(db_session, course=course, headcount=0, seq=1)
    _login_student(client)

    data = client.get(FOOTPRINT_URL, params={"window": 90}).json()

    support = data["items"][0]["support"]
    assert support["supported_sessions"] is None
    assert support["reason_code"] == "no_headcount"
    assert support["headcount"] == 0
    assert support["required_quantity"] is None


def test_estimate_supported_sessions_reason_codes() -> None:
    assert estimate_supported_sessions(100, 0, 10) == (None, "zero_demand", None)
    assert estimate_supported_sessions(100, 2, None) == (None, "no_headcount", None)
    assert estimate_supported_sessions(100, 2, 0) == (None, "no_headcount", None)
    assert estimate_supported_sessions(100, 2, 10) == (5, None, 20)
    assert estimate_supported_sessions(19, 2, 10) == (0, None, 20)
    assert estimate_supported_sessions(0, 2, 10) == (0, None, 20)
    assert estimate_supported_sessions(-5, 2, 10) == (0, None, 20)


def test_footprint_hides_other_students(
    client: TestClient, db_session: Session
) -> None:
    student = _user(db_session, "student01")
    other = User(
        account="student02",
        password_hash=hash_password("Student@123"),
        name="李同学",
        role="student",
        status="enabled",
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    own_item = _consumable(db_session, name="焊丝")
    other_item = _consumable(db_session, name="砂纸", unit="张")
    now = datetime.now().replace(microsecond=0)
    _flow(db_session, user_id=student.id, consumable_id=own_item.id, quantity=5, created_at=now)
    _flow(db_session, user_id=other.id, consumable_id=other_item.id, quantity=9, created_at=now)
    _login_student(client)

    data = client.get(
        FOOTPRINT_URL, params={"window": 90, "user_id": other.id}
    ).json()

    assert data["user_id"] == student.id
    assert data["requisition_count"] == 1
    assert [row["name"] for row in data["items"]] == ["焊丝"]
    assert all(row["consumable_id"] == own_item.id for row in data["items"])


def test_footprint_staff_sees_only_own_rows(
    client: TestClient, db_session: Session
) -> None:
    student = _user(db_session, "student01")
    item = _consumable(db_session)
    _flow(
        db_session,
        user_id=student.id,
        consumable_id=item.id,
        quantity=4,
        created_at=datetime.now().replace(microsecond=0),
    )
    _login(client, "teacher01", "Teacher@123")

    data = client.get(FOOTPRINT_URL, params={"window": 90}).json()

    assert data["user_id"] == _user(db_session, "teacher01").id
    assert data["items"] == []
    assert data["unit_totals"] == []


def test_footprint_is_read_only(client: TestClient, db_session: Session) -> None:
    student = _user(db_session, "student01")
    item = _consumable(db_session, stock=42)
    _flow(
        db_session,
        user_id=student.id,
        consumable_id=item.id,
        quantity=3,
        created_at=datetime.now().replace(microsecond=0),
    )
    _login_student(client)
    stock_before = db_session.get(Consumable, item.id).current_stock
    events_before = db_session.scalar(
        select(func.count()).select_from(InventoryEvent)
    )
    logs_before = db_session.scalar(select(func.count()).select_from(OperationLog))

    client.get(FOOTPRINT_URL, params={"window": 90})
    client.get(FOOTPRINT_URL, params={"window": 30})
    db_session.expire_all()

    assert db_session.get(Consumable, item.id).current_stock == stock_before == 42
    assert db_session.scalar(select(func.count()).select_from(InventoryEvent)) == events_before
    assert db_session.scalar(select(func.count()).select_from(OperationLog)) == logs_before


def test_footprint_window_boundaries(client: TestClient, db_session: Session) -> None:
    student = _user(db_session, "student01")
    item = _consumable(db_session)
    inside = consumption_core.local_day_start(6) + timedelta(hours=1)
    outside = consumption_core.local_day_start(7) + timedelta(hours=1)
    _flow(db_session, user_id=student.id, consumable_id=item.id, quantity=3, created_at=inside)
    _flow(db_session, user_id=student.id, consumable_id=item.id, quantity=5, created_at=outside)
    _login_student(client)

    seven = client.get(FOOTPRINT_URL, params={"window": 7}).json()
    thirty = client.get(FOOTPRINT_URL, params={"window": 30}).json()

    assert seven["items"][0]["net_quantity"] == 3
    assert thirty["items"][0]["net_quantity"] == 8
