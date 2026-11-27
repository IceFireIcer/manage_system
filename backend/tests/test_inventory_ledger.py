import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models import Consumable, InventoryEvent, Requisition, RequisitionOrder, User
from fastapi.testclient import TestClient
from sqlalchemy import select

BASE_URL = "/api/v1/requisitions"
CONSUMABLES_URL = "/api/v1/consumables"
LEDGER_URL = "/api/v1/inventory/ledger"
BACKEND_DIR = Path(__file__).resolve().parents[1]


def _consumable_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "焊丝",
        "spec": "φ1.2mm",
        "unit": "卷",
        "location": "实训室一号柜",
        "safety_stock": 5,
        "current_stock": 5,
    }
    payload.update(overrides)
    return payload


def _create_consumable(admin_client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = admin_client.post(CONSUMABLES_URL, json=_consumable_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def _login(client: TestClient, account: str, password: str) -> None:
    response = client.post("/api/v1/auth/login", json={"account": account, "password": password})
    assert response.status_code == 200, response.text


def _login_student(client: TestClient, account: str = "student01") -> None:
    _login(client, account, "Student@123")


def _create_student(admin_client: TestClient, account: str) -> None:
    response = admin_client.post(
        "/api/v1/users",
        json={
            "account": account,
            "name": "李同学",
            "password": "Student@123",
            "role": "student",
        },
    )
    assert response.status_code == 201, response.text


def _login_teacher(client: TestClient) -> None:
    _login(client, "teacher01", "Teacher@123")


def _submit(client: TestClient, items: list[dict], **extra: Any):
    payload: dict[str, Any] = {"items": items}
    payload.update(extra)
    return client.post(BASE_URL, json=payload)


def _events(db_session, consumable_id: int) -> list[InventoryEvent]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(InventoryEvent)
            .where(InventoryEvent.consumable_id == consumable_id)
            .order_by(InventoryEvent.exec_seq)
        ).all()
    )


def _requisitions(db_session) -> list[Requisition]:
    db_session.expire_all()
    return list(db_session.scalars(select(Requisition).order_by(Requisition.id)).all())


def _db_stock(db_session, consumable_id: int) -> int:
    db_session.expire_all()
    return db_session.get(Consumable, consumable_id).current_stock


def _user_id(db_session, account: str) -> int:
    return db_session.scalars(select(User).where(User.account == account)).one().id


def test_opening_event_written_and_ledger_follows_requisition(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=5)
    opening = _events(db_session, consumable["id"])
    assert len(opening) == 1
    assert opening[0].event_type == "opening"
    assert opening[0].delta == 5
    assert opening[0].stock_before == 0
    assert opening[0].stock_after == 5
    assert opening[0].exec_seq == 1
    assert opening[0].unit_snapshot == "卷"
    assert opening[0].evidence_status == "trusted"

    _login_student(admin_client)
    response = _submit(
        admin_client,
        [{"consumable_id": consumable["id"], "quantity": 3}],
        request_id="ledger-open-1",
    )
    assert response.status_code == 201, response.text

    events = _events(db_session, consumable["id"])
    assert [event.event_type for event in events] == ["opening", "requisition"]
    assert [event.stock_after for event in events] == [5, 2]
    assert events[1].delta == -3
    assert events[1].stock_before == 5
    assert events[1].exec_seq == 2
    assert events[1].source_type == "requisition_order"
    assert events[1].line_no == 1
    assert events[1].unit_snapshot == "卷"
    assert events[1].business_at == events[1].posted_at
    assert events[1].requisition_id == _requisitions(db_session)[0].id

    _login_teacher(admin_client)
    ledger = admin_client.get(LEDGER_URL, params={"consumable_id": consumable["id"]})
    assert ledger.status_code == 200, ledger.text
    data = ledger.json()
    assert data["opening_stock"] == 5
    assert data["current_stock"] == 2
    assert data["consistent"] is True
    assert [event["stock_after"] for event in data["events"]] == [5, 2]
    assert data["events"][1]["order_no"].startswith("REQ-")
    assert data["events"][1]["source_type"] == "requisition_order"
    assert data["events"][1]["source_id"] is not None
    assert data["events"][1]["event_type"] == "requisition"


