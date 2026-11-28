import json
from datetime import timedelta

from app import business_time
from app.auth_logs import trim_audit_logs
from app.config import Settings
from app.models import OperationLog, User
from fastapi.testclient import TestClient
from sqlalchemy import select

LOGS_URL = "/api/v1/logs"
EXPORT_URL = "/api/v1/logs/export"
CONSUMABLES_URL = "/api/v1/consumables"
USERS_URL = "/api/v1/users"
REQUISITIONS_URL = "/api/v1/requisitions"


def _login(client: TestClient, account: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"account": account, "password": password}
    )
    assert response.status_code == 200, response.text


def _user_id(db_session, account: str) -> int:
    return db_session.scalars(select(User).where(User.account == account)).one().id


def test_legacy_status_snapshots_are_normalized(admin_client: TestClient, db_session) -> None:
    now = business_time.now()
    db_session.add_all(
        [
            OperationLog(
                action="user_disable",
                target_type="user",
                target_id=3,
                before_value="enabled",
                after_value="disabled",
                created_at=now,
            ),
            OperationLog(
                action="consumable_enable",
                target_type="consumable",
                target_id=9,
                before_value='{"status": "disabled", "name": "砂纸"}',
                after_value='{"status": "enabled", "name": "砂纸"}',
                created_at=now,
            ),
        ]
    )
    db_session.commit()

    data = admin_client.get(LOGS_URL, params={"keyword": "砂纸", "page_size": 100}).json()
    consumable = next(row for row in data["items"] if row["action"] == "consumable_enable")
    assert consumable["before_status"] == "disabled"
    assert consumable["after_status"] == "enabled"

    user_logs = admin_client.get(
        LOGS_URL, params={"target_type": "user", "target_id": 3}
    ).json()
    assert user_logs["total"] == 1
    assert user_logs["items"][0]["before_status"] == "enabled"
    assert user_logs["items"][0]["after_status"] == "disabled"
    assert user_logs["items"][0]["result"] == "success"


def test_search_by_target_result_operator_and_keyword(
    admin_client: TestClient, db_session
) -> None:
    admin_id = _user_id(db_session, "admin01")
    student_id = _user_id(db_session, "student01")
    item = admin_client.post(
        CONSUMABLES_URL,
        json={
            "name": "焊丝",
            "spec": "φ1.2mm",
            "unit": "卷",
            "location": "一号柜",
            "safety_stock": 5,
            "current_stock": 100,
        },
    ).json()
    issued = admin_client.post(
        REQUISITIONS_URL,
        json={"user_id": student_id, "items": [{"consumable_id": item["id"], "quantity": 2}]},
    )
    assert issued.status_code in (201, 200), issued.text
    order_no = issued.json()["order_no"]
    disabled = admin_client.patch(
        f"{CONSUMABLES_URL}/{item['id']}/status", json={"status": "disabled"}
    )
    assert disabled.status_code == 200, disabled.text
    disabled_user = admin_client.patch(
        f"{USERS_URL}/{student_id}/status", json={"status": "disabled"}
    )
    assert disabled_user.status_code == 200, disabled_user.text

    by_target = admin_client.get(
        LOGS_URL, params={"target_type": "consumable", "target_id": item["id"]}
    ).json()
    assert by_target["total"] >= 2
    assert all(row["target_id"] == item["id"] for row in by_target["items"])

    by_operator = admin_client.get(LOGS_URL, params={"operator_id": admin_id}).json()
    assert by_operator["total"] >= 2
    assert all(row["operator_name"] == "李班委" for row in by_operator["items"])
    assert all(row["operator_account"] == "admin01" for row in by_operator["items"])

    by_order = admin_client.get(LOGS_URL, params={"keyword": order_no}).json()
    assert by_order["total"] >= 1
    assert any(order_no in (row["after_value"] or "") for row in by_order["items"])

    by_user = admin_client.get(
        LOGS_URL, params={"target_type": "user", "target_id": student_id}
    ).json()
    assert by_user["total"] == 1
    assert by_user["items"][0]["after_status"] == "disabled"

    db_session.add(
        OperationLog(
            operator_id=None,
            role="operator",
            action="admin_bootstrap_rejected",
            target_type="user",
            created_at=business_time.now(),
        )
    )
    db_session.commit()
    rejected = admin_client.get(LOGS_URL, params={"result": "rejected"}).json()
    assert rejected["total"] == 1
    assert rejected["items"][0]["action"] == "admin_bootstrap_rejected"


