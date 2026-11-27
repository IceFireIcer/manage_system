from typing import Any

import pytest
from app.models import OperationLog, User
from fastapi.testclient import TestClient
from sqlalchemy import select

LIST_URL = "/api/v1/consumables"


def _payload(**overrides: Any) -> dict[str, Any]:
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
    response = client.post(LIST_URL, json=_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def _admin_id(db_session) -> int:
    return db_session.scalars(select(User).where(User.account == "admin01")).one().id


def test_list_requires_login(client: TestClient) -> None:
    response = client.get(LIST_URL)

    assert response.status_code == 401
    assert response.json()["detail"] == "未登录"


def test_teacher_can_list(teacher_client: TestClient) -> None:
    response = teacher_client.get(LIST_URL)

    assert response.status_code == 200
    assert response.json() == []


def test_admin_can_list(admin_client: TestClient) -> None:
    response = admin_client.get(LIST_URL)

    assert response.status_code == 200
    assert response.json() == []


def test_student_can_list(student_client: TestClient) -> None:
    response = student_client.get(LIST_URL)

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize("fixture_name", ["teacher_client", "student_client"])
def test_non_admin_create_forbidden(fixture_name: str, request: pytest.FixtureRequest) -> None:
    client = request.getfixturevalue(fixture_name)

    response = client.post(LIST_URL, json=_payload())

    assert response.status_code == 403
    assert response.json()["detail"] == "无权限访问"


@pytest.mark.parametrize("fixture_name", ["teacher_client", "student_client"])
def test_non_admin_update_forbidden(fixture_name: str, request: pytest.FixtureRequest) -> None:
    client = request.getfixturevalue(fixture_name)

    response = client.put(f"{LIST_URL}/1", json={"name": "新名字"})

    assert response.status_code == 403


@pytest.mark.parametrize("fixture_name", ["teacher_client", "student_client"])
def test_non_admin_toggle_status_forbidden(
    fixture_name: str, request: pytest.FixtureRequest
) -> None:
    client = request.getfixturevalue(fixture_name)

    response = client.patch(f"{LIST_URL}/1/status", json={"status": "disabled"})

    assert response.status_code == 403


def test_admin_create_consumable_success(admin_client: TestClient) -> None:
    response = admin_client.post(LIST_URL, json=_payload())

    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "焊丝"
    assert data["spec"] == "φ1.2mm"
    assert data["unit"] == "卷"
    assert data["location"] == "实训室一号柜"
    assert data["safety_stock"] == 5
    assert data["current_stock"] == 10
    assert data["status"] == "enabled"
    assert data["low_stock"] is False
    assert data["id"] > 0


def test_create_writes_operation_log(admin_client: TestClient, db_session) -> None:
    data = _create_consumable(admin_client)

    log = db_session.scalars(
        select(OperationLog).where(OperationLog.action == "consumable_create")
    ).one()
    assert log.operator_id == _admin_id(db_session)
    assert log.role == "admin"
    assert log.target_type == "consumable"
    assert log.target_id == data["id"]
    assert log.before_value is None
    assert "焊丝" in (log.after_value or "")


def test_create_duplicate_name_in_same_class_rejected(admin_client: TestClient) -> None:
    _create_consumable(admin_client)

    response = admin_client.post(LIST_URL, json=_payload())

    assert response.status_code == 400
    assert "同名" in response.json()["detail"]


def test_create_negative_safety_stock_rejected(admin_client: TestClient) -> None:
    response = admin_client.post(LIST_URL, json=_payload(safety_stock=-1))

    assert response.status_code == 422


def test_create_non_integer_stock_rejected(admin_client: TestClient) -> None:
    response = admin_client.post(LIST_URL, json=_payload(safety_stock=3.5))

    assert response.status_code == 422


def test_create_blank_name_rejected(admin_client: TestClient) -> None:
    response = admin_client.post(LIST_URL, json=_payload(name="   "))

    assert response.status_code == 422


def test_create_missing_required_field_rejected(admin_client: TestClient) -> None:
    payload = _payload()
    del payload["location"]

    response = admin_client.post(LIST_URL, json=payload)

    assert response.status_code == 422


def test_update_name_and_safety_stock_success(
    admin_client: TestClient, db_session
) -> None:
    data = _create_consumable(admin_client)

    response = admin_client.put(
        f"{LIST_URL}/{data['id']}", json={"name": "焊丝2", "safety_stock": 8}
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["name"] == "焊丝2"
    assert updated["safety_stock"] == 8
    assert updated["spec"] == "φ1.2mm"
    assert updated["current_stock"] == 10

    log = db_session.scalars(
        select(OperationLog).where(OperationLog.action == "consumable_update")
    ).one()
    assert log.target_id == data["id"]
    assert "焊丝" in (log.before_value or "")
    assert "焊丝2" in (log.after_value or "")


def test_update_rejects_current_stock(admin_client: TestClient) -> None:
    data = _create_consumable(admin_client)

    response = admin_client.put(f"{LIST_URL}/{data['id']}", json={"current_stock": 99})

    assert response.status_code == 422
    assert "当前库存" in response.text


def test_update_duplicate_name_rejected(admin_client: TestClient) -> None:
    _create_consumable(admin_client, name="焊丝", spec="φ1.2mm")
    other = _create_consumable(admin_client, name="砂纸", spec="240目")

    response = admin_client.put(f"{LIST_URL}/{other['id']}", json={"name": "焊丝"})

    assert response.status_code == 400


def test_update_nonexistent_consumable(admin_client: TestClient) -> None:
    response = admin_client.put(f"{LIST_URL}/999", json={"name": "新名字"})

    assert response.status_code == 404
    assert response.json()["detail"] == "耗材不存在"


def test_update_negative_safety_stock_rejected(admin_client: TestClient) -> None:
    data = _create_consumable(admin_client)

    response = admin_client.put(f"{LIST_URL}/{data['id']}", json={"safety_stock": -3})

    assert response.status_code == 422


def test_disable_and_default_list_filters_it(admin_client: TestClient) -> None:
    data = _create_consumable(admin_client)
    _create_consumable(admin_client, name="砂纸", spec="240目")

    disable_response = admin_client.patch(
        f"{LIST_URL}/{data['id']}/status", json={"status": "disabled"}
    )

    assert disable_response.status_code == 200
    assert disable_response.json()["status"] == "disabled"

    default_list = admin_client.get(LIST_URL).json()
    assert [item["id"] for item in default_list if item["id"] == data["id"]] == []

    all_list = admin_client.get(f"{LIST_URL}?status=all").json()
    assert any(item["id"] == data["id"] for item in all_list)

    disabled_list = admin_client.get(f"{LIST_URL}?status=disabled").json()
    assert [item["id"] for item in disabled_list] == [data["id"]]


def test_disable_writes_operation_log(admin_client: TestClient, db_session) -> None:
    data = _create_consumable(admin_client)

    admin_client.patch(f"{LIST_URL}/{data['id']}/status", json={"status": "disabled"})

    log = db_session.scalars(
        select(OperationLog).where(OperationLog.action == "consumable_disable")
    ).one()
    assert log.target_id == data["id"]
    assert "enabled" in (log.before_value or "")
    assert "disabled" in (log.after_value or "")


def test_reenable_consumable(admin_client: TestClient) -> None:
    data = _create_consumable(admin_client)
    admin_client.patch(f"{LIST_URL}/{data['id']}/status", json={"status": "disabled"})

    response = admin_client.patch(f"{LIST_URL}/{data['id']}/status", json={"status": "enabled"})

    assert response.status_code == 200
    assert response.json()["status"] == "enabled"
    assert any(item["id"] == data["id"] for item in admin_client.get(LIST_URL).json())


def test_status_all_allowed_for_teacher_and_admin(
    teacher_client: TestClient, admin_client: TestClient
) -> None:
    assert teacher_client.get(f"{LIST_URL}?status=all").status_code == 200
    assert admin_client.get(f"{LIST_URL}?status=all").status_code == 200


def test_status_all_restricted_to_student(student_client: TestClient) -> None:
    response = student_client.get(f"{LIST_URL}?status=all")

    assert response.status_code == 403


def test_invalid_status_filter_rejected(admin_client: TestClient) -> None:
    response = admin_client.get(f"{LIST_URL}?status=bogus")

    assert response.status_code == 400


def test_low_stock_derived_field(admin_client: TestClient) -> None:
    low = _create_consumable(
        admin_client, name="砂纸", safety_stock=20, current_stock=5
    )
    equal = _create_consumable(
        admin_client, name="记号笔", safety_stock=8, current_stock=8
    )
    zero_line = _create_consumable(
        admin_client, name="电阻", safety_stock=0, current_stock=0
    )

    items = {item["id"]: item for item in admin_client.get(LIST_URL).json()}

    assert items[low["id"]]["low_stock"] is True
    assert items[equal["id"]]["low_stock"] is False
    assert items[zero_line["id"]]["low_stock"] is False