def test_failed_row_rolls_back_stock_events_and_records_rejection(
    admin_client: TestClient, db_session
) -> None:
    first = _create_consumable(admin_client, current_stock=5)
    second = _create_consumable(admin_client, name="砂纸", unit="张", current_stock=2)
    _login_student(admin_client)

    response = _submit(
        admin_client,
        [
            {"consumable_id": first["id"], "quantity": 3},
            {"consumable_id": second["id"], "quantity": 5},
        ],
        request_id="ledger-rollback-1",
    )

    assert response.status_code == 400
    assert "第 2 行" in response.json()["detail"]
    assert response.headers["x-error-row"] == "2"
    assert response.headers["x-error-field"] == "items[1].quantity"
    assert response.headers["x-error-code"] == "insufficient_stock"
    assert _db_stock(db_session, first["id"]) == 5
    assert _db_stock(db_session, second["id"]) == 2
    assert _requisitions(db_session) == []
    assert len(_events(db_session, first["id"])) == 1
    assert len(_events(db_session, second["id"])) == 1

    order = db_session.scalars(
        select(RequisitionOrder).where(RequisitionOrder.request_id == "ledger-rollback-1")
    ).one()
    assert order.status == "rejected"

    status = admin_client.get(f"{BASE_URL}/requests/ledger-rollback-1")
    assert status.status_code == 200, status.text
    data = status.json()
    assert data["status"] == "rejected"
    assert data["receipt"] is None
    assert data["errors"][0]["line_no"] == 2
    assert data["errors"][0]["field"] == "items[1].quantity"
    assert data["errors"][0]["code"] == "insufficient_stock"
    assert "库存不足" in data["error_detail"]


def test_repeat_request_returns_original_receipt_without_second_event(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    _login_student(admin_client)
    payload = {
        "items": [{"consumable_id": consumable["id"], "quantity": 3}],
        "request_id": "ledger-idempotent-1",
        "remark": "课间领用",
    }

    first = admin_client.post(BASE_URL, json=payload)
    second = admin_client.post(BASE_URL, json=payload)

    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    assert second.json()["order_no"] == first.json()["order_no"]
    assert second.json()["receipt"] == first.json()["receipt"]
    assert _db_stock(db_session, consumable["id"]) == 7
    assert len(_events(db_session, consumable["id"])) == 2
    assert len(_requisitions(db_session)) == 1

    conflict = _submit(
        admin_client,
        [{"consumable_id": consumable["id"], "quantity": 4}],
        request_id="ledger-idempotent-1",
        remark="课间领用",
    )
    assert conflict.status_code == 409
    assert "不同内容" in conflict.json()["detail"]
    assert _db_stock(db_session, consumable["id"]) == 7

    other = _submit(
        admin_client,
        [{"consumable_id": consumable["id"], "quantity": 1}],
        request_id="ledger-idempotent-2",
    )
    assert other.status_code == 201, other.text
    assert _db_stock(db_session, consumable["id"]) == 6
    assert len(_requisitions(db_session)) == 2


def test_receipt_lookup_by_request_and_order_number(
    admin_client: TestClient, db_session
) -> None:
    first = _create_consumable(admin_client, current_stock=10)
    second = _create_consumable(admin_client, name="砂纸", unit="张", current_stock=8)
    third = _create_consumable(admin_client, name="手套", unit="副", current_stock=6)
    _create_student(admin_client, "student02")
    _login_student(admin_client)

    response = _submit(
        admin_client,
        [
            {"consumable_id": first["id"], "quantity": 1},
            {"consumable_id": second["id"], "quantity": 2},
            {"consumable_id": third["id"], "quantity": 3},
        ],
        request_id="ledger-receipt-1",
    )
    assert response.status_code == 201, response.text
    receipt = response.json()["receipt"]
    assert receipt["order_no"].startswith("REQ-")
    assert [line["line_no"] for line in receipt["lines"]] == [1, 2, 3]
    assert [line["stock_after"] for line in receipt["lines"]] == [9, 6, 3]
    assert all(line["event_id"] > 0 for line in receipt["lines"])
    assert all(line["requisition_id"] > 0 for line in receipt["lines"])
    assert [line["unit"] for line in receipt["lines"]] == ["卷", "张", "副"]

    by_request = admin_client.get(f"{BASE_URL}/requests/ledger-receipt-1")
    assert by_request.status_code == 200
    assert by_request.json()["status"] == "submitted"
    assert by_request.json()["receipt"] == receipt

    by_order = admin_client.get(f"{BASE_URL}/orders/{receipt['order_no']}")
    assert by_order.status_code == 200
    assert by_order.json() == receipt

    _login_student(admin_client, "student02")
    changed = admin_client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "Student@123", "new_password": "Student@4567"},
    )
    assert changed.status_code == 200, changed.text
    assert admin_client.get(f"{BASE_URL}/requests/ledger-receipt-1").status_code == 404


