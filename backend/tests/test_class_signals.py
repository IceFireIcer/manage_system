from datetime import datetime, timedelta

from app import consumption as consumption_core
from app.models import Consumable, InventoryEvent, OperationLog, Requisition, User
from app.security import hash_password
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

SIGNALS_URL = "/api/v1/requisitions/class-signals"
PASSWORD = "Student@123"


def _login(client: TestClient, account: str = "student01", password: str = PASSWORD) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"account": account, "password": password}
    )
    assert response.status_code == 200, response.text


def _member(
    db: Session, account: str, *, class_id: int = 1, name: str | None = None
) -> User:
    user = User(
        account=account,
        password_hash=hash_password(PASSWORD),
        name=name or f"成员{account}",
        role="student",
        status="enabled",
        class_id=class_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _members(
    db: Session, count: int, *, class_id: int = 1, prefix: str = "sample"
) -> list[User]:
    return [
        _member(db, f"{prefix}{index}", class_id=class_id)
        for index in range(1, count + 1)
    ]


def _consumable(
    db: Session,
    *,
    name: str,
    unit: str = "卷",
    stock: int = 100,
    class_id: int = 1,
) -> Consumable:
    item = Consumable(
        name=name,
        spec="",
        unit=unit,
        location="一号柜",
        safety_stock=0,
        current_stock=stock,
        class_id=class_id,
        status="enabled",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _flow(
    db: Session,
    *,
    user_id: int,
    consumable_id: int,
    quantity: int,
    flow_type: str = "normal",
    flow_status: str = "active",
    created_at: datetime | None = None,
    reversal_of: int | None = None,
    class_id: int = 1,
) -> Requisition:
    record = Requisition(
        consumable_id=consumable_id,
        user_id=user_id,
        quantity=quantity,
        stock_after=0,
        type=flow_type,
        status=flow_status,
        reversal_of=reversal_of,
        class_id=class_id,
        created_at=created_at or datetime.now().replace(microsecond=0),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def _seed_window(
    db: Session,
    *,
    item: Consumable,
    users: list[User],
    created_at: datetime,
    quantity: int,
    class_id: int = 1,
) -> None:
    for user in users:
        _flow(
            db,
            user_id=user.id,
            consumable_id=item.id,
            quantity=quantity,
            created_at=created_at,
            class_id=class_id,
        )


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def test_class_signals_require_login(client: TestClient) -> None:
    assert client.get(SIGNALS_URL).status_code == 401


def test_class_signals_reject_unsupported_window(
    client: TestClient, db_session: Session
) -> None:
    _login(client)
    for window in (0, 14, 90):
        response = client.get(SIGNALS_URL, params={"window": window})
        assert response.status_code == 422


def test_class_signals_hide_below_requesters_threshold(
    client: TestClient, db_session: Session
) -> None:
    item = _consumable(db_session, name="焊丝")
    members = _members(db_session, 4)
    _seed_window(
        db_session,
        item=item,
        users=members,
        created_at=_now() - timedelta(minutes=5),
        quantity=2,
    )
    _login(client)

    response = client.get(SIGNALS_URL, params={"window": 7})
    data = response.json()

    assert response.status_code == 200
    assert data["insufficient"] is True
    assert data["threshold"] == {"min_requesters": 5, "min_flows": 3}
    assert data["sample"] is None
    assert data["total_net_quantity"] is None
    assert data["previous_net_quantity"] is None
    assert data["change_quantity"] is None
    assert data["change_ratio"] is None
    assert data["units"] == []
    assert data["items"] == []
    text = response.text
    for member in members:
        assert member.account not in text
        assert member.name not in text


def test_class_signals_count_distinct_requesters(
    client: TestClient, db_session: Session
) -> None:
    item = _consumable(db_session, name="砂纸", unit="张")
    members = _members(db_session, 4, prefix="dense")
    now = _now()
    for member in members:
        for offset in range(3):
            _flow(
                db_session,
                user_id=member.id,
                consumable_id=item.id,
                quantity=1,
                created_at=now - timedelta(minutes=10 - offset),
            )
    _login(client)

    data = client.get(SIGNALS_URL, params={"window": 7}).json()

    assert data["insufficient"] is True
    assert data["items"] == []


def test_class_signals_aggregate_current_and_previous(
    client: TestClient, db_session: Session
) -> None:
    item = _consumable(db_session, name="焊丝", unit="卷")
    members = _members(db_session, 5, prefix="ratio")
    now = _now()
    previous_at = consumption_core.local_day_start(6) - timedelta(hours=1)
    _seed_window(
        db_session,
        item=item,
        users=members,
        created_at=now - timedelta(minutes=3),
        quantity=2,
    )
    _seed_window(
        db_session,
        item=item,
        users=members,
        created_at=previous_at,
        quantity=1,
    )
    _login(client)

    data = client.get(SIGNALS_URL, params={"window": 7}).json()

    assert data["insufficient"] is False
    assert data["sample"] == {"requester_count": 5, "flow_count": 5}
    assert data["total_net_quantity"] == 10
    assert data["previous_net_quantity"] == 5
    assert data["change_quantity"] == 5
    assert data["change_ratio"] == 1.0
    assert data["units"] == [
        {
            "unit": "卷",
            "net_quantity": 10,
            "previous_net_quantity": 5,
            "change_quantity": 5,
            "change_ratio": 1.0,
            "consumables": 1,
        }
    ]
    row = data["items"][0]
    assert row["consumable_id"] == item.id
    assert row["name"] == "焊丝"
    assert row["net_quantity"] == 10
    assert row["previous_net_quantity"] == 5
    assert row["change_quantity"] == 5
    assert row["change_ratio"] == 1.0
    assert "user_id" not in row
    assert "user_name" not in row


def test_class_signals_clamp_net_at_zero(
    client: TestClient, db_session: Session
) -> None:
    item = _consumable(db_session, name="砂纸", unit="张")
    members = _members(db_session, 5, prefix="clamp")
    at = _now() - timedelta(minutes=5)
    origins = [
        _flow(
            db_session,
            user_id=member.id,
            consumable_id=item.id,
            quantity=2,
            created_at=at,
        )
        for member in members
    ]
    _flow(
        db_session,
        user_id=members[0].id,
        consumable_id=item.id,
        quantity=12,
        flow_type="reversal",
        reversal_of=origins[0].id,
        created_at=at,
    )
    _login(client)

    data = client.get(SIGNALS_URL, params={"window": 7}).json()

    assert data["total_net_quantity"] == 0
    assert data["items"][0]["net_quantity"] == 0
    assert data["items"][0]["change_quantity"] == 0
    assert data["items"][0]["change_ratio"] is None


def test_class_signals_previous_window_boundaries(
    client: TestClient, db_session: Session
) -> None:
    item = _consumable(db_session, name="焊丝")
    members = _members(db_session, 5, prefix="boundary")
    previous_start = consumption_core.local_day_start(13)
    _seed_window(
        db_session,
        item=item,
        users=members,
        created_at=_now() - timedelta(minutes=5),
        quantity=2,
    )
    _seed_window(
        db_session,
        item=item,
        users=members,
        created_at=previous_start,
        quantity=1,
    )
    _seed_window(
        db_session,
        item=item,
        users=members,
        created_at=previous_start - timedelta(seconds=1),
        quantity=100,
    )
    _login(client)

    data = client.get(SIGNALS_URL, params={"window": 7}).json()

    assert data["total_net_quantity"] == 10
    assert data["previous_net_quantity"] == 5


def test_class_signals_isolate_class_scope(
    client: TestClient, db_session: Session
) -> None:
    class_one_item = _consumable(db_session, name="焊丝", class_id=1)
    class_two_item = _consumable(db_session, name="砂纸", unit="张", class_id=2)
    class_one_members = _members(db_session, 5, prefix="scopeone")
    class_two_members = _members(
        db_session, 5, class_id=2, prefix="classtwo"
    )
    at = _now() - timedelta(minutes=5)
    _seed_window(
        db_session,
        item=class_one_item,
        users=class_one_members,
        created_at=at,
        quantity=2,
        class_id=1,
    )
    _seed_window(
        db_session,
        item=class_two_item,
        users=class_two_members,
        created_at=at,
        quantity=7,
        class_id=2,
    )
    _login(client)

    own = client.get(SIGNALS_URL, params={"window": 7}).json()
    assert own["class_id"] == 1
    assert {row["consumable_id"] for row in own["items"]} == {class_one_item.id}
    assert own["total_net_quantity"] == 10

    _login(client, "classtwo1")
    other = client.get(SIGNALS_URL, params={"window": 7}).json()
    assert other["class_id"] == 2
    assert {row["consumable_id"] for row in other["items"]} == {class_two_item.id}
    assert other["total_net_quantity"] == 35


def test_class_signals_visible_to_student_teacher_admin(
    client: TestClient, db_session: Session
) -> None:
    item = _consumable(db_session, name="焊丝")
    members = _members(db_session, 5, prefix="roles")
    _seed_window(
        db_session,
        item=item,
        users=members,
        created_at=_now() - timedelta(minutes=5),
        quantity=3,
    )
    totals = []
    for account, password in (
        ("student01", PASSWORD),
        ("teacher01", "Teacher@123"),
        ("admin01", "Admin@123"),
    ):
        _login(client, account, password)
        data = client.get(SIGNALS_URL, params={"window": 7}).json()
        assert data["insufficient"] is False
        assert data["class_id"] == 1
        totals.append(data["total_net_quantity"])
    assert totals == [15, 15, 15]


def test_class_signals_is_read_only(client: TestClient, db_session: Session) -> None:
    item = _consumable(db_session, name="焊丝", stock=42)
    members = _members(db_session, 5, prefix="readonly")
    _seed_window(
        db_session,
        item=item,
        users=members,
        created_at=_now() - timedelta(minutes=5),
        quantity=2,
    )
    _login(client)
    stock_before = db_session.get(Consumable, item.id).current_stock
    events_before = db_session.scalar(select(func.count()).select_from(InventoryEvent))
    logs_before = db_session.scalar(select(func.count()).select_from(OperationLog))

    client.get(SIGNALS_URL, params={"window": 7})
    client.get(SIGNALS_URL, params={"window": 30})
    db_session.expire_all()

    assert db_session.get(Consumable, item.id).current_stock == stock_before == 42
    assert db_session.scalar(select(func.count()).select_from(InventoryEvent)) == events_before
    assert db_session.scalar(select(func.count()).select_from(OperationLog)) == logs_before
