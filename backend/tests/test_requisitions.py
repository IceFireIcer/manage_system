from datetime import datetime, timedelta
from typing import Any

import pytest
from app.models import Consumable, OperationLog, Requisition, User
from fastapi.testclient import TestClient
from sqlalchemy import select

BASE_URL = "/api/v1/requisitions"
CONSUMABLES_URL = "/api/v1/consumables"
USERS_URL = "/api/v1/users"
OPTIONS_URL = "/api/v1/users/options"


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


def _create_student(admin_client: TestClient, account: str = "student02") -> dict[str, Any]:
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


def _login_student(client: TestClient) -> None:
    _login(client, "student01", "Student@123")


def _login_teacher(client: TestClient) -> None:
    _login(client, "teacher01", "Teacher@123")


def _get_stock(client: TestClient, consumable_id: int) -> int:
    data = client.get(CONSUMABLES_URL).json()
    return next(item["current_stock"] for item in data if item["id"] == consumable_id)


def _requisitions(db_session) -> list[Requisition]:
    return list(db_session.scalars(select(Requisition).order_by(Requisition.id)).all())


def _user_id(db_session, account: str) -> int:
    return db_session.scalars(select(User).where(User.account == account)).one().id


def _db_stock(db_session, consumable_id: int) -> int:
    db_session.expire_all()
    return db_session.get(Consumable, consumable_id).current_stock


def _submit(client: TestClient, items: list[dict], **extra: Any):
    payload: dict[str, Any] = {"items": items}
    payload.update(extra)
    return client.post(BASE_URL, json=payload)


def test_requires_login(client: TestClient) -> None:
    response = client.post(BASE_URL, json={"items": [{"consumable_id": 1, "quantity": 1}]})

    assert response.status_code == 401
    assert response.json()["detail"] == "未登录"


