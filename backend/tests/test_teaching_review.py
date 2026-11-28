from datetime import datetime, timedelta

from app import business_time
from app.models import (
    Consumable,
    Course,
    CourseRecipeSnapshot,
    CourseSession,
    InventoryEvent,
    Requisition,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

COURSES_URL = "/api/v1/courses"


def _consumable(
    db: Session, name: str = "焊丝", stock: int = 100, unit: str = "卷"
) -> Consumable:
    item = Consumable(
        name=name,
        spec="φ1.2mm",
        unit=unit,
        location="一号柜",
        safety_stock=0,
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
        COURSES_URL,
        json={
            "name": name,
            "class_label": "计算机 2301",
            "semester": "2026 秋",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _recipe(client: TestClient, course_id: int, items: list[tuple[int, int]]) -> dict:
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
    return response.json()


def _session(
    client: TestClient,
    course_id: int,
    seq: int,
    headcount: int,
    session_date: str | None = None,
) -> dict:
    response = client.post(
        f"{COURSES_URL}/{course_id}/sessions",
        json={
            "seq": seq,
            "session_date": session_date or business_time.today().isoformat(),
            "headcount": headcount,
            "status": "planned",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _student_id(db: Session) -> int:
    return db.scalars(select(User).where(User.account == "student01")).one().id


def _flow(
    db: Session,
    consumable_id: int,
    quantity: int,
    at: datetime,
    *,
    flow_type: str = "normal",
    flow_status: str = "active",
    reversal_of: int | None = None,
    class_id: int = 1,
) -> Requisition:
    record = Requisition(
        consumable_id=consumable_id,
        user_id=_student_id(db),
        quantity=quantity,
        stock_after=0,
        type=flow_type,
        status=flow_status,
        reversal_of=reversal_of,
        class_id=class_id,
        created_at=at,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def _at(session_date: str, hour: int = 10) -> datetime:
    day = datetime.fromisoformat(session_date)
    return business_time.day_start(day.date()) + timedelta(hours=hour)


def _create_review(
    client: TestClient,
    course_id: int,
    session_id: int,
    reason_tags: list[str] | None = None,
    teacher_note: str | None = None,
):
    return client.post(
        f"{COURSES_URL}/{course_id}/sessions/{session_id}/reviews",
        json={"reason_tags": reason_tags or [], "teacher_note": teacher_note},
    )


def test_review_uses_session_day_class_net(
    teacher_client: TestClient, db_session: Session
) -> None:
    wire = _consumable(db_session, "焊丝")
    spare = _consumable(db_session, "焊条")
    course = _course(teacher_client)
    _recipe(teacher_client, course["id"], [(wire.id, 2)])
    session = _session(teacher_client, course["id"], 1, 10)

    issued = _flow(db_session, wire.id, 15, _at(session["session_date"], 9))
    _flow(
        db_session,
        wire.id,
        5,
        _at(session["session_date"], 15),
        flow_type="reversal",
        reversal_of=issued.id,
    )
    _flow(
        db_session,
        wire.id,
        7,
        _at(session["session_date"], 16),
        flow_type="reversal",
        flow_status="rejected",
    )
    _flow(
        db_session,
        wire.id,
        100,
        _at(session["session_date"]) - timedelta(days=1),
    )
    _flow(db_session, spare.id, 100, _at(session["session_date"]))
    _flow(db_session, wire.id, 100, _at(session["session_date"]), class_id=2)

    response = _create_review(teacher_client, course["id"], session["id"])
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["actual_net_quantity"] == 10
    assert body["standard_quantity"] == 20
    assert body["deviation_ratio"] == -0.5
    assert body["deviation_available"] is True
    assert body["version"] == 1
    assert body["course_id"] == course["id"]
    assert body["session_id"] == session["id"]
    assert body["snapshot_id"] == session["recipe_snapshot_id"]
    assert body["recipe_version"] == 1
    assert body["headcount"] == 10
    assert [item["consumable_id"] for item in body["items"]] == [wire.id]
    assert body["items"][0]["standard_quantity"] == 20
    assert body["reason_candidates"]

    listed = teacher_client.get(
        f"{COURSES_URL}/{course['id']}/sessions/{session['id']}/reviews"
    )
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [body["id"]]

    single = teacher_client.get(f"{COURSES_URL}/reviews/{body['id']}")
    assert single.status_code == 200, single.text
    assert single.json()["actual_net_quantity"] == 10


def test_review_zero_standard_skips_ratio(
    teacher_client: TestClient, db_session: Session
) -> None:
    wire = _consumable(db_session)
    course = _course(teacher_client, "零人课程")
    _recipe(teacher_client, course["id"], [(wire.id, 2)])
    session = _session(teacher_client, course["id"], 1, 0)

    response = _create_review(teacher_client, course["id"], session["id"])
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["standard_quantity"] == 0
    assert body["actual_net_quantity"] == 0
    assert body["deviation_ratio"] is None
    assert body["deviation_available"] is False
    assert body["items"][0]["standard_quantity"] == 0


def test_review_keeps_history_and_allows_tag_note_edit(
    teacher_client: TestClient, db_session: Session
) -> None:
    wire = _consumable(db_session)
    course = _course(teacher_client, "历史课程")
    _recipe(teacher_client, course["id"], [(wire.id, 2)])
    session = _session(teacher_client, course["id"], 1, 10)
    _flow(db_session, wire.id, 12, _at(session["session_date"]))

    first = _create_review(
        teacher_client,
        course["id"],
        session["id"],
        ["学生操作不熟"],
        "第一次复盘",
    )
    assert first.status_code == 201, first.text
    first_body = first.json()
    assert first_body["version"] == 1

    _flow(db_session, wire.id, 8, _at(session["session_date"], 11))

    second = _create_review(
        teacher_client, course["id"], session["id"], [], "第二次复盘"
    )
    assert second.status_code == 201, second.text
    second_body = second.json()
    assert second_body["version"] == 2
    assert second_body["actual_net_quantity"] == 20

    history = teacher_client.get(
        f"{COURSES_URL}/{course['id']}/sessions/{session['id']}/reviews"
    ).json()
    assert [item["version"] for item in history["items"]] == [2, 1]
    assert history["items"][1]["actual_net_quantity"] == 12
    assert history["items"][1]["reason_tags"] == ["学生操作不熟"]
    assert history["items"][1]["teacher_note"] == "第一次复盘"

    patched = teacher_client.patch(
        f"{COURSES_URL}/reviews/{first_body['id']}",
        json={
            "reason_tags": ["配方估算偏高", "  课时调整  ", "配方估算偏高"],
            "teacher_note": "补充结论",
        },
    )
    assert patched.status_code == 200, patched.text
    patched_body = patched.json()
    assert patched_body["version"] == 1
    assert patched_body["actual_net_quantity"] == 12
    assert patched_body["reason_tags"] == ["配方估算偏高", "课时调整"]
    assert patched_body["teacher_note"] == "补充结论"
    assert patched_body["reason_candidates"]


def test_review_is_not_affected_by_later_recipe(
    teacher_client: TestClient, db_session: Session
) -> None:
    wire = _consumable(db_session)
    course = _course(teacher_client, "快照课程")
    _recipe(teacher_client, course["id"], [(wire.id, 2)])
    session = _session(teacher_client, course["id"], 1, 10)
    _recipe(teacher_client, course["id"], [(wire.id, 5)])

    body = _create_review(teacher_client, course["id"], session["id"]).json()
    assert body["recipe_version"] == 1
    assert body["standard_quantity"] == 20


def test_review_requires_snapshot(teacher_client: TestClient) -> None:
    course = _course(teacher_client, "无配方课程")
    session = _session(teacher_client, course["id"], 1, 10)

    response = _create_review(teacher_client, course["id"], session["id"])
    assert response.status_code == 400, response.text


def test_review_does_not_touch_stock_or_events(
    teacher_client: TestClient, db_session: Session
) -> None:
    wire = _consumable(db_session, stock=42)
    course = _course(teacher_client, "只读课程")
    _recipe(teacher_client, course["id"], [(wire.id, 3)])
    session = _session(teacher_client, course["id"], 1, 10)
    _flow(db_session, wire.id, 4, _at(session["session_date"]))

    events_before = (
        db_session.scalar(select(func.count()).select_from(InventoryEvent)) or 0
    )
    snapshots_before = (
        db_session.scalar(select(func.count()).select_from(CourseRecipeSnapshot)) or 0
    )

    response = _create_review(teacher_client, course["id"], session["id"])
    assert response.status_code == 201, response.text

    db_session.expire_all()
    db_session.refresh(wire)
    assert wire.current_stock == 42
    assert (
        db_session.scalar(select(func.count()).select_from(InventoryEvent)) or 0
    ) == events_before == 0
    assert (
        db_session.scalar(select(func.count()).select_from(CourseRecipeSnapshot)) or 0
    ) == snapshots_before == 1


def test_admin_can_generate_and_edit_review(
    admin_client: TestClient, db_session: Session
) -> None:
    wire = _consumable(db_session)
    course = _course(admin_client, "班委课程")
    _recipe(admin_client, course["id"], [(wire.id, 1)])
    session = _session(admin_client, course["id"], 1, 5)

    created = _create_review(admin_client, course["id"], session["id"])
    assert created.status_code == 201, created.text
    body = created.json()

    patched = admin_client.patch(
        f"{COURSES_URL}/reviews/{body['id']}", json={"teacher_note": "班委复核"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["teacher_note"] == "班委复核"
    assert admin_client.get(f"{COURSES_URL}/reviews/{body['id']}").status_code == 200


def test_review_forbidden_for_student(
    student_client: TestClient, db_session: Session
) -> None:

    user = db_session.scalars(select(User).where(User.account == "teacher01")).one()

    course = Course(
        class_id=1,
        name="权限课程",
        class_label="计算机 2301",
        semester="2026 秋",
        status="enabled",
        created_by_id=user.id,
        created_by_name=user.name,
    )
    db_session.add(course)
    db_session.commit()
    db_session.refresh(course)
    session = CourseSession(
        course_id=course.id,
        class_id=1,
        seq=1,
        session_date=business_time.today(),
        headcount=5,
        class_label=course.class_label,
        created_by_id=user.id,
        created_by_name=user.name,
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    list_url = f"{COURSES_URL}/{course.id}/sessions/{session.id}/reviews"
    single_url = f"{COURSES_URL}/reviews/999999"

    assert student_client.get(list_url).status_code == 403
    assert student_client.get(single_url).status_code == 403
    assert (
        student_client.post(
            list_url, json={"reason_tags": [], "teacher_note": None}
        ).status_code
        == 403
    )
    assert (
        student_client.patch(single_url, json={"teacher_note": "学生"}).status_code
        == 403
    )



def test_review_requires_login(client: TestClient) -> None:
    assert client.get(f"{COURSES_URL}/reviews/999999").status_code == 401
    assert (
        client.get(f"{COURSES_URL}/1/sessions/1/reviews").status_code == 401
    )
    assert (
        client.post(
            f"{COURSES_URL}/1/sessions/1/reviews", json={}
        ).status_code
        == 401
    )


def test_review_missing_resources_return_404(
    teacher_client: TestClient, db_session: Session
) -> None:
    wire = _consumable(db_session)
    course = _course(teacher_client, "越权课程")
    _recipe(teacher_client, course["id"], [(wire.id, 1)])
    session = _session(teacher_client, course["id"], 1, 5)

    assert teacher_client.get(f"{COURSES_URL}/reviews/999999").status_code == 404
    assert (
        teacher_client.get(
            f"{COURSES_URL}/{course['id']}/sessions/999999/reviews"
        ).status_code
        == 404
    )
    assert (
        teacher_client.get(
            f"{COURSES_URL}/{course['id']}/sessions/{session['id']}/reviews"
        ).status_code
        == 200
    )


def test_review_rejects_too_many_tags(
    teacher_client: TestClient, db_session: Session
) -> None:
    wire = _consumable(db_session)
    course = _course(teacher_client, "标签课程")
    _recipe(teacher_client, course["id"], [(wire.id, 1)])
    session = _session(teacher_client, course["id"], 1, 5)

    tags = [f"标签{index}" for index in range(7)]
    response = _create_review(teacher_client, course["id"], session["id"], tags)
    assert response.status_code == 422, response.text
