from typing import Any

import pytest
from app.models import Consumable, InventoryEvent, OperationLog, Requisition, User
from fastapi.testclient import TestClient
from sqlalchemy import select

BASE_URL = "/api/v1/requisitions"
CONSUMABLES_URL = "/api/v1/consumables"

STUDENT = ("student01", "Student@123")
TEACHER = ("teacher01", "Teacher@123")
ADMIN = ("admin01", "Admin@123")


def _login(client: TestClient, account: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"account": account, "password": password}
    )
    assert response.status_code == 200, response.text


def _create_consumable(admin_client: TestClient, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "焊丝",
        "spec": "φ1.2mm",
        "unit": "卷",
        "location": "实训室一号柜",
        "safety_stock": 5,
        "current_stock": 10,
    }
    payload.update(overrides)
    response = admin_client.post(CONSUMABLES_URL, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _submit_one(client: TestClient, consumable_id: int, quantity: int = 3):
    return client.post(
        BASE_URL,
        json={"items": [{"consumable_id": consumable_id, "quantity": quantity}]},
    )


def _submit_correction(
    client: TestClient, requisition_id: int, reason: str = "数量登记错了", **extra: Any
):
    payload: dict[str, Any] = {"reason": reason}
    payload.update(extra)
    return client.post(f"{BASE_URL}/{requisition_id}/corrections", json=payload)


def _reverse(client: TestClient, requisition_id: int, **payload: Any):
    body: dict[str, Any] = {"reason": "登记错误"}
    body.update(payload)
    return client.post(f"{BASE_URL}/{requisition_id}/reversal", json=body)


def _handle(
    client: TestClient,
    requisition_id: int,
    correction_id: int,
    note: str = "已核对，驳回",
    status_value: str = "rejected",
):
    return client.patch(
        f"{BASE_URL}/{requisition_id}/corrections/{correction_id}",
        json={"status": status_value, "handling_note": note},
    )


def _records(db_session) -> list[Requisition]:
    return list(db_session.scalars(select(Requisition).order_by(Requisition.id)).all())


def _first_id(db_session) -> int:
    return db_session.scalars(select(Requisition).order_by(Requisition.id)).first().id


def _db_stock(db_session, consumable_id: int) -> int:
    db_session.expire_all()
    return db_session.get(Consumable, consumable_id).current_stock


def _events(db_session, consumable_id: int) -> list[InventoryEvent]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(InventoryEvent)
            .where(InventoryEvent.consumable_id == consumable_id)
            .order_by(InventoryEvent.exec_seq, InventoryEvent.id)
        ).all()
    )


def _logs(db_session, action: str) -> list[OperationLog]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(OperationLog).where(OperationLog.action == action)
        ).all()
    )


def test_student_sees_correction_status_and_cannot_reverse(
    client: TestClient, db_session
) -> None:
    _login(client, *ADMIN)
    consumable = _create_consumable(client)
    _login(client, *STUDENT)
    assert _submit_one(client, consumable["id"], quantity=3).status_code == 201
    requisition_id = _first_id(db_session)

    response = _submit_correction(client, requisition_id, reason="实际只领了 1 卷")
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["status"] == "submitted"
    assert data["requisition_id"] == requisition_id
    assert data["applicant_name"] == "张同学"
    assert data["handling_note"] is None

    listed = client.get(f"{BASE_URL}/{requisition_id}/corrections")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [data["id"]]
    assert listed.json()[0]["status"] == "submitted"

    detail = client.get(f"{BASE_URL}/{requisition_id}").json()
    assert detail["correction"]["status"] == "submitted"
    assert detail["correction"]["reason"] == "实际只领了 1 卷"

    page = client.get(BASE_URL).json()
    assert page["items"][0]["correction_status"] == "submitted"

    forbidden = _reverse(client, requisition_id, reason="学生直接红冲")
    assert forbidden.status_code == 403
    assert _db_stock(db_session, consumable["id"]) == 7
    assert _records(db_session)[0].status == "active"
    assert _logs(db_session, "requisition_reverse") == []