def test_single_item_success(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    _login_student(admin_client)

    response = _submit(
        admin_client, [{"consumable_id": consumable["id"], "quantity": 3}], remark="实训课领用"
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["count"] == 1
    assert data["items"] == [
        {"consumable_name": "焊丝", "quantity": 3, "stock_after": 7}
    ]
    assert _get_stock(admin_client, consumable["id"]) == 7

    records = _requisitions(db_session)
    assert len(records) == 1
    record = records[0]
    assert record.consumable_id == consumable["id"]
    assert record.user_id == _user_id(db_session, "student01")
    assert record.quantity == 3
    assert record.stock_after == 7
    assert record.type == "normal"
    assert record.status == "active"
    assert record.reversal_of is None
    assert record.class_id == consumable["class_id"]
    assert record.remark == "实训课领用"


def test_three_items_success(admin_client: TestClient, db_session) -> None:
    first = _create_consumable(admin_client, name="焊丝", current_stock=10)
    second = _create_consumable(admin_client, name="砂纸", spec="240目", unit="张", current_stock=8)
    third = _create_consumable(admin_client, name="手套", spec="棉纱", unit="副", current_stock=6)
    _login_student(admin_client)

    response = _submit(
        admin_client,
        [
            {"consumable_id": first["id"], "quantity": 2},
            {"consumable_id": second["id"], "quantity": 3},
            {"consumable_id": third["id"], "quantity": 4},
        ],
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["count"] == 3
    assert [item["stock_after"] for item in data["items"]] == [8, 5, 2]
    assert _get_stock(admin_client, first["id"]) == 8
    assert _get_stock(admin_client, second["id"]) == 5
    assert _get_stock(admin_client, third["id"]) == 2
    assert len(_requisitions(db_session)) == 3


def test_any_row_insufficient_rejects_whole_order(admin_client: TestClient, db_session) -> None:
    first = _create_consumable(admin_client, name="焊丝", current_stock=5)
    second = _create_consumable(admin_client, name="砂纸", unit="张", current_stock=2)
    _login_student(admin_client)

    response = _submit(
        admin_client,
        [
            {"consumable_id": first["id"], "quantity": 3},
            {"consumable_id": second["id"], "quantity": 5},
        ],
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "第 2 行" in detail
    assert "库存不足" in detail
    assert "砂纸" in detail
    assert "仅剩 2" in detail
    assert _get_stock(admin_client, first["id"]) == 5
    assert _db_stock(db_session, second["id"]) == 2
    assert _requisitions(db_session) == []


def test_disabled_consumable_rejected(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    response = admin_client.patch(
        f"{CONSUMABLES_URL}/{consumable['id']}/status", json={"status": "disabled"}
    )
    assert response.status_code == 200
    _login_student(admin_client)

    response = _submit(admin_client, [{"consumable_id": consumable["id"], "quantity": 1}])

    assert response.status_code == 400
    assert "停用" in response.json()["detail"]
    assert "第 1 行" in response.json()["detail"]
    assert _db_stock(db_session, consumable["id"]) == 10
    assert _requisitions(db_session) == []


def test_unknown_consumable_rejected(admin_client: TestClient, db_session) -> None:
    _create_consumable(admin_client)
    _login_student(admin_client)

    response = _submit(admin_client, [{"consumable_id": 999, "quantity": 1}])

    assert response.status_code == 400
    assert "第 1 行" in response.json()["detail"]
    assert "不存在" in response.json()["detail"]
    assert _requisitions(db_session) == []


def test_more_than_five_rows_rejected(admin_client: TestClient) -> None:
    consumable = _create_consumable(admin_client)
    _login_student(admin_client)

    rows = [{"consumable_id": consumable["id"], "quantity": 1} for _ in range(6)]
    response = _submit(admin_client, rows)

    assert response.status_code == 422


def test_empty_items_rejected(admin_client: TestClient) -> None:
    _create_consumable(admin_client)
    _login_student(admin_client)

    response = _submit(admin_client, [])

    assert response.status_code == 422


@pytest.mark.parametrize("quantity", [0, -1])
def test_non_positive_quantity_rejected(
    admin_client: TestClient, quantity: int
) -> None:
    consumable = _create_consumable(admin_client)
    _login_student(admin_client)

    response = _submit(admin_client, [{"consumable_id": consumable["id"], "quantity": quantity}])

    assert response.status_code == 422


def test_student_cannot_register_for_others(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    admin_id = _user_id(db_session, "admin01")
    _login_student(admin_client)

    response = _submit(
        admin_client,
        [{"consumable_id": consumable["id"], "quantity": 1}],
        user_id=admin_id,
    )

    assert response.status_code == 403
    assert "本人" in response.json()["detail"]
    assert _get_stock(admin_client, consumable["id"]) == 10
    assert _requisitions(db_session) == []


def test_teacher_can_proxy_register(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    student = _create_student(admin_client)
    _login_teacher(admin_client)

    response = _submit(
        admin_client,
        [{"consumable_id": consumable["id"], "quantity": 2}],
        user_id=student["id"],
    )

    assert response.status_code == 201, response.text
    records = _requisitions(db_session)
    assert len(records) == 1
    assert records[0].user_id == student["id"]


def test_admin_can_proxy_register(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    student = _create_student(admin_client)

    response = _submit(
        admin_client,
        [{"consumable_id": consumable["id"], "quantity": 2}],
        user_id=student["id"],
    )

    assert response.status_code == 201, response.text
    records = _requisitions(db_session)
    assert records[0].user_id == student["id"]


def test_proxy_for_unknown_user_rejected(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    _login_teacher(admin_client)

    response = _submit(
        admin_client,
        [{"consumable_id": consumable["id"], "quantity": 1}],
        user_id=999,
    )

    assert response.status_code == 400
    assert "领用人不存在" in response.json()["detail"]
    assert _get_stock(admin_client, consumable["id"]) == 10
    assert _requisitions(db_session) == []


def test_proxy_for_disabled_user_rejected(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    disabled_id = _user_id(db_session, "disabled01")
    _login_teacher(admin_client)

    response = _submit(
        admin_client,
        [{"consumable_id": consumable["id"], "quantity": 1}],
        user_id=disabled_id,
    )

    assert response.status_code == 400
    assert "停用" in response.json()["detail"]
    assert _requisitions(db_session) == []


def test_created_at_in_future_rejected(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    _login_student(admin_client)

    future = (datetime.now() + timedelta(minutes=1)).isoformat()
    response = _submit(
        admin_client, [{"consumable_id": consumable["id"], "quantity": 1}], created_at=future
    )

    assert response.status_code == 400
    assert "晚于当前时间" in response.json()["detail"]
    assert _requisitions(db_session) == []


def test_created_at_31_days_ago_rejected(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    _login_student(admin_client)

    old = (datetime.now() - timedelta(days=31)).isoformat()
    response = _submit(
        admin_client, [{"consumable_id": consumable["id"], "quantity": 1}], created_at=old
    )

    assert response.status_code == 400
    assert "30" in response.json()["detail"]
    assert _requisitions(db_session) == []


def test_created_at_29_days_ago_accepted(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    _login_student(admin_client)

    backfill_time = datetime.now() - timedelta(days=29)
    response = _submit(
        admin_client,
        [{"consumable_id": consumable["id"], "quantity": 1}],
        created_at=backfill_time.isoformat(),
    )

    assert response.status_code == 201, response.text
    records = _requisitions(db_session)
    assert len(records) == 1
    assert records[0].created_at.replace(microsecond=0) == backfill_time.replace(microsecond=0)


def test_no_oversell_sequential_submissions(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client, current_stock=1)
    _login_student(admin_client)

    first = _submit(admin_client, [{"consumable_id": consumable["id"], "quantity": 1}])
    second = _submit(admin_client, [{"consumable_id": consumable["id"], "quantity": 1}])

    assert first.status_code == 201
    assert second.status_code == 400
    assert "库存不足" in second.json()["detail"]
    assert _db_stock(db_session, consumable["id"]) == 0
    records = _requisitions(db_session)
    assert len(records) == 1
    assert records[0].stock_after == 0


def test_same_consumable_two_rows_accumulate(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client, current_stock=5)
    _login_student(admin_client)

    response = _submit(
        admin_client,
        [
            {"consumable_id": consumable["id"], "quantity": 1},
            {"consumable_id": consumable["id"], "quantity": 1},
        ],
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["count"] == 2
    assert [item["stock_after"] for item in data["items"]] == [4, 3]
    assert _get_stock(admin_client, consumable["id"]) == 3
    records = _requisitions(db_session)
    assert [record.stock_after for record in records] == [4, 3]


def test_same_consumable_rows_exhaust_stock(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client, current_stock=5)
    _login_student(admin_client)

    response = _submit(
        admin_client,
        [
            {"consumable_id": consumable["id"], "quantity": 2},
            {"consumable_id": consumable["id"], "quantity": 3},
        ],
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert [item["stock_after"] for item in data["items"]] == [3, 0]
    assert _db_stock(db_session, consumable["id"]) == 0


def test_requisition_written_to_logs(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    student = _create_student(admin_client)
    _login_teacher(admin_client)

    response = _submit(
        admin_client,
        [{"consumable_id": consumable["id"], "quantity": 2}],
        user_id=student["id"],
    )
    assert response.status_code == 201

    logs = db_session.scalars(
        select(OperationLog).where(OperationLog.action == "requisition_create")
    ).all()
    assert len(logs) == 1
    log = logs[0]
    assert log.operator_id == _user_id(db_session, "teacher01")
    assert log.role == "teacher"
    assert "2" in log.after_value
    assert consumable["name"] in log.after_value

    created = db_session.scalars(select(Requisition).order_by(Requisition.id)).first()
    assert created is not None
    assert log.target_id == created.id


def test_user_options_forbidden_for_student(student_client: TestClient) -> None:
    response = student_client.get(OPTIONS_URL)

    assert response.status_code == 403


def test_user_options_returns_enabled_users_without_sensitive_fields(
    teacher_client: TestClient, db_session
) -> None:
    response = teacher_client.get(OPTIONS_URL)

    assert response.status_code == 200
    options = response.json()
    assert options, "应至少返回种子启用用户"
    for option in options:
        assert set(option.keys()) == {"id", "name", "role"}
    disabled_id = _user_id(db_session, "disabled01")
    assert all(option["id"] != disabled_id for option in options)
    assert {option["role"] for option in options} <= {"teacher", "admin", "student"}
