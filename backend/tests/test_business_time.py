from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from app import business_time
from app.consumption import local_day_start
from app.models import OperationLog, Requisition, User
from fastapi.testclient import TestClient
from sqlalchemy import select

BASE_URL = "/api/v1/requisitions"
EXPORT_URL = "/api/v1/requisitions/export"
LOGS_URL = "/api/v1/logs"
OVERVIEW_URL = "/api/v1/inventory/overview"
CONSUMABLES_URL = "/api/v1/consumables"

SHANGHAI = timezone(timedelta(hours=8))


def _consumable_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "焊丝",
        "spec": "φ1.2mm",
        "unit": "卷",
        "location": "一号柜",
        "safety_stock": 5,
        "current_stock": 100,
    }
    payload.update(overrides)
    return payload


def _create_consumable(admin_client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = admin_client.post(CONSUMABLES_URL, json=_consumable_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def _user_id(db_session, account: str) -> int:
    return db_session.scalars(select(User).where(User.account == account)).one().id


def _insert_requisition(db_session, **kwargs: Any) -> Requisition:
    record = Requisition(
        consumable_id=kwargs["consumable_id"],
        user_id=kwargs["user_id"],
        quantity=kwargs.get("quantity", 1),
        stock_after=kwargs.get("stock_after", 99),
        type=kwargs.get("type", "normal"),
        status=kwargs.get("status", "active"),
        created_at=kwargs["created_at"],
    )
    db_session.add(record)
    db_session.commit()
    return record


def test_normalize_aware_offsets_to_shanghai_naive() -> None:
    assert business_time.normalize(
        datetime(2026, 1, 1, 0, 30, tzinfo=UTC)
    ) == datetime(2026, 1, 1, 8, 30)
    assert business_time.normalize(
        datetime(2025, 12, 31, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    ) == datetime(2026, 1, 1, 12, 30)
    naive = datetime(2026, 1, 1, 7, 0)
    assert business_time.normalize(naive) == naive


def test_business_day_boundaries_are_half_open() -> None:
    day = date(2026, 1, 31)

    assert business_time.day_start(day) == datetime(2026, 1, 31, 0, 0, 0)
    assert business_time.next_day_start(day) == datetime(2026, 2, 1, 0, 0, 0)


def test_now_and_today_use_shanghai_business_time() -> None:
    shanghai_now = datetime.now(SHANGHAI)
    fixed = business_time.now()

    assert fixed.tzinfo is None
    assert abs((fixed - shanghai_now.replace(tzinfo=None)).total_seconds()) < 2
    assert business_time.today() == shanghai_now.date()


def test_reversed_range_detection() -> None:
    assert business_time.reversed_range(date(2026, 1, 2), date(2026, 1, 1)) is True
    assert business_time.reversed_range(date(2026, 1, 1), date(2026, 1, 1)) is False
    assert business_time.reversed_range(None, date(2026, 1, 1)) is False
    assert business_time.reversed_range(date(2026, 1, 1), None) is False


def test_local_day_start_normalizes_aware_reference() -> None:
    reference = datetime(2025, 12, 31, 16, 30, tzinfo=UTC)

    assert local_day_start(0, reference) == datetime(2026, 1, 1)
    assert local_day_start(1, reference) == datetime(2025, 12, 31)


def test_requisition_date_filter_is_left_closed_right_open(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client)
    student_id = _user_id(db_session, "student01")
    day = date(2026, 11, 20)
    next_day = business_time.next_day_start(day)
    _insert_requisition(
        db_session,
        consumable_id=consumable["id"],
        user_id=student_id,
        created_at=business_time.day_start(day),
    )
    _insert_requisition(
        db_session,
        consumable_id=consumable["id"],
        user_id=student_id,
        created_at=next_day - timedelta(microseconds=1),
    )
    _insert_requisition(
        db_session,
        consumable_id=consumable["id"],
        user_id=student_id,
        created_at=next_day,
    )
    _insert_requisition(
        db_session,
        consumable_id=consumable["id"],
        user_id=student_id,
        created_at=business_time.day_start(day) - timedelta(microseconds=1),
    )

    data = admin_client.get(
        BASE_URL,
        params={
            "consumable_id": consumable["id"],
            "start_date": day.isoformat(),
            "end_date": day.isoformat(),
        },
    ).json()

    assert data["total"] == 2
    assert [item["created_at"] for item in data["items"]] == [
        (next_day - timedelta(microseconds=1)).isoformat(),
        business_time.day_start(day).isoformat(),
    ]


def test_log_date_filter_is_left_closed_right_open(
    admin_client: TestClient, db_session
) -> None:
    day = date(2026, 11, 20)
    next_day = business_time.next_day_start(day)
    db_session.add_all(
        [
            OperationLog(
                action="half_open_probe",
                created_at=business_time.day_start(day),
            ),
            OperationLog(
                action="half_open_probe",
                created_at=next_day - timedelta(microseconds=1),
            ),
            OperationLog(
                action="half_open_probe",
                created_at=next_day,
            ),
            OperationLog(
                action="half_open_probe",
                created_at=business_time.day_start(day) - timedelta(microseconds=1),
            ),
        ]
    )
    db_session.commit()

    data = admin_client.get(
        LOGS_URL,
        params={
            "action": "half_open_probe",
            "start_date": day.isoformat(),
            "end_date": day.isoformat(),
        },
    ).json()

    assert data["total"] == 2


def test_reversed_range_rejected_consistently(admin_client: TestClient) -> None:
    params = {"start_date": "2026-11-21", "end_date": "2026-11-20"}

    records_response = admin_client.get(BASE_URL, params=params)
    logs_response = admin_client.get(LOGS_URL, params=params)

    assert records_response.status_code == 422
    assert logs_response.status_code == 422
    assert records_response.json()["detail"] == business_time.REVERSE_RANGE_DETAIL
    assert logs_response.json()["detail"] == business_time.REVERSE_RANGE_DETAIL


def test_reversed_range_rejected_for_export(admin_client: TestClient) -> None:
    response = admin_client.get(
        EXPORT_URL,
        params={"start_date": "2026-11-21", "end_date": "2026-11-20"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == business_time.REVERSE_RANGE_DETAIL


def test_offset_backfill_is_normalized_to_shanghai_business_time(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client)
    student_id = _user_id(db_session, "student01")
    expected = (
        datetime.now(SHANGHAI).replace(microsecond=0) - timedelta(minutes=30)
    )
    offset_values = [
        expected.isoformat(),
        expected.astimezone(UTC).isoformat(),
        expected.astimezone(timezone(timedelta(hours=-5))).isoformat(),
    ]

    responses = [
        admin_client.post(
            BASE_URL,
            json={
                "user_id": student_id,
                "created_at": value,
                "items": [{"consumable_id": consumable["id"], "quantity": 1}],
            },
        )
        for value in offset_values
    ]

    assert all(response.status_code == 201 for response in responses)
    stored = db_session.scalars(
        select(Requisition)
        .where(Requisition.consumable_id == consumable["id"])
        .order_by(Requisition.id)
    ).all()
    assert [record.created_at for record in stored] == [expected.replace(tzinfo=None)] * 3
    listed = admin_client.get(
        BASE_URL,
        params={"consumable_id": consumable["id"], "page_size": 100},
    ).json()
    assert {item["created_at"] for item in listed["items"]} == {
        expected.replace(tzinfo=None).isoformat()
    }


def test_today_metric_counts_confirmed_business_day_and_status(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client)
    student_id = _user_id(db_session, "student01")
    today_start = business_time.day_start(business_time.today())
    next_day_start = business_time.next_day_start(business_time.today())
    _insert_requisition(
        db_session,
        consumable_id=consumable["id"],
        user_id=student_id,
        created_at=today_start,
    )
    _insert_requisition(
        db_session,
        consumable_id=consumable["id"],
        user_id=student_id,
        created_at=next_day_start - timedelta(microseconds=1),
    )
    _insert_requisition(
        db_session,
        consumable_id=consumable["id"],
        user_id=student_id,
        created_at=next_day_start,
    )
    _insert_requisition(
        db_session,
        consumable_id=consumable["id"],
        user_id=student_id,
        created_at=today_start - timedelta(microseconds=1),
    )
    _insert_requisition(
        db_session,
        consumable_id=consumable["id"],
        user_id=student_id,
        type="reversal",
        created_at=today_start + timedelta(hours=1),
    )
    _insert_requisition(
        db_session,
        consumable_id=consumable["id"],
        user_id=student_id,
        status="reversed",
        created_at=today_start + timedelta(hours=1),
    )

    data = admin_client.get(OVERVIEW_URL).json()

    assert data["summary"]["today_requisitions"] == 2
