from typing import Any

from app.models import Consumable, ConsumableImportBatch, InventoryEvent
from fastapi.testclient import TestClient
from sqlalchemy import select

LIST_URL = "/api/v1/consumables"
PREVIEW_URL = "/api/v1/consumables/import/preview"
IMPORT_URL = "/api/v1/consumables/import"
BATCH_URL = "/api/v1/consumables/import/batches"


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


def _import_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": "焊丝",
        "spec": "φ1.2mm",
        "unit": "卷",
        "location": "实训室一号柜",
        "safety_stock": 5,
        "opening_stock": 10,
    }
    row.update(overrides)
    return row


def _import_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source": "2026 年秋季纸质台账",
        "counted_at": "2026-11-20T08:30:00",
        "rows": [_import_row()],
    }
    payload.update(overrides)
    return payload


def _preview(client: TestClient, **overrides: Any):
    return client.post(PREVIEW_URL, json=_import_payload(**overrides))


def _execute(client: TestClient, **overrides: Any):
    return client.post(IMPORT_URL, json=_import_payload(**overrides))


def _consumables(db_session) -> list[Consumable]:
    db_session.expire_all()
    return list(db_session.scalars(select(Consumable).order_by(Consumable.id)).all())


def _events(db_session, consumable_id: int) -> list[InventoryEvent]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(InventoryEvent)
            .where(InventoryEvent.consumable_id == consumable_id)
            .order_by(InventoryEvent.exec_seq)
        ).all()
    )


def test_same_name_different_spec_coexists(admin_client: TestClient) -> None:
    first = _create_consumable(admin_client, spec="φ1.2mm")
    second = _create_consumable(admin_client, spec="φ1.6mm")

    assert first["code"] and second["code"]
    assert first["code"] != second["code"]
    assert [item["spec"] for item in admin_client.get(LIST_URL).json()] == [
        "φ1.2mm",
        "φ1.6mm",
    ]


def test_same_name_same_spec_rejected(admin_client: TestClient) -> None:
    _create_consumable(admin_client)

    response = admin_client.post(LIST_URL, json=_payload())

    assert response.status_code == 400
    assert "同名同规格" in response.json()["detail"]


def test_duplicate_code_rejected(admin_client: TestClient) -> None:
    _create_consumable(admin_client, code="WELD-001")

    response = admin_client.post(
        LIST_URL, json=_payload(spec="φ1.6mm", code="WELD-001")
    )

    assert response.status_code == 400
    assert "编码" in response.json()["detail"]


def test_database_constraint_blocks_racing_create(
    admin_client: TestClient, db_session, monkeypatch
) -> None:
    import app.routers.consumables as consumables_router

    monkeypatch.setattr(
        consumables_router, "_ensure_identity_unique", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        consumables_router, "_ensure_code_unique", lambda *args, **kwargs: None
    )

    first = admin_client.post(LIST_URL, json=_payload())
    second = admin_client.post(LIST_URL, json=_payload())

    assert first.status_code == 201
    assert second.status_code == 400
    assert "同名同规格" in second.json()["detail"]
    assert len(_consumables(db_session)) == 1