def test_teacher_visibility_and_export_audit(
    teacher_client: TestClient, db_session
) -> None:
    export = teacher_client.get(EXPORT_URL, params={"page_size": 20})
    assert export.status_code == 200, export.text
    assert export.headers["content-type"].startswith("text/csv")
    text = export.content.decode("utf-8-sig")
    header = text.splitlines()[0]
    assert "经办人" in header
    assert "经办账号" not in header
    assert "变更前" not in header
    assert "变更后" not in header

    audit = db_session.scalars(
        select(OperationLog).where(OperationLog.action == "log_export")
    ).all()
    assert len(audit) == 1
    assert audit[0].operator_name == "王老师"
    assert audit[0].role == "teacher"
    assert audit[0].result == "success"
    payload = json.loads(audit[0].after_value)
    assert payload["exported"] == 0
    assert payload["fields"] == list(
        ("id", "created_at", "operator_name", "role", "action", "result",
         "target_type", "target_id", "target_label", "remark")
    )
    assert payload["filters"]["start_date"] is None

    assert teacher_client.get(LOGS_URL, params={"action": "log_export"}).status_code == 403
    listed = teacher_client.get(LOGS_URL, params={"page_size": 100}).json()
    assert all(row["action"] not in ("login", "logout", "log_export") for row in listed["items"])
    assert all(row["operator_account"] is None for row in listed["items"])


def test_admin_export_contains_full_fields_and_is_audited(admin_client: TestClient) -> None:
    export = admin_client.get(EXPORT_URL)
    assert export.status_code == 200, export.text
    header = export.content.decode("utf-8-sig").splitlines()[0]
    assert "经办账号" in header
    assert "变更前" in header
    assert "变更后" in header

    data = admin_client.get(LOGS_URL, params={"action": "log_export"}).json()
    assert data["total"] == 1
    assert data["items"][0]["operator_account"] == "admin01"
    assert data["items"][0]["target_label"].startswith("导出 ")


def test_audit_log_retention_definition(db_session) -> None:
    now = business_time.now()
    db_session.add_all(
        [
            OperationLog(
                action="log_export", created_at=now - timedelta(days=400)
            ),
            OperationLog(action="log_export", created_at=now - timedelta(days=1)),
            OperationLog(action="login", created_at=now - timedelta(days=400)),
        ]
    )
    db_session.commit()

    settings = Settings(_env_file=None, audit_log_retention_days=365)
    deleted = trim_audit_logs(db_session, settings)
    db_session.commit()

    assert deleted == 1
    actions = db_session.scalars(
        select(OperationLog.action).order_by(OperationLog.created_at.asc())
    ).all()
    assert actions == ["login", "log_export"]

    keep_all = trim_audit_logs(
        db_session, Settings(_env_file=None, audit_log_retention_days=0)
    )
    assert keep_all == 0


def test_soft_deleted_operator_name_snapshot(admin_client: TestClient, db_session) -> None:
    student_id = _user_id(db_session, "student01")
    disabled = admin_client.patch(
        f"{USERS_URL}/{student_id}/status", json={"status": "disabled"}
    )
    assert disabled.status_code == 200, disabled.text

    log = db_session.scalars(
        select(OperationLog)
        .where(OperationLog.action == "user_disable")
        .order_by(OperationLog.id.desc())
    ).first()
    assert log.operator_name == "李班委"
    assert log.operator_account == "admin01"
    assert log.target_label == "student01（张同学）"
