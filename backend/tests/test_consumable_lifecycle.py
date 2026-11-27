import json
from datetime import datetime

from app.models import (
    OperationLog,
    RequisitionOrder,
    RestockDisposition,
    RestockOrder,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import select

LIST_URL = "/api/v1/consumables"
REQ_URL = "/api/v1/requisitions"
LEDGER_URL = "/api/v1/inventory/ledger"
LOGIN_URL = "/api/v1/auth/login"


def _payload(**overrides):
    data = {
        "name": "焊丝",
        "spec": "φ1.2mm",
        "unit": "卷",
        "location": "实训室一号柜",
        "safety_stock": 5,
        "current_stock": 10,
    }
    data.update(overrides)
    return data


def _create(client: TestClient, **overrides) -> dict:
    response = client.post(LIST_URL, json=_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def _login(client: TestClient, account: str, password: str) -> None:
    response = client.post(LOGIN_URL, json={"account": account, "password": password})
    assert response.status_code == 200, response.text


def _user_id(db_session, account: str) -> int:
    return db_session.scalars(select(User).where(User.account == account)).one().id


def _requisition(client: TestClient, consumable_id: int, quantity: int, request_id: str):
    return client.post(
        REQ_URL,
        json={
            "items": [{"consumable_id": consumable_id, "quantity": quantity}],
            "request_id": request_id,
        },
    )


def _receipt_line(client: TestClient, request_id: str) -> dict:
    response = client.get(f"{REQ_URL}/requests/{request_id}")
    assert response.status_code == 200, response.text
    return response.json()["receipt"]["lines"][0]


def test_deactivation_preview_shows_balance_receipts_and_pending(
    client: TestClient, db_session
) -> None:
    _login(client, "admin01", "Admin@123")
    item = _create(client, current_stock=10)
    _login(client, "student01", "Student@123")
    created = _requisition(client, item["id"], 2, "req-preview-1")
    assert created.status_code == 201, created.text
    admin_id = _user_id(db_session, "admin01")
    at = datetime(2026, 11, 28, 9, 0, 0)
    db_session.add(
        RestockOrder(
            order_no="RSK-20261128-PREVIEW1",
            request_id="restock-preview-1",
            status="processing",
            operator_id=admin_id,
            operator_name="李班委",
            operator_role="admin",
            class_id=1,
            business_at=at,
            items_signature=json.dumps([[item["id"], 5]]),
        )
    )
    db_session.add(
        RequisitionOrder(
            order_no="REQ-20261128-PREVIEW1",
            request_id="req-processing-1",
            status="processing",
            owner_id=admin_id,
            owner_name="李班委",
            operator_id=admin_id,
            operator_name="李班委",
            operator_role="admin",
            class_id=1,
            business_at=at,
            items_signature=json.dumps([[item["id"], 1]]),
        )
    )
    db_session.add(
        RestockDisposition(
            consumable_id=item["id"], class_id=1, status="pending"
        )
    )
    db_session.commit()

    _login(client, "admin01", "Admin@123")
    response = client.get(
        f"{LIST_URL}/{item['id']}/deactivation-preview"
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["consumable"]["current_stock"] == 8
    assert data["consumable"]["version"] == 1
    assert [row["order_no"] for row in data["pending_receipts"]] == [
        "RSK-20261128-PREVIEW1"
    ]
    assert data["pending_receipts"][0]["quantity"] == 5
    kinds = {row["kind"]: row["count"] for row in data["pending_actions"]}
    assert kinds == {"requisition_order": 1, "disposition": 1}


def test_deactivation_preview_forbidden_for_non_admin(
    teacher_client: TestClient,
) -> None:
    response = teacher_client.get(f"{LIST_URL}/1/deactivation-preview")

    assert response.status_code == 403


def test_disable_persists_reason_operator_and_before_after(
    admin_client: TestClient, db_session
) -> None:
    item = _create(admin_client, current_stock=10)

    response = admin_client.patch(
        f"{LIST_URL}/{item['id']}/status",
        json={
            "status": "disabled",
            "reason": "学期结束回收",
            "expected_version": 1,
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "disabled"
    assert data["disabled_reason"] == "学期结束回收"
    assert data["version"] == 2
    assert data["audit"] == {
        "previous_stock": 10,
        "current_stock": 10,
        "balance_changed": False,
        "reason": "学期结束回收",
    }

    listed = admin_client.get(f"{LIST_URL}?status=disabled").json()
    assert listed[0]["disabled_reason"] == "学期结束回收"

    log = db_session.scalars(
        select(OperationLog).where(
            OperationLog.action == "consumable_disable"
        )
    ).one()
    assert log.operator_id == _user_id(db_session, "admin01")
    assert log.remark == "学期结束回收"
    assert '"status": "enabled"' in (log.before_value or "")
    assert '"status": "disabled"' in (log.after_value or "")
    assert "学期结束回收" in (log.after_value or "")


def test_disabled_consumable_ledger_and_reversal_visible(
    client: TestClient,
) -> None:
    _login(client, "admin01", "Admin@123")
    item = _create(client, current_stock=10)
    _login(client, "student01", "Student@123")
    created = _requisition(client, item["id"], 2, "req-ledger-1")
    assert created.status_code == 201, created.text
    line = _receipt_line(client, "req-ledger-1")

    _login(client, "admin01", "Admin@123")
    disabled = client.patch(
        f"{LIST_URL}/{item['id']}/status",
        json={"status": "disabled", "reason": "盘点暂停", "expected_version": 1},
    )
    assert disabled.status_code == 200, disabled.text

    _login(client, "teacher01", "Teacher@123")
    ledger = client.get(LEDGER_URL, params={"consumable_id": item["id"]})
    assert ledger.status_code == 200, ledger.text
    assert ledger.json()["current_stock"] == 8

    _login(client, "student01", "Student@123")
    denied = client.get(LEDGER_URL, params={"consumable_id": item["id"]})
    assert denied.status_code == 403

    _login(client, "teacher01", "Teacher@123")
    reversal = client.post(
        f"{REQ_URL}/{line['requisition_id']}/reversal",
        json={"reason": "登记数量有误"},
    )
    assert reversal.status_code == 200, reversal.text

    ledger = client.get(LEDGER_URL, params={"consumable_id": item["id"]})
    assert ledger.status_code == 200, ledger.text
    data = ledger.json()
    assert data["current_stock"] == 10
    reversal_events = [
        event for event in data["events"] if event["event_type"] == "reversal"
    ]
    assert len(reversal_events) == 1
    assert reversal_events[0]["delta"] == 2
    assert reversal_events[0]["stock_after"] == 10


def test_reenable_reports_balance_drift(client: TestClient) -> None:
    _login(client, "admin01", "Admin@123")
    item = _create(client, current_stock=10)
    _login(client, "student01", "Student@123")
    created = _requisition(client, item["id"], 2, "req-reenable-1")
    assert created.status_code == 201, created.text
    line = _receipt_line(client, "req-reenable-1")

    _login(client, "admin01", "Admin@123")
    disabled = client.patch(
        f"{LIST_URL}/{item['id']}/status",
        json={"status": "disabled", "reason": "学期结束", "expected_version": 1},
    )
    assert disabled.status_code == 200, disabled.text

    _login(client, "teacher01", "Teacher@123")
    reversal = client.post(
        f"{REQ_URL}/{line['requisition_id']}/reversal",
        json={"reason": "重复登记"},
    )
    assert reversal.status_code == 200, reversal.text

    _login(client, "admin01", "Admin@123")
    enabled = client.patch(
        f"{LIST_URL}/{item['id']}/status",
        json={
            "status": "enabled",
            "reason": "新学期复用",
            "expected_version": 2,
        },
    )

    assert enabled.status_code == 200, enabled.text
    data = enabled.json()
    assert data["status"] == "enabled"
    assert data["disabled_reason"] is None
    assert data["version"] == 3
    assert data["audit"] == {
        "previous_stock": 8,
        "current_stock": 10,
        "balance_changed": True,
        "reason": "新学期复用",
    }


def test_stale_update_tells_late_writer_to_recheck(
    admin_client: TestClient, db_session
) -> None:
    item = _create(admin_client)

    first = admin_client.put(
        f"{LIST_URL}/{item['id']}",
        json={"name": "焊丝A", "expected_version": 1},
    )
    assert first.status_code == 200, first.text
    assert first.json()["version"] == 2

    stale = admin_client.put(
        f"{LIST_URL}/{item['id']}",
        json={"name": "焊丝B", "expected_version": 1},
    )

    assert stale.status_code == 409, stale.text
    detail = stale.json()["detail"]
    assert detail["message"] == "档案已被他人修改，请重新核对后再提交"
    assert detail["version"] == 2
    assert detail["current"]["name"] == "焊丝A"
    assert admin_client.get(LIST_URL).json()[0]["name"] == "焊丝A"

    conflict = db_session.scalars(
        select(OperationLog).where(
            OperationLog.action == "consumable_conflict"
        )
    ).one()
    assert conflict.operator_id == _user_id(db_session, "admin01")
    assert '"expected_version": 1' in (conflict.before_value or "")
    assert "焊丝B" in (conflict.before_value or "")
    assert "焊丝A" in (conflict.after_value or "")


def test_stale_status_update_conflicts_with_late_writer(
    admin_client: TestClient, db_session
) -> None:
    item = _create(admin_client)
    first = admin_client.patch(
        f"{LIST_URL}/{item['id']}/status",
        json={"status": "disabled", "reason": "学期结束", "expected_version": 1},
    )
    assert first.status_code == 200, first.text

    stale = admin_client.patch(
        f"{LIST_URL}/{item['id']}/status",
        json={"status": "enabled", "expected_version": 1},
    )

    assert stale.status_code == 409, stale.text
    assert (
        stale.json()["detail"]["message"]
        == "档案已被他人修改，请重新核对后再提交"
    )
    assert stale.json()["detail"]["current"]["status"] == "disabled"
    conflict = db_session.scalars(
        select(OperationLog).where(
            OperationLog.action == "consumable_conflict"
        )
    ).one()
    assert '"status": "enabled"' in (conflict.before_value or "")
    assert '"status": "disabled"' in (conflict.after_value or "")


def test_started_registration_survives_disable_but_new_one_rejected(
    client: TestClient,
) -> None:
    _login(client, "admin01", "Admin@123")
    item = _create(client, current_stock=10)

    _login(client, "student01", "Student@123")
    created = _requisition(client, item["id"], 2, "req-started-1")
    assert created.status_code == 201, created.text

    _login(client, "admin01", "Admin@123")
    disabled = client.patch(
        f"{LIST_URL}/{item['id']}/status",
        json={"status": "disabled", "reason": "学期结束", "expected_version": 1},
    )
    assert disabled.status_code == 200, disabled.text

    _login(client, "student01", "Student@123")
    repeated = _requisition(client, item["id"], 2, "req-started-1")
    assert repeated.status_code in (200, 201), repeated.text
    assert repeated.json()["status"] == "submitted"
    assert repeated.json()["count"] == 1

    fresh = _requisition(client, item["id"], 1, "req-started-2")
    assert fresh.status_code == 400, fresh.text
    assert "已停用，不可领用" in fresh.text
    status = client.get(f"{REQ_URL}/requests/req-started-2").json()
    assert status["status"] == "rejected"
    assert "已停用" in (status["error_detail"] or "")


def test_blank_reason_is_normalized_to_none(
    admin_client: TestClient,
) -> None:
    item = _create(admin_client)

    response = admin_client.patch(
        f"{LIST_URL}/{item['id']}/status",
        json={"status": "disabled", "reason": "   "},
    )

    assert response.status_code == 200
    assert response.json()["disabled_reason"] is None