def test_preview_reports_scope_and_row_errors(admin_client: TestClient) -> None:
    _create_consumable(admin_client, name="记号笔", spec="黑色", unit="支")

    response = _preview(
        admin_client,
        rows=[
            _import_row(name="焊丝", spec="φ1.2mm", unit="卷", opening_stock=10),
            _import_row(name="焊丝", spec="φ1.6mm", unit="卷", opening_stock=4),
            _import_row(name="砂纸", spec="240目", unit="张", opening_stock=0),
            _import_row(name="   ", spec="无效"),
            _import_row(name="电阻", spec="1K", unit="个", opening_stock=-1),
            _import_row(name="焊丝", spec="φ1.2mm", unit="卷", opening_stock=2),
            _import_row(name="记号笔", spec="黑色", unit="支", opening_stock=3),
        ],
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["valid"] is False
    assert data["source"] == "2026 年秋季纸质台账"
    assert data["counted_at"].startswith("2026-11-20T08:30:00")

    summary = data["summary"]
    assert summary["total_rows"] == 7
    assert summary["ready_rows"] == 3
    assert summary["existing_rows"] == 1
    assert summary["error_rows"] == 3
    assert summary["total_kinds"] == 3
    assert summary["opening_total"] == 14
    assert summary["by_unit"] == [
        {"unit": "卷", "kinds": 2, "opening_stock": 14},
        {"unit": "张", "kinds": 1, "opening_stock": 0},
    ]

    rows = data["rows"]
    assert [row["status"] for row in rows] == [
        "ready",
        "ready",
        "ready",
        "error",
        "error",
        "error",
        "existing",
    ]
    assert rows[3]["errors"][0]["field"] == "name"
    assert rows[4]["errors"][0]["code"] == "invalid_opening_stock"
    assert rows[5]["errors"][0]["code"] == "duplicate_identity_in_file"
    assert "将跳过" in rows[6]["message"]


def test_preview_rejects_code_used_by_existing_archive(
    admin_client: TestClient,
) -> None:
    _create_consumable(admin_client, name="记号笔", spec="黑色", code="PEN-01")

    response = _preview(
        admin_client, rows=[_import_row(name="砂纸", spec="240目", code="PEN-01")]
    )

    assert response.status_code == 200
    row = response.json()["rows"][0]
    assert row["status"] == "error"
    assert row["errors"][0]["code"] == "code_exists"


def test_preview_skips_existing_identity_with_same_code(
    admin_client: TestClient,
) -> None:
    _create_consumable(admin_client, name="记号笔", spec="黑色", code="PEN-01")

    response = _preview(
        admin_client,
        rows=[
            _import_row(
                name="记号笔", spec="黑色", code="PEN-01", opening_stock=3
            )
        ],
    )

    assert response.status_code == 200
    row = response.json()["rows"][0]
    assert row["status"] == "existing"
    assert response.json()["summary"]["existing_rows"] == 1


def test_preview_rejects_future_counted_at(admin_client: TestClient) -> None:
    response = _preview(admin_client, counted_at="2999-01-01T00:00:00")

    assert response.status_code == 400
    assert "不能晚于当前时间" in response.json()["detail"]


def test_import_creates_archives_and_reconciles_opening_events(
    admin_client: TestClient, db_session
) -> None:
    response = _execute(
        admin_client,
        source="纸质台账 A",
        counted_at="2026-11-18T09:00:00",
        request_id="ledger-import-1",
        rows=[
            _import_row(name="焊丝", spec="φ1.2mm", unit="卷", opening_stock=5),
            _import_row(
                name="砂纸", spec="240目", unit="张", opening_stock=3, safety_stock=10
            ),
        ],
    )

    assert response.status_code == 201, response.text
    receipt = response.json()
    assert receipt["request_id"] == "ledger-import-1"
    assert receipt["source"] == "纸质台账 A"
    assert receipt["counted_at"].startswith("2026-11-18T09:00:00")
    assert receipt["summary"]["total_kinds"] == 2
    assert receipt["summary"]["opening_total"] == 8
    assert receipt["summary"]["by_unit"] == [
        {"unit": "卷", "kinds": 1, "opening_stock": 5},
        {"unit": "张", "kinds": 1, "opening_stock": 3},
    ]
    assert all(row["reconciled"] for row in receipt["rows"])
    assert [row["opening_stock"] for row in receipt["rows"]] == [5, 3]
    assert all(row["code"] for row in receipt["rows"])

    batch = db_session.scalars(
        select(ConsumableImportBatch).where(
            ConsumableImportBatch.request_id == "ledger-import-1"
        )
    ).one()
    assert batch.class_id == 1
    assert batch.source == "纸质台账 A"

    created = _consumables(db_session)
    assert [(item.name, item.spec, item.current_stock) for item in created] == [
        ("焊丝", "φ1.2mm", 5),
        ("砂纸", "240目", 3),
    ]

    for receipt_row, archive in zip(receipt["rows"], created, strict=True):
        events = _events(db_session, archive.id)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == "opening"
        assert event.business_at == batch.counted_at
        assert event.source_type == "ledger_import"
        assert event.source_id == batch.id
        assert event.line_no == receipt_row["line_no"]
        assert event.stock_after == receipt_row["opening_stock"]
        assert event.exec_seq == 1
        assert event.unit_snapshot == archive.unit

    detail = admin_client.get(f"{BATCH_URL}/{batch.id}")
    assert detail.status_code == 200
    assert detail.json()["summary"]["total_kinds"] == 2
    assert all(row["reconciled"] for row in detail.json()["rows"])


def test_import_is_idempotent_by_request_id(
    admin_client: TestClient, db_session
) -> None:
    first = _execute(admin_client, request_id="ledger-import-repeat")
    second = _execute(admin_client, request_id="ledger-import-repeat")

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["batch_id"] == second.json()["batch_id"]
    assert len(_consumables(db_session)) == 1
    assert len(
        list(db_session.scalars(select(ConsumableImportBatch)).all())
    ) == 1


def test_import_rejects_reused_request_id_with_other_content(
    admin_client: TestClient,
) -> None:
    assert _execute(admin_client, request_id="ledger-import-conflict").status_code == 201

    response = _execute(
        admin_client,
        request_id="ledger-import-conflict",
        rows=[_import_row(name="砂纸", spec="240目", unit="张")],
    )

    assert response.status_code == 409
    assert "不同内容" in response.json()["detail"]


def test_import_with_row_errors_writes_nothing(
    admin_client: TestClient, db_session
) -> None:
    response = _execute(
        admin_client,
        rows=[
            _import_row(name="焊丝", spec="φ1.2mm"),
            _import_row(name="焊丝", spec="φ1.2mm"),
        ],
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["summary"]["error_rows"] == 1
    assert any(
        error["code"] == "duplicate_identity_in_file"
        for row in detail["rows"]
        for error in row["errors"]
    )
    assert _consumables(db_session) == []
    assert list(db_session.scalars(select(ConsumableImportBatch)).all()) == []


def test_import_keeps_zero_opening_without_event(
    admin_client: TestClient, db_session
) -> None:
    response = _execute(
        admin_client, rows=[_import_row(name="电阻", spec="1K", opening_stock=0)]
    )

    assert response.status_code == 201
    row = response.json()["rows"][0]
    assert row["event_id"] is None
    assert row["event_stock_after"] == 0
    assert row["reconciled"] is True
    archive = _consumables(db_session)[0]
    assert archive.current_stock == 0
    assert _events(db_session, archive.id) == []


def test_import_accepts_explicit_code(admin_client: TestClient) -> None:
    response = _execute(
        admin_client, rows=[_import_row(name="焊丝", spec="φ1.2mm", code="WELD-9")]
    )

    assert response.status_code == 201
    assert response.json()["rows"][0]["code"] == "WELD-9"


def test_teacher_can_import(teacher_client: TestClient) -> None:
    preview = teacher_client.post(PREVIEW_URL, json=_import_payload())
    assert preview.status_code == 200, preview.text
    imported = teacher_client.post(IMPORT_URL, json=_import_payload())
    assert imported.status_code == 201, imported.text


def test_student_cannot_import(student_client: TestClient) -> None:
    assert student_client.post(PREVIEW_URL, json=_import_payload()).status_code == 403
    assert student_client.post(IMPORT_URL, json=_import_payload()).status_code == 403
    assert student_client.get(f"{BATCH_URL}/1").status_code == 403


def test_import_batch_not_found(admin_client: TestClient) -> None:
    response = admin_client.get(f"{BATCH_URL}/999")

    assert response.status_code == 404
    assert response.json()["detail"] == "导入批次不存在"
