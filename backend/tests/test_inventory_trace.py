from datetime import datetime

from app import inventory_ledger
from app.models import (
    Consumable,
    InventoryCount,
    InventoryCountLine,
    InventoryEvent,
    Requisition,
    RequisitionCorrection,
    RequisitionOrder,
    RequisitionReturn,
    RequisitionScrap,
    RestockCorrection,
    RestockLine,
    RestockOrder,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select

TRACE_URL = "/api/v1/inventory/events/{event_id}/trace"
CONSUMABLES_URL = "/api/v1/consumables"
REQUISITIONS_URL = "/api/v1/requisitions"
RESTOCK_ORDERS_URL = "/api/v1/inventory/restock/orders"
COUNTS_URL = "/api/v1/inventory/counts"


def _login(client: TestClient, account: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"account": account, "password": password}
    )
    assert response.status_code == 200, response.text


def _login_student(client: TestClient) -> None:
    _login(client, "student01", "Student@123")


def _login_teacher(client: TestClient) -> None:
    _login(client, "teacher01", "Teacher@123")


def _login_admin(client: TestClient) -> None:
    _login(client, "admin01", "Admin@123")


def _create_consumable(admin_client: TestClient, **overrides) -> dict:
    payload = {
        "name": "焊丝",
        "spec": "φ1.2mm",
        "unit": "卷",
        "location": "一号柜",
        "safety_stock": 5,
        "current_stock": 10,
    }
    payload.update(overrides)
    response = admin_client.post(CONSUMABLES_URL, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _user_id(db_session, account: str) -> int:
    return db_session.scalars(select(User).where(User.account == account)).one().id


def _issue(
    client: TestClient, db_session, consumable_id: int, quantity: int, **extra
) -> dict:
    payload = {
        "user_id": _user_id(db_session, "student01"),
        "items": [{"consumable_id": consumable_id, "quantity": quantity}],
    }
    payload.update(extra)
    response = client.post(REQUISITIONS_URL, json=payload)
    assert response.status_code == 201, response.text
    data = response.json()
    return data.get("receipt", data)


def _event(
    db_session, consumable_id: int, event_type: str, source_type: str | None = None
) -> InventoryEvent:
    db_session.expire_all()
    query = select(InventoryEvent).where(
        InventoryEvent.consumable_id == consumable_id,
        InventoryEvent.event_type == event_type,
    )
    if source_type is not None:
        query = query.where(InventoryEvent.source_type == source_type)
    event = db_session.scalars(
        query.order_by(InventoryEvent.exec_seq.desc(), InventoryEvent.id.desc())
    ).first()
    assert event is not None, f"missing {event_type} event"
    return event


def _trace(client: TestClient, event_id: int, **params) -> dict:
    response = client.get(
        TRACE_URL.format(event_id=event_id), params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_trace_requisition_event_links_order_and_record(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    _login_student(admin_client)
    receipt = _issue(
        admin_client, db_session, consumable["id"], 3, remark="课堂实训"
    )
    event_id = receipt["lines"][0]["event_id"]
    assert event_id is not None

    _login_teacher(admin_client)
    data = _trace(admin_client, event_id)

    core = data["event"]
    assert core["event_type"] == "requisition"
    assert core["event_label"] == "领用出库"
    assert core["delta"] == -3
    assert core["stock_before"] == 10
    assert core["stock_after"] == 7
    assert core["unit_snapshot"] == "卷"
    assert core["operator_name"] == "张同学"
    assert core["order_no"] == receipt["order_no"]
    assert core["remark"] == "课堂实训"
    assert core["consumable_name"] == "焊丝"
    assert core["evidence_label"] == "可信"

    docs = data["timeline"]
    assert data["timeline_total"] == len(docs) == 2
    assert data["truncated"] is False
    assert data["limit"] == 50
    assert [doc["kind"] for doc in docs] == ["requisition_order", "requisition"]

    order_doc, record_doc = docs
    assert order_doc["kind_label"] == "领用单"
    assert order_doc["order_no"] == receipt["order_no"]
    assert order_doc["quantity"] == 3
    assert order_doc["unit"] == "卷"
    assert order_doc["status"] == "submitted"
    assert order_doc["status_label"] == "已提交"
    assert record_doc["kind_label"] == "领用流水"
    assert record_doc["quantity"] == 3
    assert record_doc["unit"] == "卷"
    assert record_doc["status_label"] == "有效"
    assert [doc["at"] for doc in docs] == sorted(doc["at"] for doc in docs)


def test_trace_reversal_chain_includes_correction_and_reentry(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    _login_student(admin_client)
    receipt = _issue(admin_client, db_session, consumable["id"], 4)
    requisition_id = receipt["lines"][0]["requisition_id"]
    correction = admin_client.post(
        f"{REQUISITIONS_URL}/{requisition_id}/corrections",
        json={"reason": "实际只领了 2 卷"},
    )
    assert correction.status_code == 201, correction.text
    correction_id = correction.json()["id"]

    _login_teacher(admin_client)
    reversal = admin_client.post(
        f"{REQUISITIONS_URL}/{requisition_id}/reversal",
        json={
            "reason": "数量登记错误",
            "remark": "已核对原单",
            "correction_id": correction_id,
            "reentry": {"quantity": 2, "remark": "更正为 2 卷"},
        },
    )
    assert reversal.status_code == 200, reversal.text

    reversal_event = _event(db_session, consumable["id"], "reversal")
    data = _trace(admin_client, reversal_event.id)

    core = data["event"]
    assert core["event_label"] == "红冲回补"
    assert core["delta"] == 4
    assert core["stock_before"] == 6
    assert core["stock_after"] == 10
    assert core["reason"] == "数量登记错误"
    assert core["remark"] == "已核对原单"
    assert core["order_no"].startswith("RVL-")

    docs = data["timeline"]
    kinds = [doc["kind"] for doc in docs]
    assert "requisition" in kinds
    assert "requisition_reversal" in kinds
    assert "requisition_reentry" in kinds
    assert "requisition_correction" in kinds
    assert kinds.count("requisition") == 1
    assert [doc["at"] for doc in docs] == sorted(doc["at"] for doc in docs)

    correction_doc = next(
        doc for doc in docs if doc["kind"] == "requisition_correction"
    )
    assert correction_doc["status_label"] == "已处理"
    assert correction_doc["operator_name"] == "张同学"
    assert correction_doc["reason"] == "实际只领了 2 卷"

    reentry_doc = next(doc for doc in docs if doc["kind"] == "requisition_reentry")
    assert reentry_doc["quantity"] == 2
    assert reentry_doc["order_no"].startswith("REN-")


def test_trace_return_and_scrap_are_traceable(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    _login_student(admin_client)
    receipt = _issue(admin_client, db_session, consumable["id"], 5)
    requisition_id = receipt["lines"][0]["requisition_id"]

    _login_admin(admin_client)
    returned = admin_client.post(
        f"{REQUISITIONS_URL}/{requisition_id}/returns",
        json={"quantity": 2, "reason": "未使用归还"},
    )
    assert returned.status_code == 201, returned.text
    scrapped = admin_client.post(
        f"{REQUISITIONS_URL}/{requisition_id}/scraps",
        json={"quantity": 1, "reason": "摔坏无法复用"},
    )
    assert scrapped.status_code == 201, scrapped.text

    return_event = _event(db_session, consumable["id"], "return")
    data = _trace(admin_client, return_event.id)
    core = data["event"]
    assert core["event_label"] == "退库回补"
    assert core["delta"] == 2
    assert core["reason"] == "未使用归还"
    assert core["order_no"].startswith("RTN-")

    kinds = [doc["kind"] for doc in data["timeline"]]
    assert "requisition" in kinds
    assert "requisition_return" in kinds
    assert "requisition_scrap" in kinds
    return_doc = next(
        doc for doc in data["timeline"] if doc["kind"] == "requisition_return"
    )
    assert return_doc["quantity"] == 2
    assert return_doc["unit"] == "卷"
    assert return_doc["status_label"] == "已退库"

    scrap_event = _event(db_session, consumable["id"], "scrap")
    scrap_data = _trace(admin_client, scrap_event.id)
    assert scrap_data["event"]["event_label"] == "报损核销"
    assert scrap_data["event"]["delta"] == 0
    assert scrap_data["event"]["reason"] == "摔坏无法复用"
    scrap_doc = next(
        doc for doc in scrap_data["timeline"] if doc["kind"] == "requisition_scrap"
    )
    assert scrap_doc["quantity"] == 1
    assert scrap_doc["status_label"] == "已报损"


def test_trace_restock_and_correction(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client, current_stock=0)
    order = admin_client.post(
        RESTOCK_ORDERS_URL,
        json={
            "items": [{"consumable_id": consumable["id"], "quantity": 5}],
            "source": "采购回库",
            "request_id": "trace-restock-1",
        },
    )
    assert order.status_code == 201, order.text
    order_no = order.json()["order_no"]

    restock_event = _event(db_session, consumable["id"], "restock")
    data = _trace(admin_client, restock_event.id)
    assert data["event"]["event_label"] == "入库上架"
    assert data["event"]["order_no"] == order_no
    assert data["event"]["reason"] == "采购回库"
    order_doc = next(
        doc for doc in data["timeline"] if doc["kind"] == "restock_order"
    )
    assert order_doc["quantity"] == 5
    assert order_doc["unit"] == "卷"
    assert order_doc["status_label"] == "已提交"

    correction = admin_client.post(
        f"{RESTOCK_ORDERS_URL}/{order_no}/corrections",
        json={
            "line_no": 1,
            "quantity": 2,
            "reason": "实收数量录错",
            "request_id": "trace-restock-correction-1",
        },
    )
    assert correction.status_code == 200, correction.text

    correction_event = _event(
        db_session, consumable["id"], "restock_correction"
    )
    correction_data = _trace(admin_client, correction_event.id)
    assert correction_data["event"]["event_label"] == "入库纠错扣回"
    assert correction_data["event"]["reason"] == "实收数量录错"
    assert [doc["kind"] for doc in correction_data["timeline"]] == [
        "restock_order",
        "restock_correction",
    ]
    correction_doc = correction_data["timeline"][1]
    assert correction_doc["quantity"] == 2
    assert correction_doc["status_label"] == "已扣回"
    assert correction_doc["order_no"] == order_no


def test_trace_inventory_count_adjustment(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    count = admin_client.post(
        COUNTS_URL,
        json={
            "lines": [
                {
                    "consumable_id": consumable["id"],
                    "counted_qty": 12,
                    "reason": "历史迁移",
                }
            ]
        },
    )
    assert count.status_code == 201, count.text
    order_no = count.json()["order_no"]
    posted = admin_client.post(f"{COUNTS_URL}/{order_no}/post")
    assert posted.status_code == 201, posted.text

    adjustment = _event(db_session, consumable["id"], "inventory_adjustment")
    data = _trace(admin_client, adjustment.id)
    assert data["event"]["event_label"] == "盘点调整"
    assert data["event"]["delta"] == 2
    assert data["event"]["reason"] == "历史迁移"
    assert data["event"]["order_no"] == order_no

    docs = data["timeline"]
    assert [doc["kind"] for doc in docs] == [
        "inventory_count",
        "inventory_count_line",
    ]
    assert docs[0]["status_label"] == "已入账"
    assert docs[1]["quantity"] == 12
    assert docs[1]["unit"] == "卷"
    assert docs[1]["reason"] == "历史迁移"


def test_trace_requires_login(client: TestClient) -> None:
    assert client.get(TRACE_URL.format(event_id=1)).status_code == 401


def test_trace_hides_events_outside_current_class(
    admin_client: TestClient, db_session
) -> None:
    now = datetime.now().replace(microsecond=0)
    foreign = Consumable(
        name="外班耗材",
        spec="",
        unit="个",
        location="外班柜",
        safety_stock=0,
        current_stock=4,
        class_id=2,
        status="enabled",
        created_at=now,
        updated_at=now,
    )
    db_session.add(foreign)
    db_session.flush()
    foreign_event = inventory_ledger.append_event(
        db_session,
        consumable=foreign,
        event_type=inventory_ledger.EVENT_OPENING,
        delta=4,
        stock_before=0,
        stock_after=4,
        business_at=now,
        posted_at=now,
        source_type=inventory_ledger.SOURCE_OPENING,
    )
    db_session.commit()

    assert (
        admin_client.get(
            TRACE_URL.format(event_id=foreign_event.id)
        ).status_code
        == 404
    )
    assert (
        admin_client.get(TRACE_URL.format(event_id=999999)).status_code == 404
    )

    own = _create_consumable(admin_client, current_stock=5)
    opening = _event(db_session, own["id"], "opening")
    opening_data = _trace(admin_client, opening.id)
    assert opening_data["event"]["event_label"] == "期初建账"
    assert opening_data["timeline"] == []
    assert opening_data["timeline_total"] == 0

    _login_teacher(admin_client)
    assert (
        admin_client.get(TRACE_URL.format(event_id=opening.id)).status_code == 200
    )

    _login_student(admin_client)
    assert (
        admin_client.get(TRACE_URL.format(event_id=opening.id)).status_code == 403
    )


def test_trace_limit_boundary_and_read_only(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    _login_student(admin_client)
    receipt = _issue(admin_client, db_session, consumable["id"], 3)
    event_id = receipt["lines"][0]["event_id"]
    requisition_id = receipt["lines"][0]["requisition_id"]

    _login_admin(admin_client)
    returned = admin_client.post(
        f"{REQUISITIONS_URL}/{requisition_id}/returns",
        json={"quantity": 1, "reason": "未使用归还"},
    )
    assert returned.status_code == 201, returned.text

    limited = _trace(admin_client, event_id, limit=1)
    assert limited["limit"] == 1
    assert limited["timeline_total"] >= 3
    assert len(limited["timeline"]) == 1
    assert limited["truncated"] is True

    full = _trace(admin_client, event_id, limit=200)
    assert full["truncated"] is False
    assert len(full["timeline"]) == full["timeline_total"]

    url = TRACE_URL.format(event_id=event_id)
    assert admin_client.get(url, params={"limit": 0}).status_code == 422
    assert admin_client.get(url, params={"limit": 201}).status_code == 422

    tables = [
        InventoryEvent,
        Consumable,
        Requisition,
        RequisitionOrder,
        RequisitionReturn,
        RequisitionScrap,
        RestockOrder,
        RestockLine,
        RestockCorrection,
        InventoryCount,
        InventoryCountLine,
        RequisitionCorrection,
    ]
    db_session.expire_all()
    before_counts = {
        table.__tablename__: db_session.scalar(
            select(func.count()).select_from(table)
        )
        for table in tables
    }
    before_stock = db_session.get(Consumable, consumable["id"]).current_stock
    target = db_session.get(InventoryEvent, event_id)
    before_event = (
        target.delta,
        target.stock_before,
        target.stock_after,
        target.exec_seq,
        target.source_type,
        target.source_id,
        target.created_at,
    )

    _trace(admin_client, event_id, limit=200)
    _trace(admin_client, event_id, limit=200)

    db_session.expire_all()
    after_counts = {
        table.__tablename__: db_session.scalar(
            select(func.count()).select_from(table)
        )
        for table in tables
    }
    assert after_counts == before_counts
    assert db_session.get(Consumable, consumable["id"]).current_stock == before_stock
    target = db_session.get(InventoryEvent, event_id)
    assert (
        target.delta,
        target.stock_before,
        target.stock_after,
        target.exec_seq,
        target.source_type,
        target.source_id,
        target.created_at,
    ) == before_event