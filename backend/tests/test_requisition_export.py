import csv
import io
import json

from app.models import OperationLog, User
from app.routers import requisitions as requisitions_router
from fastapi.testclient import TestClient
from sqlalchemy import select

REQUISITIONS_URL = "/api/v1/requisitions"
EXPORT_URL = "/api/v1/requisitions/export"
PREVIEW_URL = "/api/v1/requisitions/export/preview"
CONSUMABLES_URL = "/api/v1/consumables"


def _user_id(db_session, account: str) -> int:
    return db_session.scalars(select(User).where(User.account == account)).one().id


def _create_consumable(
    client: TestClient,
    *,
    name: str = "练字纸",
    spec: str = "A4",
    unit: str = "张",
    stock: int = 100,
) -> dict:
    response = client.post(
        CONSUMABLES_URL,
        json={
            "name": name,
            "spec": spec,
            "unit": unit,
            "location": "一号柜",
            "safety_stock": 5,
            "current_stock": stock,
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def _issue(
    client: TestClient,
    consumable_id: int,
    user_id: int,
    *,
    quantity: int = 1,
    remark: str | None = None,
) -> dict:
    payload = {
        "user_id": user_id,
        "items": [{"consumable_id": consumable_id, "quantity": quantity}],
    }
    if remark is not None:
        payload["remark"] = remark
    response = client.post(REQUISITIONS_URL, json=payload)
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_preview_reports_total_and_range_matching_query(
    admin_client: TestClient, db_session
) -> None:
    student_id = _user_id(db_session, "student01")
    item = _create_consumable(admin_client, stock=50)
    for quantity in (1, 2, 3):
        _issue(admin_client, item["id"], student_id, quantity=quantity)

    preview = admin_client.get(PREVIEW_URL, params={"consumable_id": item["id"]})
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert data["total"] == 3
    assert data["segments"] == 1
    assert data["limit"] == requisitions_router.CSV_EXPORT_LIMIT
    assert data["first_at"] is not None
    assert data["last_at"] is not None
    assert "order_no" in data["fields"]
    assert "posted_at" in data["fields"]

    listing = admin_client.get(
        REQUISITIONS_URL,
        params={"consumable_id": item["id"], "page_size": 100},
    ).json()
    assert listing["total"] == data["total"]


def test_segmented_export_is_complete_and_consistent(
    admin_client: TestClient, db_session, monkeypatch
) -> None:
    monkeypatch.setattr(requisitions_router, "CSV_EXPORT_LIMIT", 2)
    student_id = _user_id(db_session, "student01")
    item = _create_consumable(admin_client, stock=50)
    created = [
        _issue(admin_client, item["id"], student_id) for _ in range(5)
    ]
    expected_ids = {
        line["requisition_id"]
        for record in created
        for line in record["receipt"]["lines"]
    }

    params = {"consumable_id": item["id"]}
    first = admin_client.get(EXPORT_URL, params=params)
    assert first.status_code == 200, first.text
    assert first.headers["x-export-total"] == "5"
    assert first.headers["x-export-this-segment"] == "2"
    assert first.headers["x-export-has-more"] == "true"

    parts = [first.content.decode("utf-8-sig")]
    exported = int(first.headers["x-export-this-segment"])
    before_created_at = first.headers["x-export-next-before-created-at"]
    before_id = first.headers["x-export-next-before-id"]
    guard = 0
    while first.headers["x-export-has-more"] == "true":
        guard += 1
        assert guard < 10
        segment = admin_client.get(
            EXPORT_URL,
            params={
                **params,
                "include_header": "false",
                "before_created_at": before_created_at,
                "before_id": before_id,
            },
        )
        assert segment.status_code == 200, segment.text
        assert not segment.content.startswith(b"\xef\xbb\xbf")
        exported += int(segment.headers["x-export-this-segment"])
        parts.append(segment.content.decode("utf-8"))
        first = segment
        before_created_at = segment.headers.get("x-export-next-before-created-at")
        before_id = segment.headers.get("x-export-next-before-id")

    text = "".join(parts)
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][:3] == ["流水号", "单号", "业务时间"]
    assert len(rows) == 1 + 5
    row_ids = {int(row[0]) for row in rows[1:]}
    assert row_ids == expected_ids
    assert exported == 5
    assert len(row_ids) == len(rows) - 1


def test_csv_text_safety_blocks_formula_injection(
    admin_client: TestClient, db_session
) -> None:
    student_id = _user_id(db_session, "student01")
    item = _create_consumable(admin_client, name="=SUM(1,2)", stock=10)
    _issue(admin_client, item["id"], student_id, remark="+cmd|' /C calc'!A0")

    response = admin_client.get(EXPORT_URL, params={"consumable_id": item["id"]})
    assert response.status_code == 200, response.text
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    header = rows[0]
    name_index = header.index("耗材")
    remark_index = header.index("备注")
    values = rows[1]
    assert values[name_index] == "'=SUM(1,2)"
    assert values[remark_index].startswith("'")
    for row in rows[1:]:
        for cell in row:
            assert cell[:1] not in ("=", "+", "@")


def test_field_permissions_hide_accounts_for_teacher(
    admin_client: TestClient, db_session
) -> None:
    student_id = _user_id(db_session, "student01")
    item = _create_consumable(admin_client, stock=10)
    _issue(admin_client, item["id"], student_id, remark="安全导出")

    admin_export = admin_client.get(EXPORT_URL, params={"consumable_id": item["id"]})
    assert admin_export.status_code == 200, admin_export.text
    admin_header = admin_export.content.decode("utf-8-sig").splitlines()[0]
    assert "领用人账号" in admin_header
    assert "经办账号" in admin_header

    login = admin_client.post(
        "/api/v1/auth/login",
        json={"account": "teacher01", "password": "Teacher@123"},
    )
    assert login.status_code == 200, login.text
    teacher_export = admin_client.get(EXPORT_URL, params={"consumable_id": item["id"]})
    assert teacher_export.status_code == 200, teacher_export.text
    teacher_header = teacher_export.content.decode("utf-8-sig").splitlines()[0]
    assert "领用人" in teacher_header
    assert "经办人" in teacher_header
    assert "账号" not in teacher_header
    assert "领用人账号" not in teacher_header
    assert "经办账号" not in teacher_header


def test_export_audit_records_scope_and_counts(
    admin_client: TestClient, db_session
) -> None:
    student_id = _user_id(db_session, "student01")
    item = _create_consumable(admin_client, stock=10)
    _issue(admin_client, item["id"], student_id)

    response = admin_client.get(
        EXPORT_URL,
        params={
            "consumable_id": item["id"],
            "start_date": "2020-01-01",
            "end_date": "2030-12-31",
        },
    )
    assert response.status_code == 200, response.text

    log = db_session.scalars(
        select(OperationLog)
        .where(OperationLog.action == "requisition_export")
        .order_by(OperationLog.id.desc())
    ).first()
    assert log is not None
    assert log.operator_name == "李班委"
    assert log.role == "admin"
    assert log.result == "success"
    payload = json.loads(log.after_value)
    assert payload["matched"] == 1
    assert payload["exported"] == 1
    assert payload["has_more"] is False
    assert payload["filters"]["consumable_id"] == item["id"]
    assert payload["filters"]["start_date"] == "2020-01-01"
    assert payload["filters"]["end_date"] == "2030-12-31"
    assert "order_no" in payload["fields"]
    assert "posted_at" in payload["fields"]
    assert "user_account" in payload["fields"]


def test_student_cannot_preview_or_export(student_client: TestClient) -> None:
    assert student_client.get(PREVIEW_URL).status_code == 403
    assert student_client.get(EXPORT_URL).status_code == 403