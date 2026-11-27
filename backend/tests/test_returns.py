from typing import Any

from app.models import (
    Consumable,
    InventoryEvent,
    OperationLog,
    Requisition,
    RequisitionReturn,
    RequisitionScrap,
)
from fastapi.testclient import TestClient
from sqlalchemy import select

BASE_URL = "/api/v1/requisitions"
CONSUMABLES_URL = "/api/v1/consumables"
LEDGER_URL = "/api/v1/inventory/ledger"


def _consumable_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "焊丝",
        "spec": "φ1.2mm",
        "unit": "卷",
        "location": "实训室一号柜",
        "safety_stock": 5,
        "current_stock": 10,
    }
    payload.update(overrides)
    return payload


def _create_consumable(client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = client.post(CONSUMABLES_URL, json=_consumable_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def _submit(client: TestClient, consumable_id: int, quantity: int = 10):
    return client.post(
        BASE_URL,
        json={"items": [{"consumable_id": consumable_id, "quantity": quantity}]},
    )


def _return(client: TestClient, requisition_id: int, quantity: int, **extra: Any):
    payload: dict[str, Any] = {"quantity": quantity}
    payload.update(extra)
    return client.post(f"{BASE_URL}/{requisition_id}/returns", json=payload)


def _scrap(client: TestClient, requisition_id: int, quantity: int, **extra: Any):
    payload: dict[str, Any] = {"quantity": quantity, "reason": "耗材破损无法复用"}
    payload.update(extra)
    return client.post(f"{BASE_URL}/{requisition_id}/scraps", json=payload)


def _requisitions(db_session) -> list[Requisition]:
    db_session.expire_all()
    return list(db_session.scalars(select(Requisition).order_by(Requisition.id)).all())


def _first_requisition_id(db_session) -> int:
    return _requisitions(db_session)[0].id


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


def _login(client: TestClient, account: str) -> None:
    password = {
        "teacher01": "Teacher@123",
        "admin01": "Admin@123",
        "student01": "Student@123",
    }[account]
    response = client.post(
        "/api/v1/auth/login", json={"account": account, "password": password}
    )
    assert response.status_code == 200, response.text


def test_partial_return_adds_only_returned_quantity(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client)
    assert _submit(admin_client, consumable["id"], quantity=10).status_code == 201
    requisition_id = _first_requisition_id(db_session)

    response = _return(admin_client, requisition_id, 2, reason="未使用归还")

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["requisition_id"] == requisition_id
    assert data["quantity"] == 2
    assert data["stock_before"] == 0
    assert data["stock_after"] == 2
    assert data["order_no"].startswith("RTN-")
    assert data["event_id"] is not None
    assert data["remark"] is None

    assert _db_stock(db_session, consumable["id"]) == 2

    original = _requisitions(db_session)[0]
    assert original.id == requisition_id
    assert original.quantity == 10
    assert original.stock_after == 0
    assert original.status == "active"
    assert len(_requisitions(db_session)) == 1

    events = _events(db_session, consumable["id"])
    return_event = events[-1]
    assert return_event.event_type == "return"
    assert return_event.delta == 2
    assert return_event.stock_before == 0
    assert return_event.stock_after == 2
    assert return_event.source_type == "requisition_return"
    assert return_event.source_id == data["id"]
    assert return_event.requisition_id == requisition_id
    assert return_event.operator_name == "李班委"


def test_detail_traces_original_requisition(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client)
    assert _submit(admin_client, consumable["id"], quantity=10).status_code == 201
    requisition_id = _first_requisition_id(db_session)
    returned = _return(admin_client, requisition_id, 2, reason="未使用归还")
    assert returned.status_code == 201, returned.text

    detail = admin_client.get(f"{BASE_URL}/{requisition_id}")
    assert detail.status_code == 200, detail.text
    data = detail.json()
    assert data["returned_quantity"] == 2
    assert data["scrapped_quantity"] == 0
    assert data["returnable_quantity"] == 8
    assert len(data["returns"]) == 1
    assert data["returns"][0]["requisition_id"] == requisition_id
    assert data["returns"][0]["order_no"] == returned.json()["order_no"]
    assert data["returns"][0]["quantity"] == 2



def test_over_return_rejected(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    assert _submit(admin_client, consumable["id"], quantity=10).status_code == 201
    requisition_id = _first_requisition_id(db_session)
    assert _return(admin_client, requisition_id, 2).status_code == 201

    response = _return(admin_client, requisition_id, 9)

    assert response.status_code == 400
    assert "退库数量不能超过可退数量 8" in response.json()["detail"]
    assert _db_stock(db_session, consumable["id"]) == 2
    db_session.expire_all()
    assert len(db_session.scalars(select(RequisitionReturn)).all()) == 1


def test_scrap_records_loss_without_changing_stock(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client)
    assert _submit(admin_client, consumable["id"], quantity=10).status_code == 201
    requisition_id = _first_requisition_id(db_session)

    missing_reason = admin_client.post(
        f"{BASE_URL}/{requisition_id}/scraps", json={"quantity": 3}
    )
    assert missing_reason.status_code == 422

    response = _scrap(admin_client, requisition_id, 3, reason="摔坏无法复用")

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["reason"] == "摔坏无法复用"
    assert data["quantity"] == 3
    assert data["stock_before"] == 0
    assert data["stock_after"] == 0
    assert data["order_no"].startswith("SCP-")
    assert data["operator_name"] == "李班委"

    assert _db_stock(db_session, consumable["id"]) == 0

    events = _events(db_session, consumable["id"])
    scrap_event = events[-1]
    assert scrap_event.event_type == "scrap"
    assert scrap_event.delta == 0
    assert scrap_event.stock_before == scrap_event.stock_after == 0
    assert scrap_event.source_type == "requisition_scrap"
    assert scrap_event.source_id == data["id"]
    assert scrap_event.requisition_id == requisition_id

    detail = admin_client.get(f"{BASE_URL}/{requisition_id}").json()
    assert detail["returned_quantity"] == 0
    assert detail["scrapped_quantity"] == 3
    assert detail["returnable_quantity"] == 7
    assert detail["scraps"][0]["reason"] == "摔坏无法复用"
    assert detail["scraps"][0]["requisition_id"] == requisition_id


def test_return_and_scrap_share_original_quantity_cap(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client)
    assert _submit(admin_client, consumable["id"], quantity=10).status_code == 201
    requisition_id = _first_requisition_id(db_session)
    assert _return(admin_client, requisition_id, 2).status_code == 201
    assert _scrap(admin_client, requisition_id, 8).status_code == 201

    over_return = _return(admin_client, requisition_id, 1)
    over_scrap = _scrap(admin_client, requisition_id, 1)

    assert over_return.status_code == 400
    assert "可退数量 0" in over_return.json()["detail"]
    assert over_scrap.status_code == 400
    assert "可退数量 0" in over_scrap.json()["detail"]
    assert _db_stock(db_session, consumable["id"]) == 2


def test_reversed_requisition_cannot_be_returned_or_scrapped(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client)
    assert _submit(admin_client, consumable["id"], quantity=3).status_code == 201
    requisition_id = _first_requisition_id(db_session)
    reversed_response = admin_client.post(
        f"{BASE_URL}/{requisition_id}/reversal", json={"reason": "登记错误"}
    )
    assert reversed_response.status_code == 200, reversed_response.text

    return_response = _return(admin_client, requisition_id, 1)
    scrap_response = _scrap(admin_client, requisition_id, 1)

    assert return_response.status_code == 400
    assert scrap_response.status_code == 400
    assert "仅有效领用记录" in return_response.json()["detail"]
    assert "仅有效领用记录" in scrap_response.json()["detail"]


def test_student_can_return_own_but_not_scrap_or_others(
    client: TestClient, db_session
) -> None:
    _login(client, "admin01")
    admin_consumable = _create_consumable(client, name="锉刀", spec="8寸")
    student_consumable = _create_consumable(client, name="砂纸", spec="240目")
    assert _submit(client, admin_consumable["id"], quantity=2).status_code == 201
    admin_requisition_id = _requisitions(db_session)[0].id

    _login(client, "student01")
    student_requisition_response = _submit(
        client, student_consumable["id"], quantity=2
    )
    assert student_requisition_response.status_code == 201
    db_session.expire_all()
    student_requisition_id = db_session.scalars(
        select(Requisition)
        .where(Requisition.consumable_id == student_consumable["id"])
        .order_by(Requisition.id.desc())
    ).first().id

    own_return = _return(client, student_requisition_id, 1)
    assert own_return.status_code == 201, own_return.text
    assert _db_stock(db_session, student_consumable["id"]) == 9

    own_scrap = _scrap(client, student_requisition_id, 1)
    assert own_scrap.status_code == 403

    other_return = _return(client, admin_requisition_id, 1)
    assert other_return.status_code == 403


def test_return_idempotent_by_request_id(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client)
    assert _submit(admin_client, consumable["id"], quantity=5).status_code == 201
    requisition_id = _first_requisition_id(db_session)

    first = _return(admin_client, requisition_id, 1, request_id="ret-fixed-1")
    second = _return(admin_client, requisition_id, 1, request_id="ret-fixed-1")
    conflict = _return(admin_client, requisition_id, 2, request_id="ret-fixed-1")

    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    assert conflict.status_code == 409
    assert _db_stock(db_session, consumable["id"]) == 6


def test_inventory_ledger_links_return_and_scrap_orders(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client)
    assert _submit(admin_client, consumable["id"], quantity=10).status_code == 201
    requisition_id = _first_requisition_id(db_session)
    returned = _return(admin_client, requisition_id, 2)
    scrapped = _scrap(admin_client, requisition_id, 3)
    assert returned.status_code == 201
    assert scrapped.status_code == 201

    response = admin_client.get(
        LEDGER_URL, params={"consumable_id": consumable["id"]}
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["consistent"] is True

    by_type = {event["event_type"]: event for event in data["events"]}
    assert by_type["return"]["delta"] == 2
    assert by_type["return"]["order_no"] == returned.json()["order_no"]
    assert by_type["scrap"]["delta"] == 0
    assert by_type["scrap"]["order_no"] == scrapped.json()["order_no"]


def test_return_and_scrap_written_to_logs(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client)
    assert _submit(admin_client, consumable["id"], quantity=4).status_code == 201
    requisition_id = _first_requisition_id(db_session)
    assert _return(admin_client, requisition_id, 1, reason="部分归还").status_code == 201
    assert _scrap(admin_client, requisition_id, 2, reason="破损").status_code == 201

    db_session.expire_all()
    logs = db_session.scalars(
        select(OperationLog)
        .where(OperationLog.action.in_(["requisition_return", "requisition_scrap"]))
        .order_by(OperationLog.id)
    ).all()
    assert [log.action for log in logs] == ["requisition_return", "requisition_scrap"]
    assert all(log.target_id == requisition_id for log in logs)
    assert "部分归还" in logs[0].after_value
    assert "破损" in logs[1].after_value