def test_student_cannot_submit_correction_for_other_record(
    client: TestClient, db_session
) -> None:
    _login(client, *ADMIN)
    consumable = _create_consumable(client)
    assert _submit_one(client, consumable["id"], quantity=2).status_code == 201
    requisition_id = _first_id(db_session)
    assert _records(db_session)[0].user_id != db_session.scalars(
        select(User).where(User.account == "student01")
    ).one().id

    _login(client, *STUDENT)
    response = _submit_correction(client, requisition_id)
    assert response.status_code == 403
    assert "本人记录" in response.json()["detail"]


def test_teacher_accepts_correction_with_reversal(
    client: TestClient, db_session
) -> None:
    _login(client, *ADMIN)
    consumable = _create_consumable(client)
    _login(client, *STUDENT)
    assert _submit_one(client, consumable["id"], quantity=3).status_code == 201
    requisition_id = _first_id(db_session)
    correction = _submit_correction(client, requisition_id).json()

    _login(client, *TEACHER)
    response = _reverse(
        client,
        requisition_id,
        reason="数量登记错误",
        remark="已核对原单",
        correction_id=correction["id"],
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["type"] == "reversal"
    assert data["order_no"].startswith("RVL-")
    assert data["reversal_of"] == requisition_id
    assert data["quantity"] == 3
    assert data["stock_after"] == 10
    assert data["handler_name"] == "王老师"
    assert data["correction"]["status"] == "accepted"
    assert data["correction"]["handling_note"] == "已核对原单"
    assert data["correction"]["handler_name"] == "王老师"
    assert data["correction"]["reversal_order_no"] == data["order_no"]

    original = db_session.get(Requisition, requisition_id)
    assert original.status == "reversed"
    assert original.quantity == 3
    assert original.reversal_reason is None
    assert _db_stock(db_session, consumable["id"]) == 10

    events = _events(db_session, consumable["id"])
    reversal_events = [
        event for event in events if event.event_type == "reversal"
    ]
    assert len(reversal_events) == 1
    event = reversal_events[0]
    assert event.delta == 3
    assert event.stock_before == 7
    assert event.stock_after == 10
    assert event.source_type == "requisition_reversal"
    assert event.source_id == requisition_id
    assert event.requisition_id == data["id"]
    assert event.operator_name == "王老师"

    corrections = client.get(f"{BASE_URL}/{requisition_id}/corrections").json()
    assert corrections[0]["status"] == "accepted"
    assert corrections[0]["reversal_order_no"] == data["order_no"]
    assert corrections[0]["handled_at"] is not None

    detail = client.get(f"{BASE_URL}/{requisition_id}").json()
    assert detail["status"] == "reversed"
    assert detail["reversal"]["order_no"] == data["order_no"]
    assert detail["reversal"]["type"] == "reversal"

    ledger = client.get(
        "/api/v1/inventory/ledger", params={"consumable_id": consumable["id"]}
    )
    assert ledger.status_code == 200
    ledger_data = ledger.json()
    assert ledger_data["consistent"] is True
    ledger_event = next(
        item for item in ledger_data["events"] if item["event_type"] == "reversal"
    )
    assert ledger_event["order_no"] == data["order_no"]

    assert len(_logs(db_session, "requisition_reverse")) == 1
    assert len(_logs(db_session, "requisition_correction_accept")) == 1
    assert len(_logs(db_session, "requisition_reentry")) == 0


def test_reversal_with_reentry_links_both_orders(client: TestClient, db_session) -> None:
    _login(client, *ADMIN)
    consumable = _create_consumable(client)
    _login(client, *STUDENT)
    assert _submit_one(client, consumable["id"], quantity=3).status_code == 201
    requisition_id = _first_id(db_session)
    _submit_correction(client, requisition_id, reason="应为 2 卷")


    _login(client, *TEACHER)
    response = _reverse(
        client,
        requisition_id,
        reason="数量登记错误",
        remark="已核实",
        reentry={"quantity": 2, "remark": "更正为 2 卷"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    reversal_order_no = data["order_no"]
    reentry = data["reentry"]
    assert reversal_order_no.startswith("RVL-")
    assert reentry["order_no"].startswith("REN-")
    assert reentry["order_no"] != reversal_order_no
    assert reentry["type"] == "normal"
    assert reentry["quantity"] == 2
    assert reentry["status"] == "active"
    assert data["correction"]["status"] == "accepted"
    assert data["correction"]["reentry_order_no"] == reentry["order_no"]

    records = _records(db_session)
    assert len(records) == 3
    original, reversal, reentry_record = records
    assert original.status == "reversed"
    assert reversal.type == "reversal"
    assert reversal.order_no == reversal_order_no
    assert reentry_record.reentry_of == reversal.id
    assert reentry_record.order_no == reentry["order_no"]
    assert reentry_record.remark == "更正为 2 卷"
    assert reentry_record.user_id == original.user_id
    assert reentry_record.handler_name == "王老师"
    assert _db_stock(db_session, consumable["id"]) == 8

    reentry_detail = client.get(f"{BASE_URL}/{reentry['id']}").json()
    assert reentry_detail["reentry_of"] == reversal.id
    assert reentry_detail["reversal"]["id"] == reversal.id
    assert reentry_detail["reversal"]["order_no"] == reversal_order_no
    assert reentry_detail["reversal"]["order_no"] != reentry_detail["order_no"]

    reversal_detail = client.get(f"{BASE_URL}/{reversal.id}").json()
    assert reversal_detail["origin"]["id"] == requisition_id
    assert reversal_detail["reentry"]["order_no"] == reentry["order_no"]
    assert reversal_detail["reentry"]["id"] == reentry["id"]

    original_detail = client.get(f"{BASE_URL}/{requisition_id}").json()
    assert original_detail["reversal"]["order_no"] == reversal_order_no
    assert original_detail["reentry"]["order_no"] == reentry["order_no"]
    assert original_detail["quantity"] == 3

    events = _events(db_session, consumable["id"])
    assert [(event.event_type, event.delta) for event in events] == [
        ("opening", 10),
        ("requisition", -3),
        ("reversal", 3),
        ("requisition", -2),
    ]
    assert events[-1].source_type == "requisition_reentry"
    assert events[-1].requisition_id == reentry["id"]
    assert events[-1].operator_name == "王老师"

    assert len(_logs(db_session, "requisition_reentry")) == 1
    assert len(_logs(db_session, "requisition_reverse")) == 1


def test_reentry_quantity_above_origin_rejected(
    client: TestClient, db_session
) -> None:
    _login(client, *ADMIN)
    consumable = _create_consumable(client)
    _login(client, *STUDENT)
    assert _submit_one(client, consumable["id"], quantity=2).status_code == 201
    requisition_id = _first_id(db_session)

    _login(client, *TEACHER)
    response = _reverse(
        client, requisition_id, reason="错误", reentry={"quantity": 3}
    )
    assert response.status_code == 400
    assert "不能超过原领用数量" in response.json()["detail"]
    assert _db_stock(db_session, consumable["id"]) == 8
    assert _records(db_session)[0].status == "active"
    assert _events(db_session, consumable["id"])[-1].event_type == "requisition"


def test_reentry_on_disabled_consumable_rolls_back(
    client: TestClient, db_session
) -> None:
    _login(client, *ADMIN)
    consumable = _create_consumable(client)
    _login(client, *STUDENT)
    assert _submit_one(client, consumable["id"], quantity=2).status_code == 201
    requisition_id = _first_id(db_session)

    _login(client, *ADMIN)
    response = client.patch(
        f"{CONSUMABLES_URL}/{consumable['id']}/status", json={"status": "disabled"}
    )
    assert response.status_code == 200

    _login(client, *TEACHER)
    response = _reverse(
        client, requisition_id, reason="错误", reentry={"quantity": 1}
    )
    assert response.status_code == 400
    assert "已停用" in response.json()["detail"]
    assert _db_stock(db_session, consumable["id"]) == 8
    assert _records(db_session)[0].status == "active"
    assert len(_records(db_session)) == 1
    assert [event.event_type for event in _events(db_session, consumable["id"])] == [
        "opening",
        "requisition",
    ]


def test_teacher_rejects_correction_with_result(
    client: TestClient, db_session
) -> None:
    _login(client, *ADMIN)
    consumable = _create_consumable(client)
    _login(client, *STUDENT)
    assert _submit_one(client, consumable["id"], quantity=2).status_code == 201
    requisition_id = _first_id(db_session)
    correction = _submit_correction(client, requisition_id, reason="可能多记了").json()

    _login(client, *TEACHER)
    response = _handle(client, requisition_id, correction["id"], note="核对无误，无需红冲")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "rejected"
    assert data["handler_name"] == "王老师"
    assert data["handling_note"] == "核对无误，无需红冲"
    assert data["handled_at"] is not None
    assert data["reversal_id"] is None

    _login(client, *STUDENT)
    listed = client.get(f"{BASE_URL}/{requisition_id}/corrections").json()
    assert listed[0]["status"] == "rejected"
    assert listed[0]["handling_note"] == "核对无误，无需红冲"
    assert _db_stock(db_session, consumable["id"]) == 8
    assert _records(db_session)[0].status == "active"
    assert len(_logs(db_session, "requisition_correction_handle")) == 1


def test_correction_request_idempotent_and_conflict(
    client: TestClient, db_session
) -> None:
    _login(client, *ADMIN)
    consumable = _create_consumable(client)
    _login(client, *STUDENT)
    assert _submit_one(client, consumable["id"], quantity=1).status_code == 201
    requisition_id = _first_id(db_session)

    first = _submit_correction(
        client, requisition_id, reason="登记错", request_id="corr-req-1"
    )
    assert first.status_code == 201
    repeat = _submit_correction(
        client, requisition_id, reason="登记错", request_id="corr-req-1"
    )
    assert repeat.status_code == 200
    assert repeat.json()["id"] == first.json()["id"]

    conflict = _submit_correction(
        client, requisition_id, reason="内容不同", request_id="corr-req-1"
    )
    assert conflict.status_code == 409

    pending = _submit_correction(
        client, requisition_id, reason="再来一条", request_id="corr-req-2"
    )
    assert pending.status_code == 409
    assert "待处理" in pending.json()["detail"]


def test_correction_rejected_for_reversed_record(
    client: TestClient, db_session
) -> None:
    _login(client, *ADMIN)
    consumable = _create_consumable(client)
    _login(client, *STUDENT)
    assert _submit_one(client, consumable["id"], quantity=1).status_code == 201
    requisition_id = _first_id(db_session)

    _login(client, *TEACHER)
    assert _reverse(client, requisition_id).status_code == 200

    _login(client, *STUDENT)
    response = _submit_correction(client, requisition_id)
    assert response.status_code == 400
    assert "仅有效领用" in response.json()["detail"]


def test_cross_class_manager_cannot_handle(client: TestClient, db_session) -> None:
    _login(client, *ADMIN)
    consumable = _create_consumable(client)
    _login(client, *STUDENT)
    assert _submit_one(client, consumable["id"], quantity=1).status_code == 201
    requisition_id = _first_id(db_session)
    correction = _submit_correction(client, requisition_id).json()

    teacher = db_session.scalars(
        select(User).where(User.account == "teacher01")
    ).one()
    teacher.class_id = 2
    db_session.commit()

    _login(client, *TEACHER)
    reverse_response = _reverse(client, requisition_id)
    assert reverse_response.status_code == 403
    handle_response = _handle(client, requisition_id, correction["id"])
    assert handle_response.status_code == 403
    assert _db_stock(db_session, consumable["id"]) == 9
    assert _records(db_session)[0].status == "active"


def test_correction_requires_reason(client: TestClient, db_session) -> None:
    _login(client, *ADMIN)
    consumable = _create_consumable(client)
    _login(client, *STUDENT)
    assert _submit_one(client, consumable["id"], quantity=1).status_code == 201
    requisition_id = _first_id(db_session)

    blank = _submit_correction(client, requisition_id, reason="   ")
    assert blank.status_code == 422
    missing = client.post(f"{BASE_URL}/{requisition_id}/corrections", json={})
    assert missing.status_code == 422


@pytest.mark.parametrize("status_value", ["submitted", "accepted", "done"])
def test_handle_only_allows_rejected(
    client: TestClient, db_session, status_value: str
) -> None:
    _login(client, *ADMIN)
    consumable = _create_consumable(client)
    _login(client, *STUDENT)
    assert _submit_one(client, consumable["id"], quantity=1).status_code == 201
    requisition_id = _first_id(db_session)
    correction = _submit_correction(client, requisition_id).json()

    _login(client, *TEACHER)
    response = _handle(
        client, requisition_id, correction["id"], status_value=status_value
    )
    assert response.status_code == 422
