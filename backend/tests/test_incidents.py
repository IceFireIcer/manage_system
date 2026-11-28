from datetime import datetime

import pytest
from app.models import Consumable, IncidentReport, InventoryEvent, User
from app.routers import incidents
from app.schemas_incidents import MAX_PHOTO_BYTES
from app.security import hash_password
from fastapi.testclient import TestClient
from sqlalchemy import func, select

BASE_URL = "/api/v1/incidents"
CONSUMABLES_URL = "/api/v1/consumables"


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture(autouse=True)
def _isolated_photo_dir(tmp_path, monkeypatch):
    photo_dir = tmp_path / "incidents"
    monkeypatch.setattr(incidents, "PHOTO_DIR", photo_dir)
    return photo_dir


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


def _create_consumable(
    admin_client: TestClient,
    *,
    name: str = "焊丝",
    location: str = "实训室一号柜",
    current_stock: int = 10,
) -> dict:
    response = admin_client.post(
        CONSUMABLES_URL,
        json={
            "name": name,
            "spec": "φ1.2mm",
            "unit": "卷",
            "location": location,
            "safety_stock": 5,
            "current_stock": current_stock,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_student(
    db_session, account: str = "student02", name: str = "李同学"
) -> User:
    user = User(
        account=account,
        password_hash=hash_password("Student@123"),
        name=name,
        role="student",
        class_id=1,
        status="enabled",
        must_change_password=False,
    )
    db_session.add(user)
    db_session.commit()
    return user


def _submit(
    client: TestClient,
    consumable_id: int,
    *,
    description: str = "柜门损坏，耗材散落",
    location: str | None = None,
    photo: bytes | None = None,
    content_type: str = "image/png",
    filename: str = "incident.png",
):
    data = {"consumable_id": str(consumable_id), "description": description}
    if location is not None:
        data["location"] = location
    files = (
        {"photo": (filename, photo, content_type)} if photo is not None else None
    )
    return client.post(BASE_URL, data=data, files=files)


def _other_class_consumable(db_session) -> Consumable:
    consumable = Consumable(
        name="外班耗材",
        spec="",
        unit="件",
        location="二号柜",
        safety_stock=1,
        current_stock=10,
        class_id=2,
        status="enabled",
    )
    db_session.add(consumable)
    db_session.commit()
    return consumable


def test_requires_login(client: TestClient) -> None:
    response = client.post(
        BASE_URL, data={"consumable_id": "1", "description": "异常"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "未登录"


def test_student_submits_and_lists_only_own_records(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client)
    _login_student(admin_client)

    response = _submit(admin_client, consumable["id"], description="柜门损坏")

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["status"] == "open"
    assert data["location"] == "实训室一号柜"
    assert data["reporter_name"] == "张同学"
    assert data["reporter_role"] == "student"
    assert data["photo_url"] is None
    assert data["handler_id"] is None

    listed = admin_client.get(BASE_URL)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == data["id"]

    _create_student(db_session)
    _login(admin_client, "student02", "Student@123")
    other = _submit(admin_client, consumable["id"], description="外班同学上报")
    assert other.status_code == 201, other.text
    other_id = other.json()["id"]

    student_two_list = admin_client.get(BASE_URL).json()
    assert student_two_list["total"] == 1
    assert student_two_list["items"][0]["id"] == other_id

    _login_student(admin_client)
    own_list = admin_client.get(BASE_URL).json()
    assert [item["id"] for item in own_list["items"]] == [data["id"]]
    assert admin_client.get(f"{BASE_URL}/{other_id}").status_code == 404


def test_custom_location_and_description_validation(admin_client: TestClient) -> None:
    consumable = _create_consumable(admin_client)
    _login_student(admin_client)

    custom = _submit(admin_client, consumable["id"], location="走廊临时工位")
    assert custom.status_code == 201, custom.text
    assert custom.json()["location"] == "走廊临时工位"

    blank = _submit(admin_client, consumable["id"], description="   ")
    assert blank.status_code == 400
    assert blank.json()["detail"] == "请填写异常描述"

    too_long = _submit(admin_client, consumable["id"], description="x" * 501)
    assert too_long.status_code == 400

    missing = _submit(admin_client, 9999)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "耗材不存在"


def test_staff_queue_confirm_and_reject(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    _login_student(admin_client)
    first = _submit(admin_client, consumable["id"], description="柜门损坏").json()
    second = _submit(admin_client, consumable["id"], description="标签脱落").json()

    _login_teacher(admin_client)
    queue = admin_client.get(BASE_URL).json()
    assert queue["total"] == 2
    assert {item["id"] for item in queue["items"]} == {first["id"], second["id"]}

    confirmed = admin_client.patch(
        f"{BASE_URL}/{first['id']}", json={"action": "confirm"}
    )
    assert confirmed.status_code == 200, confirmed.text
    confirmed_data = confirmed.json()
    assert confirmed_data["status"] == "confirmed"
    assert confirmed_data["handler_name"] == "王老师"
    assert confirmed_data["handled_at"] is not None
    assert confirmed_data["reject_reason"] is None

    missing_reason = admin_client.patch(
        f"{BASE_URL}/{second['id']}", json={"action": "reject"}
    )
    assert missing_reason.status_code == 400
    assert missing_reason.json()["detail"] == "请填写驳回原因"

    rejected = admin_client.patch(
        f"{BASE_URL}/{second['id']}",
        json={"action": "reject", "reject_reason": "照片不清晰"},
    )
    assert rejected.status_code == 200, rejected.text
    rejected_data = rejected.json()
    assert rejected_data["status"] == "rejected"
    assert rejected_data["handler_name"] == "王老师"
    assert rejected_data["reject_reason"] == "照片不清晰"

    again = admin_client.patch(
        f"{BASE_URL}/{first['id']}", json={"action": "reject", "reject_reason": "重试"}
    )
    assert again.status_code == 409

    _login_student(admin_client)
    forbidden = admin_client.patch(
        f"{BASE_URL}/{first['id']}", json={"action": "confirm"}
    )
    assert forbidden.status_code == 403

    _login_admin(admin_client)
    filtered = admin_client.get(BASE_URL, params={"status": "rejected"}).json()
    assert [item["id"] for item in filtered["items"]] == [second["id"]]


def test_rate_limit(admin_client: TestClient) -> None:
    consumable = _create_consumable(admin_client)
    _login_student(admin_client)

    for index in range(5):
        response = _submit(admin_client, consumable["id"], description=f"异常 {index}")
        assert response.status_code == 201, response.text

    blocked = _submit(admin_client, consumable["id"], description="第六条")
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers

    _login_teacher(admin_client)
    assert admin_client.get(BASE_URL).json()["total"] == 5


def test_photo_type_size_and_access(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client)
    _login_student(admin_client)

    wrong_type = _submit(
        admin_client, consumable["id"], photo=b"hello", content_type="text/plain"
    )
    assert wrong_type.status_code == 400

    spoofed = _submit(admin_client, consumable["id"], photo=b"not a png")
    assert spoofed.status_code == 400

    too_large = _submit(
        admin_client,
        consumable["id"],
        photo=b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_PHOTO_BYTES,
    )
    assert too_large.status_code == 400

    created = _submit(admin_client, consumable["id"], photo=PNG_BYTES)
    assert created.status_code == 201, created.text
    photo_url = created.json()["photo_url"]
    assert photo_url == f"/api/v1/incidents/{created.json()['id']}/photo"

    fetched = admin_client.get(photo_url)
    assert fetched.status_code == 200
    assert fetched.content == PNG_BYTES


    _create_student(db_session)
    _login(admin_client, "student02", "Student@123")
    assert admin_client.get(photo_url).status_code == 404

    _login_teacher(admin_client)
    assert admin_client.get(photo_url).status_code == 200


def test_cross_class_isolation(admin_client: TestClient, db_session) -> None:
    consumable = _create_consumable(admin_client)
    other = _other_class_consumable(db_session)
    student_id = db_session.scalars(
        select(User).where(User.account == "student01")
    ).one().id
    other_report = IncidentReport(
        class_id=2,
        consumable_id=other.id,
        reporter_id=student_id,
        reporter_name="外班同学",
        reporter_role="student",
        location="二号柜",
        description="外班异常",
        status="open",
        created_at=datetime.now().replace(microsecond=0),
        updated_at=datetime.now().replace(microsecond=0),
    )
    db_session.add(other_report)
    db_session.commit()

    _login_student(admin_client)
    assert _submit(admin_client, other.id).status_code == 404
    assert admin_client.get(f"{BASE_URL}/{other_report.id}").status_code == 404

    _login_teacher(admin_client)
    assert admin_client.get(f"{BASE_URL}/{other_report.id}").status_code == 404
    visible_ids = {item["id"] for item in admin_client.get(BASE_URL).json()["items"]}
    assert other_report.id not in visible_ids
    assert _submit(admin_client, consumable["id"]).status_code == 201


def test_incident_does_not_change_stock_or_events(
    admin_client: TestClient, db_session
) -> None:
    consumable = _create_consumable(admin_client, current_stock=10)
    events_before = db_session.scalar(
        select(func.count()).select_from(InventoryEvent)
    )

    _login_student(admin_client)
    created = _submit(admin_client, consumable["id"]).json()
    _login_teacher(admin_client)
    assert (
        admin_client.patch(
            f"{BASE_URL}/{created['id']}", json={"action": "confirm"}
        ).status_code
        == 200
    )

    db_session.expire_all()
    assert db_session.get(Consumable, consumable["id"]).current_stock == 10
    events_after = db_session.scalar(select(func.count()).select_from(InventoryEvent))
    assert events_after == events_before
    assert db_session.get(IncidentReport, created["id"]).status == "confirmed"
