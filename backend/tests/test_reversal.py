from typing import Any

import pytest
from app.models import Consumable, OperationLog, Requisition, User
from fastapi.testclient import TestClient
from sqlalchemy import select

BASE_URL = "/api/v1/requisitions"
CONSUMABLES_URL = "/api/v1/consumables"
USERS_URL = "/api/v1/users"


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


def _create_consumable(admin_client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = admin_client.post(CONSUMABLES_URL, json=_consumable_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def _create_student(admin_client: TestClient, account: str) -> dict[str, Any]:
    response = admin_client.post(
        USERS_URL,
        json={
            "account": account,
            "name": "李同学",
            "password": "Student@123",
            "role": "student",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _login(client: TestClient, account: str, password: str) -> None:
    response = client.post("/api/v1/auth/login", json={"account": account, "password": password})
    assert response.status_code == 200, response.text


def _login_teacher(client: TestClient) -> None:
    _login(client, "teacher01", "Teacher@123")


def _submit_one(client: TestClient, consumable_id: int, quantity: int = 3, **extra: Any):
    payload: dict[str, Any] = {"items": [{"consumable_id": consumable_id, "quantity": quantity}]}
    payload.update(extra)
    return client.post(BASE_URL, json=payload)


def _reverse(client: TestClient, requisition_id: int, reason: str = "登记错误", **extra: Any):
    payload: dict[str, Any] = {"reason": reason}
    payload.update(extra)
    return client.post(f"{BASE_URL}/{requisition_id}/reversal", json=payload)


def _records(db_session) -> list[Requisition]:
    return list(db_session.scalars(select(Requisition).order_by(Requisition.id)).all())


def _first_id(db_session) -> int:
    return db_session.scalars(select(Requisition).order_by(Requisition.id)).first().id


def _db_stock(db_session, consumable_id: int) -> int:
    db_session.expire_all()
    return db_session.get(Consumable, consumable_id).current_stock


def _user_id(db_session, account: str) -> int:
    return db_session.scalars(select(User).where(User.account == account)).one().id


def test_teacher_reverse_restores_stock_and_creates_reversal(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client)
    assert _submit_one(admin_client, consumable["id"], quantity=3).status_code == 201
    requisition_id = _first_id(db_session)

    _login_teacher(admin_client)
    response = _reverse(admin_client, requisition_id, reason="登记错误，回退")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["reversal_of"] == requisition_id
    assert data["quantity"] == 3
    assert data["stock_after"] == 10
    assert data["consumable_name"] == "焊丝"
    assert data["reversal_reason"] == "登记错误，回退"

    assert _db_stock(db_session, consumable["id"]) == 10

    records = _records(db_session)
    assert len(records) == 2
    original, reversal = records
    assert original.status == "reversed"
    assert reversal.type == "reversal"
    assert reversal.status == "active"
    assert reversal.reversal_of == original.id
    assert reversal.reversal_reason == "登记错误，回退"
    assert reversal.quantity == 3
    assert reversal.stock_after == 10
    assert reversal.consumable_id == original.consumable_id
    assert reversal.user_id == original.user_id
    assert reversal.class_id == original.class_id


def test_admin_reverse_success(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client, current_stock=6)
    assert _submit_one(admin_client, consumable["id"], quantity=2).status_code == 201
    requisition_id = _first_id(db_session)

    response = _reverse(admin_client, requisition_id, reason="多登了一行")

    assert response.status_code == 200, response.text
    assert _db_stock(db_session, consumable["id"]) == 6
    assert _records(db_session)[0].status == "reversed"


def test_reverse_with_optional_remark(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    assert _submit_one(admin_client, consumable["id"], quantity=1).status_code == 201
    requisition_id = _first_id(db_session)

    response = _reverse(admin_client, requisition_id, reason="笔误", remark="班长确认")

    assert response.status_code == 200, response.text
    records = _records(db_session)
    assert records[1].remark == "班长确认"


def test_student_cannot_reverse(client: TestClient, db_session) -> None:
    _login(client, "admin01", "Admin@123")
    consumable = _create_consumable(client)
    _login(client, "student01", "Student@123")
    assert _submit_one(client, consumable["id"], quantity=1).status_code == 201
    requisition_id = _first_id(db_session)

    response = _reverse(client, requisition_id, reason="想撤销")

    assert response.status_code == 403
    assert _db_stock(db_session, consumable["id"]) == 9
    assert _records(db_session)[0].status == "active"
    assert len(_records(db_session)) == 1


@pytest.mark.parametrize("reason", ["", "   "])
def test_blank_reason_rejected(
    admin_client: TestClient, db_session, reason: str
) -> None:
    consumable = _create_consumable(admin_client)
    assert _submit_one(admin_client, consumable["id"], quantity=1).status_code == 201
    requisition_id = _first_id(db_session)

    response = _reverse(admin_client, requisition_id, reason=reason)

    assert response.status_code == 422
    assert _db_stock(db_session, consumable["id"]) == 9
    assert _records(db_session)[0].status == "active"


def test_missing_reason_rejected(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    assert _submit_one(admin_client, consumable["id"], quantity=1).status_code == 201
    requisition_id = _first_id(db_session)

    response = admin_client.post(f"{BASE_URL}/{requisition_id}/reversal", json={})

    assert response.status_code == 422
    assert _db_stock(db_session, consumable["id"]) == 9


def test_reverse_twice_rejected(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    assert _submit_one(admin_client, consumable["id"], quantity=1).status_code == 201
    requisition_id = _first_id(db_session)
    assert _reverse(admin_client, requisition_id, reason="第一次撤销").status_code == 200

    response = _reverse(admin_client, requisition_id, reason="再撤一次")

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "已撤销" in detail
    assert "不可重复撤销" in detail
    assert _db_stock(db_session, consumable["id"]) == 10
    assert len(_records(db_session)) == 2


def test_reverse_reversal_record_rejected(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    assert _submit_one(admin_client, consumable["id"], quantity=1).status_code == 201
    original_id = _first_id(db_session)
    assert _reverse(admin_client, original_id, reason="撤销").status_code == 200
    reversal_id = _records(db_session)[1].id

    response = _reverse(admin_client, reversal_id, reason="撤销红冲")

    assert response.status_code == 400
    assert "红冲流水不可被撤销" in response.json()["detail"]
    assert _db_stock(db_session, consumable["id"]) == 10
    assert len(_records(db_session)) == 2


def test_reverse_unknown_requisition(admin_client: TestClient) -> None:
    response = _reverse(admin_client, 999, reason="任意")

    assert response.status_code == 404
    assert "流水不存在" in response.json()["detail"]


def test_reverse_disabled_consumable_succeeds(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client)
    assert _submit_one(admin_client, consumable["id"], quantity=4).status_code == 201
    requisition_id = _first_id(db_session)
    response = admin_client.patch(
        f"{CONSUMABLES_URL}/{consumable['id']}/status", json={"status": "disabled"}
    )
    assert response.status_code == 200

    response = _reverse(admin_client, requisition_id, reason="停用后撤销")

    assert response.status_code == 200, response.text
    assert _db_stock(db_session, consumable["id"]) == 10
    assert _records(db_session)[0].status == "reversed"


def test_concurrent_reverse_applies_once(
    admin_client: TestClient, db_session
) -> None:
    from app.routers.requisitions import reverse_requisition
    from app.schemas_requisitions import ReversalCreate
    from fastapi import HTTPException

    consumable = _create_consumable(admin_client)
    assert _submit_one(admin_client, consumable["id"], quantity=3).status_code == 201
    requisition_id = _first_id(db_session)

    stale = db_session.get(Requisition, requisition_id)
    assert stale.status == "active"
    db_session.commit()

    assert _reverse(admin_client, requisition_id, reason="第一次撤销").status_code == 200

    manager = db_session.get(User, _user_id(db_session, "teacher01"))
    with pytest.raises(HTTPException) as exc_info:
        reverse_requisition(
            requisition_id,
            ReversalCreate(reason="并发撤销"),
            db_session,
            manager,
        )

    assert exc_info.value.status_code == 400
    db_session.rollback()
    db_session.expire_all()
    assert _db_stock(db_session, consumable["id"]) == 10
    assert len(_records(db_session)) == 2


def test_detail_bidirectional_link(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    assert _submit_one(admin_client, consumable["id"], quantity=3).status_code == 201
    original_id = _first_id(db_session)
    _login_teacher(admin_client)
    assert _reverse(admin_client, original_id, reason="登记错误").status_code == 200
    reversal_id = _records(db_session)[1].id

    original_detail = admin_client.get(f"{BASE_URL}/{original_id}")
    assert original_detail.status_code == 200
    data = original_detail.json()
    assert data["status"] == "reversed"
    assert data["reversal"]["id"] == reversal_id
    assert data["reversal"]["type"] == "reversal"
    assert data["reversal"]["quantity"] == 3

    reversal_detail = admin_client.get(f"{BASE_URL}/{reversal_id}")
    assert reversal_detail.status_code == 200
    data = reversal_detail.json()
    assert data["type"] == "reversal"
    assert data["origin"]["id"] == original_id
    assert data["origin"]["status"] == "reversed"
    assert data["reversal_of"] == original_id
    assert data["reversal_reason"] == "登记错误"


def test_student_cannot_view_others_detail(client: TestClient, db_session) -> None:
    _login(client, "admin01", "Admin@123")
    consumable = _create_consumable(client)
    assert _submit_one(client, consumable["id"], quantity=1).status_code == 201
    requisition_id = _first_id(db_session)

    _login(client, "student01", "Student@123")
    response = client.get(f"{BASE_URL}/{requisition_id}")

    assert response.status_code == 404
    assert "流水不存在" in response.json()["detail"]


def test_student_views_own_detail(client: TestClient, db_session) -> None:
    _login(client, "admin01", "Admin@123")
    consumable = _create_consumable(client)
    _login(client, "student01", "Student@123")
    assert _submit_one(client, consumable["id"], quantity=1).status_code == 201
    requisition_id = _first_id(db_session)

    response = client.get(f"{BASE_URL}/{requisition_id}")

    assert response.status_code == 200
    assert response.json()["id"] == requisition_id


def test_detail_not_found(admin_client: TestClient) -> None:
    response = admin_client.get(f"{BASE_URL}/999")

    assert response.status_code == 404
    assert "流水不存在" in response.json()["detail"]


def test_list_requires_login(client: TestClient) -> None:
    response = client.get(BASE_URL)

    assert response.status_code == 401


def test_list_only_own_records_for_student(client: TestClient, db_session) -> None:
    _login(client, "admin01", "Admin@123")
    consumable = _create_consumable(client)
    _create_student(client, "student02")
    _login(client, "student01", "Student@123")
    assert _submit_one(client, consumable["id"], quantity=1).status_code == 201
    _login(client, "student02", "Student@123")
    changed = client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "Student@123", "new_password": "Student@4567"},
    )
    assert changed.status_code == 200, changed.text
    assert _submit_one(client, consumable["id"], quantity=2).status_code == 201

    _login(client, "student01", "Student@123")
    response = client.get(BASE_URL)

    assert response.status_code == 200
    data = response.json()
    student1_id = _user_id(db_session, "student01")
    assert data["total"] == 1
    assert [item["user_id"] for item in data["items"]] == [student1_id]
    assert data["items"][0]["user_name"] == "张同学"
    assert data["items"][0]["consumable_name"] == "焊丝"


def test_list_all_records_with_filters(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    assert _submit_one(admin_client, consumable["id"], quantity=1).status_code == 201
    first_id = _first_id(db_session)
    assert _reverse(admin_client, first_id, reason="撤销").status_code == 200
    assert _submit_one(admin_client, consumable["id"], quantity=2).status_code == 201

    data = admin_client.get(BASE_URL).json()
    assert data["total"] == 3
    ids = [item["id"] for item in data["items"]]
    assert ids == sorted(ids, reverse=True)

    reversal_only = admin_client.get(BASE_URL, params={"type": "reversal"}).json()
    assert reversal_only["total"] == 1
    assert reversal_only["items"][0]["type"] == "reversal"
    assert reversal_only["items"][0]["reversal_reason"] == "撤销"
    assert reversal_only["items"][0]["reversal_of"] == first_id

    normal_only = admin_client.get(BASE_URL, params={"type": "normal"}).json()
    assert normal_only["total"] == 2
    assert all(item["type"] == "normal" for item in normal_only["items"])

    reversed_only = admin_client.get(BASE_URL, params={"status": "reversed"}).json()
    assert reversed_only["total"] == 1
    assert reversed_only["items"][0]["status"] == "reversed"

    active_only = admin_client.get(BASE_URL, params={"status": "active"}).json()
    assert active_only["total"] == 2

    combo = admin_client.get(
        BASE_URL, params={"type": "normal", "status": "active"}
    ).json()
    assert combo["total"] == 1
    assert combo["items"][0]["id"] != first_id


def test_list_invalid_filter_rejected(admin_client: TestClient) -> None:
    assert admin_client.get(BASE_URL, params={"type": "xxx"}).status_code == 422
    assert admin_client.get(BASE_URL, params={"status": "xxx"}).status_code == 422


def test_list_returns_relation_for_reversal_and_origin(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client)
    assert _submit_one(admin_client, consumable["id"], quantity=2).status_code == 201
    original_id = _first_id(db_session)
    assert _reverse(admin_client, original_id, reason="关联展示").status_code == 200
    reversal_id = _records(db_session)[1].id

    items = admin_client.get(BASE_URL).json()["items"]
    by_type = {item["type"]: item for item in items}

    assert by_type["reversal"]["related"]["id"] == original_id
    assert by_type["reversal"]["related"]["created_at"]
    assert by_type["reversal"]["related"]["quantity"] == 2
    assert by_type["normal"]["related"]["id"] == reversal_id
    assert by_type["normal"]["related"]["type"] == "reversal"


def test_list_related_is_none_without_reversal(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client)
    assert _submit_one(admin_client, consumable["id"], quantity=1).status_code == 201

    item = admin_client.get(BASE_URL).json()["items"][0]

    assert item["related"] is None


def test_list_pagination_desc_by_id(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client, current_stock=100)
    for _ in range(5):
        assert _submit_one(admin_client, consumable["id"], quantity=1).status_code == 201

    page1 = admin_client.get(BASE_URL, params={"page": 1, "page_size": 2}).json()
    assert page1["total"] == 5
    assert len(page1["items"]) == 2
    page3 = admin_client.get(BASE_URL, params={"page": 3, "page_size": 2}).json()
    assert len(page3["items"]) == 1
    assert page1["items"][0]["id"] > page3["items"][0]["id"]


def test_reverse_written_to_logs(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    assert _submit_one(admin_client, consumable["id"], quantity=3).status_code == 201
    requisition_id = _first_id(db_session)
    _login_teacher(admin_client)
    assert _reverse(admin_client, requisition_id, reason="登记错误").status_code == 200

    logs = db_session.scalars(
        select(OperationLog).where(OperationLog.action == "requisition_reverse")
    ).all()
    assert len(logs) == 1
    log = logs[0]
    assert log.operator_id == _user_id(db_session, "teacher01")
    assert log.role == "teacher"
    assert log.target_id == requisition_id
    assert str(requisition_id) in log.before_value
    assert str(requisition_id) in log.after_value
    assert "10" in log.after_value
    assert "7" in log.after_value
    assert "登记错误" in log.after_value
