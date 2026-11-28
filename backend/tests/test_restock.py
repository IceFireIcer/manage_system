import os
import sqlite3
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from app import business_time
from app.models import (
    Consumable,
    InventoryEvent,
    OperationLog,
    RestockCorrection,
    RestockDisposition,
    RestockLine,
    RestockOrder,
)
from fastapi.testclient import TestClient
from sqlalchemy import select

RESTOCK_URL = "/api/v1/inventory/restock"
ORDERS_URL = "/api/v1/inventory/restock/orders"
SUGGESTIONS_URL = "/api/v1/inventory/suggestions"
LEDGER_URL = "/api/v1/inventory/ledger"
CONSUMABLES_URL = "/api/v1/consumables"
BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_HEAD = ScriptDirectory.from_config(
    Config(str(BACKEND_DIR / "alembic.ini"))
).get_current_head()


def _consumable_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "焊丝",
        "spec": "φ1.2mm",
        "unit": "卷",
        "location": "实训室一号柜",
        "safety_stock": 5,
        "current_stock": 0,
    }
    payload.update(overrides)
    return payload


def _create_consumable(admin_client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = admin_client.post(CONSUMABLES_URL, json=_consumable_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def _stock(db_session, consumable_id: int) -> int:
    db_session.expire_all()
    return db_session.get(Consumable, consumable_id).current_stock


def _events(db_session, consumable_id: int) -> list[InventoryEvent]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(InventoryEvent)
            .where(InventoryEvent.consumable_id == consumable_id)
            .order_by(InventoryEvent.exec_seq)
        ).all()
    )


def _suggestion(admin_client: TestClient, consumable_id: int) -> dict[str, Any] | None:
    items = admin_client.get(SUGGESTIONS_URL).json()["items"]
    return next(
        (item for item in items if item["consumable_id"] == consumable_id), None
    )


def _submit_order(admin_client: TestClient, **payload: Any):
    return admin_client.post(ORDERS_URL, json=payload)


def _login(client: TestClient, account: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"account": account, "password": password}
    )
    assert response.status_code == 200, response.text


