from datetime import datetime

from app.models import User
from fastapi.testclient import TestClient
from sqlalchemy import select

COUNTS_URL = "/api/v1/inventory/counts"
CONSUMABLES_URL = "/api/v1/consumables"
REQUISITIONS_URL = "/api/v1/requisitions"
LEDGER_URL = "/api/v1/inventory/ledger"
AUDIT_URL = "/api/v1/inventory/audit"


def _login_admin(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"account": "admin01", "password": "Admin@123"},
    )
    assert response.status_code == 200, response.text


def _login_teacher(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"account": "teacher01", "password": "Teacher@123"},
    )
    assert response.status_code == 200, response.text


def _login_student(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"account": "student01", "password": "Student@123"},
    )
    assert response.status_code == 200, response.text


def _user_id(db_session, account: str) -> int:
    return db_session.scalars(select(User).where(User.account == account)).one().id


def _create_consumable(client: TestClient, **overrides) -> dict:
    payload = {
        "name": "焊丝",
        "spec": "φ1.2mm",
        "unit": "卷",
        "location": "一号柜",
        "safety_stock": 5,
        "current_stock": 10,
    }
    payload.update(overrides)
    response = client.post(CONSUMABLES_URL, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _create_count(
    client: TestClient, lines: list[dict], **overrides
) -> tuple[int, dict]:
    payload = {"lines": lines}
    payload.update(overrides)
    response = client.post(COUNTS_URL, json=payload)
    return response.status_code, response.json()


def _issue(client: TestClient, db_session, consumable_id: int, quantity: int) -> dict:
    payload = {
        "user_id": _user_id(db_session, "student01"),
        "items": [{"consumable_id": consumable_id, "quantity": quantity}],
    }
    response = client.post(REQUISITIONS_URL, json=payload)
    assert response.status_code == 201, response.text
    data = response.json()
    return data.get("receipt", data)


def _post(client: TestClient, order_no: str):
    return client.post(f"{COUNTS_URL}/{order_no}/post")


def _get(client: TestClient, order_no: str) -> dict:
    response = client.get(f"{COUNTS_URL}/{order_no}")
    assert response.status_code == 200, response.text
    return response.json()


def _adjustment_events(db_session, count_id: int) -> list:
    db_session.expire_all()
    from app.models import InventoryEvent

    return list(
        db_session.scalars(
            select(InventoryEvent).where(
                InventoryEvent.source_type == "inventory_count",
                InventoryEvent.source_id == count_id,
            )
        ).all()
    )


def test_create_count_freezes_baseline_and_records_people(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    reviewer_id = _user_id(db_session, "teacher01")

    status_code, body = _create_count(
        admin_client,
        [{"consumable_id": consumable["id"], "counted_qty": 12, "reason": "历史迁移"}],
        reviewer_id=reviewer_id,
    )

    assert status_code == 201, body
    assert body["order_no"].startswith("CNT-")
    assert body["status"] == "open"
    assert body["counter_name"] == "李班委"
    assert body["reviewer_name"] == "王老师"
    line = body["lines"][0]
    assert line["baseline_stock"] == 10
    assert line["baseline_seq"] == 1
    assert line["counted_qty"] == 12
    assert line["diff"] == 2
    assert body["recheck_required"] is False


def test_post_generates_surplus_and_shortage_adjustments(
    admin_client: TestClient, db_session
) -> None:
    surplus = _create_consumable(admin_client, name="盘盈耗材", current_stock=10)
    shortage = _create_consumable(admin_client, name="盘亏耗材", current_stock=10)

    _, body = _create_count(
        admin_client,
        [
            {"consumable_id": surplus["id"], "counted_qty": 12, "reason": "历史迁移"},
            {"consumable_id": shortage["id"], "counted_qty": 7, "reason": "丢失"},
        ],
    )
    order_no = body["order_no"]

    response = _post(admin_client, order_no)
    assert response.status_code == 201, response.text
    posted = response.json()
    assert posted["status"] == "posted"
    assert posted["lines"][0]["diff"] == 2
    assert posted["lines"][1]["diff"] == -3

    db_session.expire_all()
    from app.models import Consumable, OperationLog

    assert db_session.get(Consumable, surplus["id"]).current_stock == 12
    assert db_session.get(Consumable, shortage["id"]).current_stock == 7
    events = _adjustment_events(db_session, posted["id"])
    assert sorted(event.delta for event in events) == [-3, 2]
    assert all(event.event_type == "inventory_adjustment" for event in events)
    assert all(
        event.business_at == datetime.fromisoformat(posted["count_at"])
        for event in events
    )
    assert {event.line_no for event in events} == {1, 2}
    logs = db_session.scalars(
        select(OperationLog).where(OperationLog.action == "inventory_adjustment")
    ).all()
    assert len(logs) == 2

    ledger = admin_client.get(
        LEDGER_URL, params={"consumable_id": surplus["id"]}
    )
    assert ledger.status_code == 200, ledger.text
    ledger_body = ledger.json()
    assert ledger_body["events"][-1]["order_no"] == order_no

    audit = admin_client.get(AUDIT_URL, params={"consumable_id": surplus["id"]})
    assert audit.status_code == 200, audit.text
    adjustment = [
        event
        for event in audit.json()["posting"]["events"]
        if event["event_type"] == "inventory_adjustment"
    ][0]
    assert adjustment["order_no"] == order_no
    assert adjustment["can_open_source"] is False


def test_concurrent_requisition_recomputes_baseline(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    _, body = _create_count(
        admin_client,
        [{"consumable_id": consumable["id"], "counted_qty": 9, "reason": "丢失"}],
    )
    order_no = body["order_no"]

    _login_student(admin_client)
    _issue(admin_client, db_session, consumable["id"], 3)
    _login_admin(admin_client)

    before = _get(admin_client, order_no)
    line = before["lines"][0]
    assert line["baseline_stock"] == 10
    assert line["period_delta"] == -3
    assert line["expected_stock"] == 7
    assert line["current_stock"] == 7
    assert line["recheck_required"] is False

    response = _post(admin_client, order_no)
    assert response.status_code == 201, response.text
    posted = response.json()
    assert posted["lines"][0]["period_delta"] == -3
    assert posted["lines"][0]["diff"] == -1
    assert posted["warning"] is None

    db_session.expire_all()
    from app.models import Consumable

    assert db_session.get(Consumable, consumable["id"]).current_stock == 6


def test_recheck_required_when_current_stock_mismatches_snapshot(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    _, body = _create_count(
        admin_client, [{"consumable_id": consumable["id"], "counted_qty": 10}]
    )
    order_no = body["order_no"]

    db_session.expire_all()
    from app.models import Consumable

    stored = db_session.get(Consumable, consumable["id"])
    stored.current_stock = 8
    db_session.commit()

    before = _get(admin_client, order_no)
    assert before["recheck_required"] is True
    assert before["lines"][0]["expected_stock"] == 10
    assert before["lines"][0]["current_stock"] == 8

    response = _post(admin_client, order_no)
    assert response.status_code == 201, response.text
    posted = response.json()
    assert posted["warning"]
    assert posted["lines"][0]["diff"] == 0
    assert posted["lines"][0]["event_id"] is None
    assert _adjustment_events(db_session, posted["id"]) == []


def test_stale_count_object_cannot_post_twice(
    admin_client: TestClient, db_session
) -> None:
    from app import inventory_count as inventory_count_service
    from app.models import Consumable, InventoryCount

    consumable = _create_consumable(admin_client, current_stock=10)
    _, body = _create_count(
        admin_client, [{"consumable_id": consumable["id"], "counted_qty": 12}]
    )
    order_no = body["order_no"]
    count_id = body["id"]

    stale = db_session.scalars(
        select(InventoryCount).where(InventoryCount.id == count_id)
    ).one()
    assert stale.status == "open"
    db_session.commit()

    first = _post(admin_client, order_no)
    assert first.status_code == 201, first.text

    operator = db_session.get(User, _user_id(db_session, "admin01"))
    refreshed, created = inventory_count_service.post_count(
        db_session, count=stale, operator=operator
    )

    assert created is False
    assert refreshed.status == "posted"
    assert refreshed.posted_at == datetime.fromisoformat(
        first.json()["posted_at"]
    )
    assert len(_adjustment_events(db_session, count_id)) == 1

    db_session.expire_all()
    assert db_session.get(Consumable, consumable["id"]).current_stock == 12


def test_repeat_post_is_idempotent(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    _, body = _create_count(
        admin_client, [{"consumable_id": consumable["id"], "counted_qty": 12}]
    )
    order_no = body["order_no"]

    first = _post(admin_client, order_no)
    assert first.status_code == 201, first.text
    second = _post(admin_client, order_no)
    assert second.status_code == 200, second.text
    assert second.json()["order_no"] == order_no
    assert second.json()["posted_at"] == first.json()["posted_at"]

    events = _adjustment_events(db_session, first.json()["id"])
    assert len(events) == 1
    assert events[0].delta == 2

    db_session.expire_all()
    from app.models import Consumable

    assert db_session.get(Consumable, consumable["id"]).current_stock == 12


def test_teacher_reads_but_cannot_create_or_post(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    _, body = _create_count(
        admin_client, [{"consumable_id": consumable["id"], "counted_qty": 11}]
    )
    order_no = body["order_no"]

    _login_teacher(admin_client)
    response = admin_client.get(f"{COUNTS_URL}/{order_no}")
    assert response.status_code == 200, response.text
    assert response.json()["reviewer_name"] == "李班委"

    create = admin_client.post(
        COUNTS_URL,
        json={"lines": [{"consumable_id": consumable["id"], "counted_qty": 9}]},
    )
    assert create.status_code == 403, create.text
    assert _post(admin_client, order_no).status_code == 403

    _login_student(admin_client)
    assert admin_client.get(f"{COUNTS_URL}/{order_no}").status_code == 403
    assert _post(admin_client, order_no).status_code == 403


def test_missing_counted_quantity_blocks_posting(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    _, body = _create_count(admin_client, [{"consumable_id": consumable["id"]}])
    order_no = body["order_no"]

    response = _post(admin_client, order_no)
    assert response.status_code == 400, response.text
    assert "尚未录入实盘量" in response.json()["detail"]

    update = admin_client.patch(
        f"{COUNTS_URL}/{order_no}/lines",
        json={
            "lines": [
                {
                    "consumable_id": consumable["id"],
                    "counted_qty": 8,
                    "reason": "破损",
                }
            ]
        },
    )
    assert update.status_code == 200, update.text
    assert update.json()["lines"][0]["diff"] == -2

    posted = _post(admin_client, order_no)
    assert posted.status_code == 201, posted.text
    assert posted.json()["lines"][0]["diff"] == -2


def test_duplicate_and_disabled_consumables_are_rejected(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    status_code, body = _create_count(
        admin_client,
        [
            {"consumable_id": consumable["id"], "counted_qty": 10},
            {"consumable_id": consumable["id"], "counted_qty": 9},
        ],
    )
    assert status_code == 400, body

    disabled = _create_consumable(admin_client, name="停用耗材", current_stock=3)
    db_session.expire_all()
    from app.models import Consumable

    stored = db_session.get(Consumable, disabled["id"])
    stored.status = "disabled"
    db_session.commit()

    status_code, body = _create_count(
        admin_client, [{"consumable_id": disabled["id"], "counted_qty": 3}]
    )
    assert status_code == 400, body
