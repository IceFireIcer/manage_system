import csv
import io
from typing import Any

from app.models import Consumable, InventoryEvent, Requisition, User
from fastapi.testclient import TestClient
from sqlalchemy import select

BASE_URL = "/api/v1/requisitions"
EXPORT_URL = "/api/v1/requisitions/export"
OPTIONS_URL = "/api/v1/requisitions/consumable-options"
CONSUMABLES_URL = "/api/v1/consumables"
USERS_URL = "/api/v1/users"
LEDGER_URL = "/api/v1/inventory/ledger"
AUDIT_URL = "/api/v1/inventory/audit"


def _consumable_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "焊丝",
        "spec": "φ1.2mm",
        "unit": "个",
        "location": "一号柜",
        "safety_stock": 5,
        "current_stock": 100,
    }
    payload.update(overrides)
    return payload


def _create_consumable(client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = client.post(CONSUMABLES_URL, json=_consumable_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def _create_student(
    client: TestClient, account: str = "student02", name: str = "李同学"
) -> dict[str, Any]:
    response = client.post(
        USERS_URL,
        json={
            "account": account,
            "name": name,
            "password": "Student@123",
            "role": "student",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _login(client: TestClient, account: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"account": account, "password": password}
    )
    assert response.status_code == 200, response.text


def _submit(
    client: TestClient,
    consumable_id: int,
    quantity: int,
    user_id: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "items": [{"consumable_id": consumable_id, "quantity": quantity}]
    }
    if user_id is not None:
        payload["user_id"] = user_id
    response = client.post(BASE_URL, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _parse_csv(response) -> list[list[str]]:
    content = response.content.decode("utf-8")
    assert content.startswith("\ufeff")
    return list(csv.reader(io.StringIO(content[1:])))


def _user_id(db_session, account: str) -> int:
    return db_session.scalars(select(User).where(User.account == account)).one().id


def test_unit_change_blocked_with_balance_or_history(
    client: TestClient, db_session
) -> None:
    _login(client, "admin01", "Admin@123")
    consumable = _create_consumable(client, current_stock=10)
    response = client.put(
        f"{CONSUMABLES_URL}/{consumable['id']}", json={"unit": "盒"}
    )
    assert response.status_code == 400
    assert "不能直接修改计量单位" in response.json()["detail"]

    empty = _create_consumable(client, name="空档案", current_stock=0)
    response = client.put(f"{CONSUMABLES_URL}/{empty['id']}", json={"unit": "盒"})
    assert response.status_code == 200
    assert response.json()["unit"] == "盒"

    _login(client, "student01", "Student@123")
    _submit(client, consumable["id"], 10)
    _login(client, "admin01", "Admin@123")
    response = client.put(
        f"{CONSUMABLES_URL}/{consumable['id']}", json={"unit": "盒"}
    )
    assert response.status_code == 400
    assert "不能直接修改计量单位" in response.json()["detail"]


def test_history_uses_snapshot_after_profile_changes(
    client: TestClient, db_session
) -> None:
    _login(client, "admin01", "Admin@123")
    consumable = _create_consumable(client, name="焊丝", spec="φ1.2mm", unit="个")
    _login(client, "student01", "Student@123")
    _submit(client, consumable["id"], 10)

    _login(client, "admin01", "Admin@123")
    record = client.get(BASE_URL).json()["items"][0]

    stored = db_session.get(Consumable, consumable["id"])
    stored.name = "焊丝盒装"
    stored.spec = "1盒"
    stored.unit = "盒"
    db_session.commit()

    listed = client.get(BASE_URL).json()["items"][0]
    assert listed["id"] == record["id"]
    assert listed["quantity"] == 10
    assert listed["unit"] == "个"
    assert listed["consumable_name"] == "焊丝"
    assert listed["consumable_spec"] == "φ1.2mm"

    detail = client.get(f"{BASE_URL}/{record['id']}").json()
    assert detail["quantity"] == 10
    assert detail["unit"] == "个"
    assert detail["consumable_name"] == "焊丝"

    rows = _parse_csv(client.get(EXPORT_URL))
    assert rows[1][1] == "焊丝（φ1.2mm）"
    assert rows[1][2] == "10"

    ledger = client.get(LEDGER_URL, params={"consumable_id": consumable["id"]}).json()
    assert [event["unit_snapshot"] for event in ledger["events"]] == ["个", "个"]

    audit = client.get(AUDIT_URL, params={"consumable_id": consumable["id"]}).json()
    for basis in ("posting", "business"):
        assert {
            event["unit_snapshot"] for event in audit[basis]["events"]
        } == {"个"}


def test_student_can_query_own_disabled_consumable_history(
    client: TestClient, db_session
) -> None:
    _login(client, "admin01", "Admin@123")
    consumable = _create_consumable(client, name="口罩", spec="一次性", unit="个")
    _create_student(client)
    student2_id = _user_id(db_session, "student02")

    other = _submit(client, consumable["id"], 3, user_id=student2_id)

    student1_id = _user_id(db_session, "student01")
    _login(client, "student01", "Student@123")
    own = _submit(client, consumable["id"], 2)
    db_session.expire_all()
    own_record = db_session.scalars(
        select(Requisition).where(Requisition.user_id == student1_id)
    ).one()
    other_record = db_session.scalars(
        select(Requisition).where(Requisition.user_id == student2_id)
    ).one()
    assert other["count"] == 1 and own["count"] == 1

    _login(client, "admin01", "Admin@123")
    response = client.patch(
        f"{CONSUMABLES_URL}/{consumable['id']}/status",
        json={"status": "disabled"},
    )
    assert response.status_code == 200

    _login(client, "student01", "Student@123")
    data = client.get(BASE_URL, params={"consumable_id": consumable["id"]}).json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == own_record.id
    assert data["items"][0]["quantity"] == 2

    options = client.get(OPTIONS_URL).json()
    assert options == [
        {
            "id": consumable["id"],
            "name": "口罩",
            "spec": "一次性",
            "unit": "个",
            "status": "disabled",
        }
    ]

    other_detail = client.get(f"{BASE_URL}/{other_record.id}")
    assert other_detail.status_code == 404
    assert student2_id != _user_id(db_session, "student01")


def test_staff_can_filter_history_by_disabled_person(
    client: TestClient, db_session
) -> None:
    _login(client, "admin01", "Admin@123")
    consumable = _create_consumable(client, name="砂纸", spec="240目", unit="张")
    student = _create_student(client, account="student02", name="停用同学")
    _submit(client, consumable["id"], 1, user_id=student["id"])

    response = client.patch(
        f"{USERS_URL}/{student['id']}/status", json={"status": "disabled"}
    )
    assert response.status_code == 200

    _login(client, "teacher01", "Teacher@123")
    data = client.get(BASE_URL, params={"user_id": student["id"]}).json()
    assert data["total"] == 1
    assert data["items"][0]["user_name"] == "停用同学"
    assert data["items"][0]["unit"] == "张"

    options = client.get("/api/v1/users/history-options").json()
    matched = [item for item in options if item["id"] == student["id"]]
    assert matched == [
        {
            "id": student["id"],
            "account": "student02",
            "name": "停用同学",
            "role": "student",
            "status": "disabled",
        }
    ]


def test_history_records_store_snapshots_on_creation(
    client: TestClient, db_session
) -> None:
    _login(client, "admin01", "Admin@123")
    consumable = _create_consumable(client, name="手套", spec="棉纱", unit="副")
    _login(client, "student01", "Student@123")
    _submit(client, consumable["id"], 4)

    db_session.expire_all()
    record = db_session.scalars(select(Requisition)).all()[0]
    assert (record.name_snapshot, record.spec_snapshot, record.unit_snapshot) == (
        "手套",
        "棉纱",
        "副",
    )
    event = db_session.scalars(
        select(InventoryEvent).order_by(InventoryEvent.exec_seq)
    ).all()[-1]
    assert (event.name_snapshot, event.spec_snapshot, event.unit_snapshot) == (
        "手套",
        "棉纱",
        "副",
    )