def test_restock_without_suggestion_is_receipt_traceable(
    admin_client: TestClient, db_session
) -> None:
    no_safety = _create_consumable(
        admin_client, name="无预警线", safety_stock=0, current_stock=0
    )
    ample = _create_consumable(
        admin_client, name="库存充足", safety_stock=5, current_stock=100
    )
    assert admin_client.get(SUGGESTIONS_URL).json()["items"] == []

    received_at = (business_time.now() - timedelta(days=1)).replace(microsecond=0)
    response = _submit_order(
        admin_client,
        items=[
            {"consumable_id": no_safety["id"], "quantity": 2},
            {"consumable_id": ample["id"], "quantity": 3},
        ],
        source="采购回库",
        remark="无建议收货",
        received_at=received_at.isoformat(),
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["order_no"].startswith("RSK-")
    assert data["status"] == "submitted"
    assert data["source"] == "采购回库"
    assert data["operator_name"] == "李班委"
    assert len(data["lines"]) == 2
    assert [line["line_no"] for line in data["lines"]] == [1, 2]
    assert [line["stock_after"] for line in data["lines"]] == [2, 103]
    assert data["business_at"] != data["posted_at"]

    by_order = admin_client.get(f"{ORDERS_URL}/{data['order_no']}")
    assert by_order.status_code == 200, by_order.text
    assert by_order.json() == data

    assert _stock(db_session, no_safety["id"]) == 2
    assert _stock(db_session, ample["id"]) == 103
    events = _events(db_session, no_safety["id"])
    assert [event.event_type for event in events] == ["restock"]
    assert events[0].delta == 2
    assert events[0].source_type == "restock_order"
    assert events[0].source_id == db_session.scalars(
        select(RestockOrder).where(RestockOrder.order_no == data["order_no"])
    ).one().id
    assert events[0].business_at < events[0].posted_at
    assert events[0].operator_name == "李班委"

    ledger = admin_client.get(LEDGER_URL, params={"consumable_id": no_safety["id"]})
    assert ledger.status_code == 200, ledger.text
    ledger_data = ledger.json()
    assert ledger_data["consistent"] is True
    assert ledger_data["current_stock"] == 2
    assert ledger_data["events"][0]["event_type"] == "restock"
    assert ledger_data["events"][0]["order_no"] == data["order_no"]


def test_partial_arrival_keeps_suggestion_and_recomputes_gap(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(
        admin_client, name="铝板", safety_stock=10, current_stock=0
    )
    before = _suggestion(admin_client, consumable["id"])
    assert before is not None
    assert before["gap"] == 10
    assert before["disposition_status"] == "pending"

    claimed = admin_client.patch(
        f"{SUGGESTIONS_URL}/{consumable['id']}/disposition",
        json={"status": "awaiting", "note": "已联系采购"},
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["status"] == "awaiting"
    assert claimed.json()["handler_name"] == "李班委"

    response = _submit_order(
        admin_client,
        items=[{"consumable_id": consumable["id"], "quantity": 2}],
        source="建议补货",
        request_id="restock-partial-1",
    )
    assert response.status_code == 201, response.text
    order_no = response.json()["order_no"]

    after = _suggestion(admin_client, consumable["id"])
    assert after is not None
    assert after["current_stock"] == 2
    assert after["gap"] == 8
    assert after["disposition_status"] == "partial"
    assert after["disposition_order_no"] == order_no
    assert after["disposition_handler_name"] == "李班委"
    assert _stock(db_session, consumable["id"]) == 2

    completed = _submit_order(
        admin_client,
        items=[{"consumable_id": consumable["id"], "quantity": 8}],
        source="建议补货",
        request_id="restock-partial-2",
    )
    assert completed.status_code == 201, completed.text
    assert _suggestion(admin_client, consumable["id"]) is None
    db_session.expire_all()
    latest = db_session.scalars(
        select(RestockDisposition)
        .where(RestockDisposition.consumable_id == consumable["id"])
        .order_by(RestockDisposition.id.desc())
    ).first()
    assert latest.status == "resolved"
    assert latest.order_no == completed.json()["order_no"]


def test_repeat_restock_request_creates_one_order(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(
        admin_client, name="砂纸", safety_stock=0, current_stock=0
    )
    payload = {
        "items": [{"consumable_id": consumable["id"], "quantity": 12}],
        "source": "采购回库",
        "remark": "重复提交",
        "request_id": "restock-idempotent-1",
    }

    first = _submit_order(admin_client, **payload)
    second = _submit_order(admin_client, **payload)

    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    assert second.json()["order_no"] == first.json()["order_no"]
    assert second.json()["lines"] == first.json()["lines"]
    assert _stock(db_session, consumable["id"]) == 12
    db_session.expire_all()
    assert len(db_session.scalars(select(RestockOrder)).all()) == 1
    assert len(db_session.scalars(select(RestockLine)).all()) == 1
    assert len(_events(db_session, consumable["id"])) == 1

    status = admin_client.get(f"{ORDERS_URL.rsplit('/orders', 1)[0]}/requests/restock-idempotent-1")
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "submitted"
    assert status.json()["receipt"]["order_no"] == first.json()["order_no"]

    conflict = _submit_order(
        admin_client,
        items=[{"consumable_id": consumable["id"], "quantity": 13}],
        source="采购回库",
        remark="重复提交",
        request_id="restock-idempotent-1",
    )
    assert conflict.status_code == 409
    assert "不同内容" in conflict.json()["detail"]
    assert _stock(db_session, consumable["id"]) == 12


def test_legacy_restock_endpoint_writes_same_contract(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(
        admin_client, name="手套", unit="副", safety_stock=5, current_stock=1
    )

    response = admin_client.post(
        RESTOCK_URL,
        json={
            "consumable_id": consumable["id"],
            "quantity": 4,
            "remark": "旧入口",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["order_no"].startswith("RSK-")
    assert data["stock_before"] == 1
    assert data["stock_after"] == 5
    assert data["quantity"] == 4

    events = _events(db_session, consumable["id"])
    assert [event.event_type for event in events] == ["opening", "restock"]
    assert events[-1].source_type == "restock_order"



def test_correction_uses_reverse_event_and_respects_original_quantity(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(
        admin_client, name="焊条", safety_stock=10, current_stock=0
    )
    order = _submit_order(
        admin_client,
        items=[{"consumable_id": consumable["id"], "quantity": 5}],
        source="采购回库",
        request_id="restock-correct-source",
    )
    assert order.status_code == 201, order.text
    order_no = order.json()["order_no"]

    correction = admin_client.post(
        f"{ORDERS_URL}/{order_no}/corrections",
        json={
            "line_no": 1,
            "quantity": 2,
            "reason": "实收数量录错",
            "request_id": "restock-correction-1",
        },
    )
    assert correction.status_code == 200, correction.text
    data = correction.json()
    assert data["quantity"] == 2
    assert data["stock_before"] == 5
    assert data["stock_after"] == 3
    assert data["event_id"] is not None
    assert _stock(db_session, consumable["id"]) == 3

    events = _events(db_session, consumable["id"])
    assert [event.event_type for event in events] == ["restock", "restock_correction"]
    assert events[-1].delta == -2
    assert events[-1].source_type == "restock_order"
    assert events[-1].line_no == 1

    db_session.expire_all()
    db_order = db_session.scalars(
        select(RestockOrder).where(RestockOrder.order_no == order_no)
    ).one()
    assert events[-1].source_id == db_order.id
    line = db_session.scalars(
        select(RestockLine).where(RestockLine.order_id == db_order.id)
    ).one()
    assert line.corrected_quantity == 2
    log = db_session.scalars(
        select(OperationLog).where(OperationLog.action == "restock_correction")
    ).one()
    assert log.target_id == consumable["id"]
    assert db_session.scalars(select(RestockCorrection)).one().quantity == 2

    receipt = admin_client.get(f"{ORDERS_URL}/{order_no}").json()
    assert receipt["lines"][0]["corrected_quantity"] == 2
    assert len(receipt["corrections"]) == 1
    assert receipt["corrections"][0]["correction_id"] == data["correction_id"]

    too_much = admin_client.post(
        f"{ORDERS_URL}/{order_no}/corrections",
        json={
            "line_no": 1,
            "quantity": 4,
            "reason": "继续扣回",
            "request_id": "restock-correction-2",
        },
    )
    assert too_much.status_code == 400
    assert "最多可扣回 3" in too_much.json()["detail"]
    assert _stock(db_session, consumable["id"]) == 3

    repeat = admin_client.post(
        f"{ORDERS_URL}/{order_no}/corrections",
        json={
            "line_no": 1,
            "quantity": 2,
            "reason": "实收数量录错",
            "request_id": "restock-correction-1",
        },
    )
    assert repeat.status_code == 200, repeat.text
    assert repeat.json()["correction_id"] == data["correction_id"]
    assert _stock(db_session, consumable["id"]) == 3


def test_teacher_can_write_restock_but_student_cannot(
    admin_client: TestClient,
) -> None:
    consumable = _create_consumable(
        admin_client, name="胶带", safety_stock=0, current_stock=0
    )
    order = _submit_order(
        admin_client,
        items=[{"consumable_id": consumable["id"], "quantity": 1}],
        request_id="restock-auth-source",
    )
    assert order.status_code == 201, order.text
    order_no = order.json()["order_no"]

    payload = {"items": [{"consumable_id": consumable["id"], "quantity": 1}]}
    correction_payload = {
        "line_no": 1,
        "quantity": 1,
        "reason": "测试越权",
        "request_id": "restock-auth-correction",
    }

    _login(admin_client, "teacher01", "Teacher@123")
    teacher_order = admin_client.post(ORDERS_URL, json=payload)
    assert teacher_order.status_code == 201, teacher_order.text
    assert (
        admin_client.post(
            RESTOCK_URL, json={"consumable_id": consumable["id"], "quantity": 1}
        ).status_code
        == 200
    )
    assert (
        admin_client.post(
            f"{ORDERS_URL}/{order_no}/corrections", json=correction_payload
        ).status_code
        == 200
    )
    assert admin_client.get(f"{ORDERS_URL}/{order_no}").status_code == 200

    _login(admin_client, "student01", "Student@123")
    assert admin_client.post(ORDERS_URL, json=payload).status_code == 403
    assert admin_client.post(
        RESTOCK_URL, json={"consumable_id": consumable["id"], "quantity": 1}
    ).status_code == 403
    assert admin_client.post(
        f"{ORDERS_URL}/{order_no}/corrections", json=correction_payload
    ).status_code == 403
    assert admin_client.get(f"{ORDERS_URL}/{order_no}").status_code == 403


def test_migration_creates_restock_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "restock_migration.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path.as_posix()}"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    finally:
        conn.close()

    assert version == (ALEMBIC_HEAD,)
    assert {
        "restock_orders",
        "restock_lines",
        "restock_corrections",
        "restock_dispositions",
        "stock_alerts",
    } <= tables
