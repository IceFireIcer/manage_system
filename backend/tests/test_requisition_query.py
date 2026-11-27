import csv
import io
from datetime import date, datetime, time, timedelta
from typing import Any

import pytest
from app.models import Requisition, User
from fastapi.testclient import TestClient
from sqlalchemy import select

BASE_URL = "/api/v1/requisitions"
EXPORT_URL = "/api/v1/requisitions/export"
CONSUMABLES_URL = "/api/v1/consumables"
USERS_URL = "/api/v1/users"

CSV_HEADER = ["时间", "耗材", "数量", "领用人", "领后剩余", "类型", "状态", "撤销原因", "备注"]


def _consumable_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "焊丝",
        "spec": "φ1.2mm",
        "unit": "卷",
        "location": "一号柜",
        "safety_stock": 5,
        "current_stock": 100,
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


def _user_id(db_session, account: str) -> int:
    return db_session.scalars(select(User).where(User.account == account)).one().id


def _insert_requisition(db_session, **kwargs: Any) -> Requisition:
    record = Requisition(
        consumable_id=kwargs["consumable_id"],
        user_id=kwargs["user_id"],
        quantity=kwargs.get("quantity", 1),
        stock_after=kwargs.get("stock_after", 99),
        type=kwargs.get("type", "normal"),
        status=kwargs.get("status", "active"),
        reversal_reason=kwargs.get("reversal_reason"),
        remark=kwargs.get("remark"),
        created_at=kwargs.get("created_at", datetime.now().replace(microsecond=0)),
    )
    db_session.add(record)
    db_session.commit()
    return record


def _parse_csv(response) -> list[list[str]]:
    content = response.content.decode("utf-8")
    assert content.startswith("\ufeff"), "CSV 缺少 UTF-8 BOM 前缀"
    return list(csv.reader(io.StringIO(content[1:])))


def test_list_requires_login(client: TestClient) -> None:
    response = client.get(BASE_URL)

    assert response.status_code == 401


