import csv
import io
import json
import zipfile
from datetime import date, datetime

from app import business_time
from app.models import (
    Consumable,
    Course,
    CourseSession,
    HandoverChecklist,
    HandoverItem,
    IncidentReport,
    InventoryCount,
    InventoryCountLine,
    InventoryEvent,
    OperationLog,
    TeachingReview,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select

EXPORT_URL = "/api/v1/ops/semester-close"
PREVIEW_URL = "/api/v1/ops/semester-close/preview"
CONSUMABLES_URL = "/api/v1/consumables"
REQUISITIONS_URL = "/api/v1/requisitions"

START = "2026-03-01"
END = "2026-03-31"
START_DATE = date(2026, 3, 1)
END_DATE = date(2026, 3, 31)
CSV_FILES = (
    "key-events.csv",
    "incidents.csv",
    "handover.csv",
    "count-differences.csv",
    "review-summary.csv",
)


def _user_id(db_session, account: str) -> int:
    return db_session.scalars(select(User).where(User.account == account)).one().id


def _zip(response) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _rows(text: bytes) -> list[list[str]]:
    assert text.startswith(b"\xef\xbb\xbf")
    reader = csv.reader(io.StringIO(text.decode("utf-8-sig")))
    return list(reader)


def _summary(entries: dict[str, bytes]) -> dict:
    return json.loads(entries["summary.json"].decode("utf-8"))


def _seed_consumable(db_session, name: str, class_id: int = 1) -> Consumable:
    consumable = Consumable(
        name=name,
        spec="φ1.2mm",
        unit="卷",
        location="一号柜",
        safety_stock=5,
        current_stock=10,
        class_id=class_id,
    )
    db_session.add(consumable)
    db_session.flush()
    return consumable


def _seed_event(
    db_session,
    consumable: Consumable,
    *,
    delta: int,
    business_at: datetime,
    exec_seq: int,
    class_id: int = 1,
    event_type: str = "requisition",
) -> InventoryEvent:
    event = InventoryEvent(
        consumable_id=consumable.id,
        event_type=event_type,
        delta=delta,
        stock_before=10,
        stock_after=10 + delta,
        exec_seq=exec_seq,
        business_at=business_at,
        posted_at=business_at,
        source_type="requisition",
        operator_id=None,
        operator_name="王老师",
        operator_role="teacher",
        name_snapshot=consumable.name,
        spec_snapshot=consumable.spec,
        unit_snapshot=consumable.unit,
        class_id=class_id,
    )
    db_session.add(event)
    return event


def _seed_incident(
    db_session,
    consumable: Consumable,
    *,
    created_at: datetime,
    class_id: int = 1,
) -> IncidentReport:
    incident = IncidentReport(
        class_id=class_id,
        consumable_id=consumable.id,
        reporter_id=_user_id(db_session, "student01"),
        reporter_name="张同学",
        reporter_role="student",
        location="一号柜",
        description="张同学反映焊丝受潮",
        status="confirmed",
        handler_name="李班委",
        handled_at=created_at,

        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(incident)
    return incident


def _seed_handover(db_session, *, created_at: datetime) -> HandoverChecklist:
    checklist = HandoverChecklist(
        class_id=1,
        version=1,
        status="pending",
        outgoing_id=_user_id(db_session, "admin01"),
        outgoing_name="李班委",
        outgoing_account="admin01",
        snapshot_at=created_at,
        created_at=created_at,
    )
    db_session.add(checklist)
    db_session.flush()
    db_session.add(
        HandoverItem(
            checklist_id=checklist.id,
            seq=1,
            kind="stock",
            ref_type="stock_alert",
            label="库存异常：焊丝",
            snapshot_payload='{"name": "焊丝", "reporter": "张同学"}',
            confirmed=False,
            created_at=created_at,
        )
    )
    return checklist


def _seed_count(
    db_session, consumable: Consumable, *, count_at: datetime
) -> InventoryCount:
    count = InventoryCount(
        order_no="CNT-20260310-001",
        request_id="req-count-1",
        status="posted",
        count_at=count_at,
        counter_id=_user_id(db_session, "admin01"),
        counter_name="李班委",
        counter_role="admin",
        reviewer_id=_user_id(db_session, "teacher01"),
        reviewer_name="王老师",
        reviewer_role="teacher",
        operator_id=_user_id(db_session, "admin01"),
        operator_name="李班委",
        operator_role="admin",
        class_id=1,
        posted_at=count_at,
        posted_by_id=_user_id(db_session, "admin01"),
        posted_by_name="李班委",
    )
    db_session.add(count)
    db_session.flush()
    db_session.add_all(
        [
            InventoryCountLine(
                count_id=count.id,
                line_no=1,
                consumable_id=consumable.id,
                consumable_name=consumable.name,
                unit=consumable.unit,
                baseline_stock=10,
                baseline_seq=1,
                counted_qty=12,
                period_delta=0,
                diff=2,
                reason="盘盈",
                event_id=1,
                created_at=count_at,
                updated_at=count_at,
            ),
            InventoryCountLine(
                count_id=count.id,
                line_no=2,
                consumable_id=consumable.id,
                consumable_name=consumable.name,
                unit=consumable.unit,
                baseline_stock=10,
                baseline_seq=2,
                counted_qty=10,
                period_delta=0,
                diff=0,
                created_at=count_at,
                updated_at=count_at,
            ),
        ]
    )
    return count


def _seed_review(db_session, *, session_date: date) -> TeachingReview:
    teacher_id = _user_id(db_session, "teacher01")
    course = Course(
        class_id=1,
        name="焊接实训",
        class_label="一班",
        semester="2026春",
        created_by_id=teacher_id,
        created_by_name="王老师",
    )
    db_session.add(course)
    db_session.flush()
    session = CourseSession(
        course_id=course.id,
        class_id=1,
        seq=1,
        session_date=session_date,
        headcount=30,
        class_label="一班",
        status="completed",
        created_by_id=teacher_id,
        created_by_name="王老师",
    )
    db_session.add(session)
    db_session.flush()
    review = TeachingReview(
        class_id=1,
        session_id=session.id,
        snapshot_id=1,
        actual_net_quantity=18,
        standard_quantity=20,
        deviation_ratio=0.9,
        reason_tags='["浪费"]',
        teacher_note="张同学未到课，材料发放偏多",
        created_by_id=teacher_id,
        created_by_name="王老师",
        version=1,
    )
    db_session.add(review)
    return review


def _seed_all(db_session) -> dict[str, object]:
    consumable = _seed_consumable(db_session, "焊丝")
    other = _seed_consumable(db_session, "砂纸")
    _seed_event(
        db_session,
        consumable,
        delta=-2,
        business_at=datetime(2026, 3, 2, 9, 0),
        exec_seq=1,
    )
    _seed_event(
        db_session,
        other,
        delta=5,
        business_at=datetime(2026, 3, 3, 9, 0),
        exec_seq=1,
        event_type="restock",
    )
    _seed_event(
        db_session,
        consumable,
        delta=-1,
        business_at=datetime(2026, 2, 28, 9, 0),
        exec_seq=2,
    )
    _seed_event(
        db_session,
        other,
        delta=-3,
        business_at=datetime(2026, 3, 4, 9, 0),
        exec_seq=2,
        class_id=2,
    )
    _seed_incident(db_session, consumable, created_at=datetime(2026, 3, 5, 10, 0))
    _seed_incident(
        db_session,
        other,
        created_at=datetime(2026, 3, 6, 10, 0),
        class_id=2,
    )
    _seed_handover(db_session, created_at=datetime(2026, 3, 7, 8, 0))
    _seed_count(db_session, consumable, count_at=datetime(2026, 3, 10, 8, 0))
    _seed_review(db_session, session_date=date(2026, 3, 12))
    db_session.commit()
    return {"consumable": consumable, "other": other}


def _login(client: TestClient, account: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"account": account, "password": password}
    )
    assert response.status_code == 200, response.text


def test_semester_close_requires_staff(client: TestClient) -> None:
    assert client.get(EXPORT_URL, params={"start": START, "end": END}).status_code == 401

    _login(client, "student01", "Student@123")
    assert client.get(EXPORT_URL, params={"start": START, "end": END}).status_code == 403
    assert client.get(PREVIEW_URL, params={"start": START, "end": END}).status_code == 403


def test_semester_close_rejects_reversed_range(teacher_client: TestClient) -> None:
    response = teacher_client.get(EXPORT_URL, params={"start": END, "end": START})

    assert response.status_code == 422


def test_semester_close_archive_contents_match_source(
    teacher_client: TestClient, db_session
) -> None:
    _seed_all(db_session)

    response = teacher_client.get(EXPORT_URL, params={"start": START, "end": END})

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert "20260301" in response.headers["content-disposition"]
    assert "20260331" in response.headers["content-disposition"]

    entries = _zip(response)
    for name in CSV_FILES:
        assert name in entries
    assert "summary.json" in entries
    summary = _summary(entries)

    assert summary["range"] == {"start": START, "end": END, "days": 31}
    assert summary["totals"]["key_events"] == 2
    assert summary["totals"]["key_event_delta_sum"] == 3
    assert summary["totals"]["incidents"] == 1
    assert summary["totals"]["open_incidents"] == 1
    assert summary["totals"]["handover_checklists"] == 1
    assert summary["totals"]["handover_items"] == 1
    assert summary["totals"]["count_differences"] == 1
    assert summary["totals"]["count_difference_sum"] == 2
    assert summary["totals"]["reviews"] == 1

    key_events = _rows(entries["key-events.csv"])
    assert key_events[0][1] == "事件类型"
    assert len(key_events) - 1 == summary["files"]["key-events.csv"]["rows"] == 2
    assert [row[1] for row in key_events[1:]] == ["requisition", "restock"]
    assert [row[5] for row in key_events[1:]] == ["-2", "5"]

    incidents = _rows(entries["incidents.csv"])
    assert len(incidents) - 1 == summary["files"]["incidents.csv"]["rows"] == 1
    assert incidents[1][4] == "confirmed"
    assert incidents[1][5] == "李班委"

    handover = _rows(entries["handover.csv"])
    assert len(handover) - 1 == summary["files"]["handover.csv"]["rows"] == 1
    assert handover[1][1] == "pending"
    assert handover[1][9] == "库存异常：焊丝"

    differences = _rows(entries["count-differences.csv"])
    assert len(differences) - 1 == summary["files"]["count-differences.csv"]["rows"] == 1
    assert differences[1][0] == "CNT-20260310-001"
    assert differences[1][8] == "2"

    reviews = _rows(entries["review-summary.csv"])
    assert len(reviews) - 1 == summary["files"]["review-summary.csv"]["rows"] == 1
    assert reviews[1][0] == "焊接实训"
    assert reviews[1][6] == "0.9"

    assert summary["files"]["summary.json"]["rows"] == 1


def test_semester_close_filters_range_and_class(
    teacher_client: TestClient, db_session
) -> None:
    _seed_all(db_session)

    entries = _zip(teacher_client.get(EXPORT_URL, params={"start": START, "end": END}))
    key_events = _rows(entries["key-events.csv"])
    incidents = _rows(entries["incidents.csv"])

    assert len(key_events) - 1 == 2
    assert all(row[0].startswith("2026-03") for row in key_events[1:])
    assert len(incidents) - 1 == 1
    assert incidents[1][1].startswith("2026-03-05")


def test_semester_close_excludes_student_detail(
    teacher_client: TestClient, db_session
) -> None:
    _seed_all(db_session)

    entries = _zip(teacher_client.get(EXPORT_URL, params={"start": START, "end": END}))
    blob = "".join(entries[name].decode("utf-8-sig") for name in entries)

    assert "张同学" not in blob
    assert "student01" not in blob
    assert "焊丝受潮" not in blob
    assert "未到课" not in blob
    assert "reporter" not in blob


def test_semester_close_repeat_is_stable(
    teacher_client: TestClient, db_session
) -> None:
    _seed_all(db_session)

    first = teacher_client.get(EXPORT_URL, params={"start": START, "end": END})
    second = teacher_client.get(EXPORT_URL, params={"start": START, "end": END})

    assert first.status_code == second.status_code == 200
    first_entries = _zip(first)
    second_entries = _zip(second)
    assert set(first_entries) == set(second_entries)
    for name in first_entries:
        if name == "summary.json":
            continue
        assert first_entries[name] == second_entries[name]

    first_summary = _summary(first_entries)
    second_summary = _summary(second_entries)
    first_summary.pop("generated_at")
    second_summary.pop("generated_at")
    assert first_summary == second_summary


def test_semester_close_writes_audit_log(
    teacher_client: TestClient, db_session
) -> None:
    _seed_all(db_session)

    teacher_client.get(EXPORT_URL, params={"start": START, "end": END})
    teacher_client.get(EXPORT_URL, params={"start": START, "end": END})

    db_session.expire_all()
    logs = db_session.scalars(
        select(OperationLog).where(OperationLog.action == "semester_close_export")
    ).all()

    assert len(logs) == 2
    for log in logs:
        assert log.operator_name == "王老师"
        assert log.role == "teacher"
        assert log.target_type == "semester_close"
        assert log.target_label == "2026-03-01 ~ 2026-03-31"
        payload = json.loads(log.after_value)
        assert payload["start"] == START
        assert payload["end"] == END
        assert payload["files"]["key-events.csv"]["rows"] == 2



def test_semester_close_is_read_only(
    teacher_client: TestClient, db_session
) -> None:
    _seed_all(db_session)
    consumable = db_session.scalars(
        select(Consumable).where(Consumable.name == "焊丝")
    ).one()
    stock_before = consumable.current_stock
    event_count = db_session.scalar(
        select(func.count()).select_from(InventoryEvent)
    )

    response = teacher_client.get(EXPORT_URL, params={"start": START, "end": END})
    assert response.status_code == 200

    db_session.expire_all()
    assert db_session.get(Consumable, consumable.id).current_stock == stock_before
    assert (
        db_session.scalar(select(func.count()).select_from(InventoryEvent))
        == event_count
    )


def test_semester_close_export_behaviour_through_api(
    admin_client: TestClient, db_session
) -> None:
    admin_client.post(
        CONSUMABLES_URL,
        json={
            "name": "焊丝",
            "spec": "φ1.2mm",
            "unit": "卷",
            "location": "一号柜",
            "safety_stock": 5,
            "current_stock": 10,
        },
    )
    db_session.expire_all()
    consumable = db_session.scalars(
        select(Consumable).where(Consumable.name == "焊丝")
    ).one()
    user_id = _user_id(db_session, "student01")
    issued = admin_client.post(
        REQUISITIONS_URL,
        json={
            "user_id": user_id,
            "items": [{"consumable_id": consumable.id, "quantity": 2}],
        },
    )
    assert issued.status_code == 201, issued.text

    today = business_time.today().isoformat()
    response = admin_client.get(
        EXPORT_URL, params={"start": today, "end": today}
    )
    assert response.status_code == 200, response.text

    entries = _zip(response)
    key_events = _rows(entries["key-events.csv"])
    rows = {row[1]: row for row in key_events[1:]}
    assert set(rows) == {"opening", "requisition"}
    assert rows["requisition"][5] == "-2"
    assert _summary(entries)["totals"]["key_event_delta_sum"] == 8


def test_semester_close_preview_matches_archive_without_audit(
    teacher_client: TestClient, db_session
) -> None:
    _seed_all(db_session)

    preview = teacher_client.get(PREVIEW_URL, params={"start": START, "end": END})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    entries = _zip(teacher_client.get(EXPORT_URL, params={"start": START, "end": END}))
    summary = _summary(entries)

    assert body["range"] == summary["range"]
    assert body["files"] == summary["files"]
    assert body["totals"] == summary["totals"]
    assert body["files"]["handover.csv"]["rows"] == 1

    db_session.expire_all()
    logs = db_session.scalars(
        select(OperationLog).where(OperationLog.action == "semester_close_export")
    ).all()
    assert len(logs) == 1