def test_processing_request_is_queryable(admin_client: TestClient, db_session) -> None:
    owner_id = _user_id(db_session, "student01")
    operator_id = _user_id(db_session, "admin01")
    now = datetime.now().replace(microsecond=0)
    db_session.add(
        RequisitionOrder(
            order_no="REQ-20261128-PROCESSING1",
            request_id="ledger-processing-1",
            status="processing",
            owner_id=owner_id,
            owner_name="张同学",
            operator_id=operator_id,
            operator_name="李班委",
            operator_role="admin",
            class_id=1,
            business_at=now,
            remark=None,
            items_signature="[]",
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()

    _login_student(admin_client)
    response = admin_client.get(f"{BASE_URL}/requests/ledger-processing-1")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "processing"
    assert data["receipt"] is None
    assert data["errors"] == []


def test_disabled_consumable_and_owner_errors_are_row_scoped(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client)
    admin_client.patch(
        f"{CONSUMABLES_URL}/{consumable['id']}/status", json={"status": "disabled"}
    )
    _login_student(admin_client)

    disabled = _submit(
        admin_client,
        [{"consumable_id": consumable["id"], "quantity": 1}],
        request_id="ledger-disabled-1",
    )
    assert disabled.status_code == 400
    assert "第 1 行" in disabled.json()["detail"]
    assert disabled.headers["x-error-row"] == "1"
    assert disabled.headers["x-error-code"] == "consumable_disabled"

    _login(admin_client, "admin01", "Admin@123")
    disabled_owner = _submit(
        admin_client,
        [{"consumable_id": consumable["id"], "quantity": 1}],
        request_id="ledger-owner-1",
        user_id=_user_id(db_session, "disabled01"),
    )
    assert disabled_owner.status_code == 400
    assert "领用人" in disabled_owner.json()["detail"]
    assert "停用" in disabled_owner.json()["detail"]

    status = admin_client.get(f"{BASE_URL}/requests/ledger-owner-1").json()
    assert status["status"] == "rejected"
    assert status["errors"][0]["field"] == "user_id"
    assert status["errors"][0]["line_no"] is None
    assert status["errors"][0]["code"] == "owner_disabled"


def test_migration_backfills_opening_events(tmp_path: Path) -> None:
    db_path = tmp_path / "ledger_migration.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path.as_posix()}"}

    def run(*args: str) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        assert result.returncode == 0, f"alembic {args} failed:\n{result.stdout}\n{result.stderr}"

    run("upgrade", "631bec3432b3")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO consumables ("
            "name, spec, unit, location, safety_stock, current_stock, class_id, "
            "status, created_at, updated_at"
            ") VALUES ('焊丝', 'φ1.2mm', '卷', '一号柜', 5, 7, 1, 'enabled', "
            "'2026-11-01 08:00:00', '2026-11-01 08:00:00')"
        )
        conn.commit()
    finally:
        conn.close()

    run("upgrade", "head")

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT event_type, delta, stock_before, stock_after, exec_seq, "
            "source_type, evidence_status FROM inventory_events"
        ).fetchone()
        order_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='requisition_orders'"
        ).fetchone()
    finally:
        conn.close()

    assert row == ("opening", 7, 0, 7, 1, "opening", "insufficient")
    assert order_table is not None