def test_list_default_order_and_pagination(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    student_id = _user_id(db_session, "student01")
    base = datetime(2026, 1, 1, 8, 0, 0)
    for index in range(25):
        _insert_requisition(
            db_session,
            consumable_id=consumable["id"],
            user_id=student_id,
            stock_after=100 - index,
            created_at=base + timedelta(hours=index),
        )

    data = admin_client.get(BASE_URL).json()

    assert data["total"] == 25
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert len(data["items"]) == 20
    created = [item["created_at"] for item in data["items"]]
    assert created == sorted(created, reverse=True)
    assert data["items"][0]["created_at"] == (base + timedelta(hours=24)).isoformat()

    page2 = admin_client.get(BASE_URL, params={"page": 2}).json()
    assert len(page2["items"]) == 5
    assert page2["items"][-1]["created_at"] == base.isoformat()

    small = admin_client.get(BASE_URL, params={"page_size": 5}).json()
    assert len(small["items"]) == 5


def test_list_filter_by_consumable(admin_client: TestClient, db_session) -> None:
    c1 = _create_consumable(admin_client, name="焊丝")
    c2 = _create_consumable(admin_client, name="手套", spec="L码")
    student_id = _user_id(db_session, "student01")
    _insert_requisition(db_session, consumable_id=c1["id"], user_id=student_id)
    _insert_requisition(db_session, consumable_id=c2["id"], user_id=student_id)
    _insert_requisition(db_session, consumable_id=c1["id"], user_id=student_id)

    data = admin_client.get(BASE_URL, params={"consumable_id": c1["id"]}).json()

    assert data["total"] == 2
    assert all(item["consumable_name"] == "焊丝" for item in data["items"])


def test_list_filter_by_user(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    student2 = _create_student(admin_client)
    student1_id = _user_id(db_session, "student01")
    _insert_requisition(db_session, consumable_id=consumable["id"], user_id=student1_id)
    _insert_requisition(db_session, consumable_id=consumable["id"], user_id=student2["id"])

    _login(admin_client, "teacher01", "Teacher@123")
    data = admin_client.get(BASE_URL, params={"user_id": student2["id"]}).json()

    assert data["total"] == 1
    assert data["items"][0]["user_name"] == "李同学"


def test_list_filter_by_date_range(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    student_id = _user_id(db_session, "student01")
    today = date.today()
    today_start = datetime.combine(today, time.min)
    _insert_requisition(
        db_session, consumable_id=consumable["id"], user_id=student_id, created_at=today_start
    )
    _insert_requisition(
        db_session,
        consumable_id=consumable["id"],
        user_id=student_id,
        created_at=datetime.combine(today, time(23, 0)),
    )
    _insert_requisition(
        db_session,
        consumable_id=consumable["id"],
        user_id=student_id,
        created_at=today_start - timedelta(seconds=1),
    )
    yesterday = today - timedelta(days=1)
    _insert_requisition(
        db_session,
        consumable_id=consumable["id"],
        user_id=student_id,
        created_at=datetime.combine(yesterday, time(12, 0)),
    )

    data = admin_client.get(
        BASE_URL,
        params={"start_date": today.isoformat(), "end_date": today.isoformat()},
    ).json()

    assert data["total"] == 2

    data_all = admin_client.get(
        BASE_URL, params={"start_date": yesterday.isoformat()}
    ).json()
    assert data_all["total"] == 4


def test_list_filter_by_type_and_status(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    student_id = _user_id(db_session, "student01")
    _insert_requisition(db_session, consumable_id=consumable["id"], user_id=student_id)
    _insert_requisition(
        db_session,
        consumable_id=consumable["id"],
        user_id=student_id,
        type="reversal",
        status="reversed",
        reversal_reason="登错数量",
    )
    _insert_requisition(
        db_session, consumable_id=consumable["id"], user_id=student_id, status="reversed"
    )

    reversal = admin_client.get(BASE_URL, params={"type": "reversal"}).json()
    assert reversal["total"] == 1
    assert reversal["items"][0]["reversal_reason"] == "登错数量"

    reversed_only = admin_client.get(BASE_URL, params={"status": "reversed"}).json()
    assert reversed_only["total"] == 2

    active = admin_client.get(BASE_URL, params={"status": "active"}).json()
    assert active["total"] == 1


def test_list_filter_combination(admin_client: TestClient, db_session) -> None:
    c1 = _create_consumable(admin_client, name="焊丝")
    c2 = _create_consumable(admin_client, name="手套")
    student2 = _create_student(admin_client)
    student1_id = _user_id(db_session, "student01")
    today = date.today()
    yesterday = today - timedelta(days=1)
    for day in (today, today):
        _insert_requisition(
            db_session,
            consumable_id=c1["id"],
            user_id=student1_id,
            created_at=datetime.combine(day, time(10, 0)),
        )
    _insert_requisition(
        db_session,
        consumable_id=c1["id"],
        user_id=student1_id,
        created_at=datetime.combine(yesterday, time(10, 0)),
    )
    _insert_requisition(
        db_session,
        consumable_id=c2["id"],
        user_id=student1_id,
        created_at=datetime.combine(today, time(10, 0)),
    )
    _insert_requisition(
        db_session,
        consumable_id=c1["id"],
        user_id=student2["id"],
        created_at=datetime.combine(today, time(10, 0)),
    )
    _insert_requisition(
        db_session,
        consumable_id=c1["id"],
        user_id=student1_id,
        status="reversed",
        created_at=datetime.combine(today, time(11, 0)),
    )

    _login(admin_client, "teacher01", "Teacher@123")
    data = admin_client.get(
        BASE_URL,
        params={
            "consumable_id": c1["id"],
            "user_id": student1_id,
            "start_date": today.isoformat(),
            "end_date": today.isoformat(),
            "status": "active",
        },
    ).json()

    assert data["total"] == 2
    assert all(item["consumable_name"] == "焊丝" for item in data["items"])
    assert all(item["user_name"] == "张同学" for item in data["items"])


def test_list_student_sees_only_own_records(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    student2 = _create_student(admin_client)
    student1_id = _user_id(db_session, "student01")
    _insert_requisition(db_session, consumable_id=consumable["id"], user_id=student1_id)
    _insert_requisition(db_session, consumable_id=consumable["id"], user_id=student2["id"])

    _login(admin_client, "student01", "Student@123")
    data = admin_client.get(BASE_URL).json()

    assert data["total"] == 1
    assert data["items"][0]["user_name"] == "张同学"


def test_list_student_user_id_param_forced_self(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    student2 = _create_student(admin_client)
    student1_id = _user_id(db_session, "student01")
    _insert_requisition(db_session, consumable_id=consumable["id"], user_id=student1_id)
    _insert_requisition(db_session, consumable_id=consumable["id"], user_id=student2["id"])

    _login(admin_client, "student01", "Student@123")
    data = admin_client.get(BASE_URL, params={"user_id": student2["id"]}).json()

    assert data["total"] == 1
    assert data["items"][0]["user_name"] == "张同学"


def test_list_item_fields(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    student_id = _user_id(db_session, "student01")
    record = _insert_requisition(
        db_session,
        consumable_id=consumable["id"],
        user_id=student_id,
        quantity=3,
        stock_after=7,
        remark="实训课领用",
    )

    data = admin_client.get(BASE_URL).json()

    item = data["items"][0]
    assert item == {
        "id": record.id,
        "created_at": record.created_at.isoformat(),
        "consumable_name": "焊丝",
        "consumable_spec": "φ1.2mm",
        "quantity": 3,
        "user_id": student_id,
        "user_name": "张同学",
        "stock_after": 7,
        "type": "normal",
        "status": "active",
        "reversal_of": None,
        "reversal_reason": None,
        "remark": "实训课领用",
        "related": None,
    }


def test_list_empty_result(admin_client: TestClient) -> None:
    data = admin_client.get(BASE_URL).json()

    assert data == {"items": [], "total": 0, "page": 1, "page_size": 20}


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page_size": 0},
        {"page_size": 101},
        {"type": "bad"},
        {"status": "bad"},
        {"start_date": "2026/11/01"},
    ],
)
def test_list_invalid_params(admin_client: TestClient, params: dict) -> None:
    response = admin_client.get(BASE_URL, params=params)

    assert response.status_code == 422


def test_export_requires_login(client: TestClient) -> None:
    response = client.get(EXPORT_URL)

    assert response.status_code == 401


def test_export_student_forbidden(client: TestClient) -> None:
    _login(client, "student01", "Student@123")

    response = client.get(EXPORT_URL)

    assert response.status_code == 403


def test_export_teacher_ok(client: TestClient) -> None:
    _login(client, "teacher01", "Teacher@123")

    response = client.get(EXPORT_URL)

    assert response.status_code == 200


def test_export_csv_content_and_headers(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    student_id = _user_id(db_session, "student01")
    _insert_requisition(
        db_session,
        consumable_id=consumable["id"],
        user_id=student_id,
        quantity=3,
        stock_after=7,
        created_at=datetime(2026, 11, 27, 10, 0, 0),
        remark="实训课领用",
    )
    _insert_requisition(
        db_session,
        consumable_id=consumable["id"],
        user_id=student_id,
        quantity=1,
        stock_after=8,
        type="reversal",
        status="reversed",
        reversal_reason="登错数量",
        created_at=datetime(2026, 11, 27, 11, 0, 0),
    )

    response = admin_client.get(EXPORT_URL)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    rows = _parse_csv(response)
    assert rows[0] == CSV_HEADER
    assert len(rows) == 3
    assert rows[1][0] == "2026-11-27 11:00:00"
    assert rows[1][1] == "焊丝（φ1.2mm）"
    assert rows[1][2] == "1"
    assert rows[1][3] == "张同学"
    assert rows[1][4] == "8"
    assert rows[1][5] == "红冲"
    assert rows[1][6] == "已撤销"
    assert rows[1][7] == "登错数量"
    assert rows[1][8] == ""
    assert rows[2][0] == "2026-11-27 10:00:00"
    assert rows[2][2] == "3"
    assert rows[2][4] == "7"
    assert rows[2][5] == "领用"
    assert rows[2][6] == "有效"
    assert rows[2][8] == "实训课领用"


def test_export_respects_filters(admin_client: TestClient, db_session) -> None:
    c1 = _create_consumable(admin_client, name="焊丝")
    c2 = _create_consumable(admin_client, name="手套")
    student_id = _user_id(db_session, "student01")
    _insert_requisition(db_session, consumable_id=c1["id"], user_id=student_id)
    _insert_requisition(db_session, consumable_id=c1["id"], user_id=student_id)
    _insert_requisition(db_session, consumable_id=c2["id"], user_id=student_id)

    rows = _parse_csv(admin_client.get(EXPORT_URL, params={"consumable_id": c1["id"]}))

    assert len(rows) == 3
    assert all(row[1].startswith("焊丝") for row in rows[1:])


def test_export_empty(admin_client: TestClient) -> None:
    rows = _parse_csv(admin_client.get(EXPORT_URL))

    assert rows == [CSV_HEADER]
