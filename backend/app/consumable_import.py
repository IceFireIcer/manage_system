import hashlib
import json
import uuid
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import business_time, inventory_ledger, stock_alerts
from app.class_scope import active_class_id
from app.models import Consumable, ConsumableImportBatch, InventoryEvent, OperationLog, User
from app.schemas_consumables import (
    CODE_MAX_LENGTH,
    IMPORT_STATUS_ERROR,
    IMPORT_STATUS_EXISTING,
    IMPORT_STATUS_READY,
    STATUS_ENABLED,
    ImportRowErrorOut,
    ImportUnitSummaryOut,
    LedgerImportPreviewOut,
    LedgerImportReceiptOut,
    LedgerImportReceiptRowOut,
    LedgerImportRequest,
    LedgerImportRowIn,
    LedgerImportRowOut,
    LedgerImportSummaryOut,
    snapshot,
)

SOURCE_LEDGER_IMPORT = "ledger_import"


def generate_code(consumable_id: int) -> str:
    return f"C-{consumable_id:08d}"


def _normalize_counted_at(value: datetime) -> datetime:
    counted_at = business_time.normalize(value).replace(microsecond=0)
    if counted_at > business_time.now():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="初始计数时点不能晚于当前时间",
        )
    return counted_at


def _text(
    value: str | None,
    *,
    field: str,
    label: str,
    max_length: int,
    errors: list[ImportRowErrorOut],
) -> str | None:
    if value is None:
        errors.append(
            ImportRowErrorOut(
                field=field, code=f"missing_{field}", reason=f"缺少{label}"
            )
        )
        return None
    stripped = value.strip()
    if not stripped:
        errors.append(
            ImportRowErrorOut(
                field=field, code=f"blank_{field}", reason=f"{label}不能为空白内容"
            )
        )
        return None
    if len(stripped) > max_length:
        errors.append(
            ImportRowErrorOut(
                field=field,
                code=f"invalid_{field}",
                reason=f"{label}不能超过 {max_length} 个字符",
            )
        )
        return None
    return stripped


def _optional_code(
    value: str | None, errors: list[ImportRowErrorOut]
) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if len(stripped) > CODE_MAX_LENGTH:
        errors.append(
            ImportRowErrorOut(
                field="code",
                code="invalid_code",
                reason=f"编码不能超过 {CODE_MAX_LENGTH} 个字符",
            )
        )
        return None
    if any(ch not in "-_.:" and not ch.isalnum() for ch in stripped):
        errors.append(
            ImportRowErrorOut(
                field="code",
                code="invalid_code",
                reason="编码仅支持字母、数字与 - _ . : 字符",
            )
        )
        return None
    return stripped


def _stock(
    value: int | str | None,
    *,
    field: str,
    label: str,
    errors: list[ImportRowErrorOut],
    default: int = 0,
) -> int:
    if value is None:
        return default
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return default
        try:
            value = int(stripped)
        except ValueError:
            errors.append(
                ImportRowErrorOut(
                    field=field,
                    code=f"invalid_{field}",
                    reason=f"{label}必须为非负整数",
                )
            )
            return default
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(
            ImportRowErrorOut(
                field=field, code=f"invalid_{field}", reason=f"{label}必须为非负整数"
            )
        )
        return default
    if value < 0:
        errors.append(
            ImportRowErrorOut(
                field=field, code=f"invalid_{field}", reason=f"{label}必须为非负整数"
            )
        )
        return default
    return value


def _build_summary(
    rows: list[LedgerImportRowOut],
) -> LedgerImportSummaryOut:
    ready = [row for row in rows if row.status == IMPORT_STATUS_READY]
    existing = [row for row in rows if row.status == IMPORT_STATUS_EXISTING]
    failed = [row for row in rows if row.status == IMPORT_STATUS_ERROR]
    buckets: dict[str, list[int]] = {}
    for row in ready:
        unit = row.unit or ""
        bucket = buckets.setdefault(unit, [0, 0])
        bucket[0] += 1
        bucket[1] += row.opening_stock
    by_unit = [
        ImportUnitSummaryOut(unit=unit, kinds=values[0], opening_stock=values[1])
        for unit, values in sorted(buckets.items())
    ]
    return LedgerImportSummaryOut(
        total_rows=len(rows),
        ready_rows=len(ready),
        existing_rows=len(existing),
        error_rows=len(failed),
        total_kinds=len(ready),
        opening_total=sum(row.opening_stock for row in ready),
        by_unit=by_unit,
    )


def _evaluate_row(
    index: int,
    row: LedgerImportRowIn,
    *,
    existing_by_code: dict[str, Consumable],
    existing_by_identity: dict[tuple[str, str], Consumable],
    seen_codes: set[str],
    seen_identities: set[tuple[str, str]],
) -> LedgerImportRowOut:
    line_no = row.line_no or index
    errors: list[ImportRowErrorOut] = []
    code = _optional_code(row.code, errors)
    name = _text(
        row.name, field="name", label="名称", max_length=128, errors=errors
    )
    spec = _text(
        row.spec, field="spec", label="规格", max_length=128, errors=errors
    )
    unit = _text(
        row.unit, field="unit", label="单位", max_length=16, errors=errors
    )
    location = _text(
        row.location, field="location", label="地点", max_length=64, errors=errors
    )
    safety_stock = _stock(
        row.safety_stock, field="safety_stock", label="安全库存线", errors=errors
    )
    opening_stock = _stock(
        row.opening_stock, field="opening_stock", label="期初库存", errors=errors
    )

    status_value = IMPORT_STATUS_READY
    message: str | None = None
    if not errors and name is not None and spec is not None:
        identity = (name, spec)
        matched_identity = existing_by_identity.get(identity)
        matched_code = existing_by_code.get(code) if code is not None else None
        if matched_identity is not None:
            if (
                matched_code is not None
                and matched_code.id != matched_identity.id
            ):
                errors.append(
                    ImportRowErrorOut(
                        field="code",
                        code="code_exists",
                        reason=(
                            f"编码已被档案「{matched_code.name} / "
                            f"{matched_code.spec}」使用"
                        ),
                    )
                )
            else:
                status_value = IMPORT_STATUS_EXISTING
                message = (
                    f"已存在同名同规格档案「"
                    f"{matched_identity.code or matched_identity.id}」，将跳过"
                )
        elif matched_code is not None:
            errors.append(
                ImportRowErrorOut(
                    field="code",
                    code="code_exists",
                    reason=(
                        f"编码已被档案「{matched_code.name} / "
                        f"{matched_code.spec}」使用"
                    ),
                )
            )
        elif code is not None and code in seen_codes:
            errors.append(
                ImportRowErrorOut(
                    field="code", code="duplicate_code_in_file", reason="批内编码重复"
                )
            )
        elif identity in seen_identities:
            errors.append(
                ImportRowErrorOut(
                    field="name",
                    code="duplicate_identity_in_file",
                    reason="批内存在同名同规格行",
                )
            )

        if status_value == IMPORT_STATUS_READY:
            seen_identities.add(identity)
            if code is not None:
                seen_codes.add(code)

    if errors:
        status_value = IMPORT_STATUS_ERROR
        message = errors[0].reason

    return LedgerImportRowOut(
        line_no=line_no,
        code=code,
        name=name,
        spec=spec,
        unit=unit,
        location=location,
        safety_stock=safety_stock,
        opening_stock=opening_stock,
        status=status_value,
        message=message,
        errors=errors,
    )


def build_preview(
    db: Session, *, class_id: int, payload: LedgerImportRequest
) -> LedgerImportPreviewOut:
    counted_at = _normalize_counted_at(payload.counted_at)
    consumables = db.scalars(
        select(Consumable).where(Consumable.class_id == class_id)
    ).all()
    existing_by_code = {
        item.code: item for item in consumables if item.code
    }
    existing_by_identity = {(item.name, item.spec): item for item in consumables}

    seen_codes: set[str] = set()
    seen_identities: set[tuple[str, str]] = set()
    rows = [
        _evaluate_row(
            index,
            row,
            existing_by_code=existing_by_code,
            existing_by_identity=existing_by_identity,
            seen_codes=seen_codes,
            seen_identities=seen_identities,
        )
        for index, row in enumerate(payload.rows, start=1)
    ]
    summary = _build_summary(rows)
    return LedgerImportPreviewOut(
        source=payload.source,
        counted_at=counted_at,
        valid=summary.error_rows == 0 and summary.ready_rows > 0,
        summary=summary,
        rows=rows,
    )


def _signature(preview: LedgerImportPreviewOut) -> str:
    material = {
        "source": preview.source,
        "counted_at": preview.counted_at.isoformat(),
        "rows": [
            [
                row.line_no,
                row.code,
                row.name,
                row.spec,
                row.unit,
                row.location,
                row.safety_stock,
                row.opening_stock,
            ]
            for row in preview.rows
        ],
    }
    payload = json.dumps(material, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _receipt_from_batch(
    db: Session, batch: ConsumableImportBatch
) -> LedgerImportReceiptOut:
    stored = json.loads(batch.receipt_json or "{}")
    stored_rows = stored.get("rows", [])
    consumable_ids = [row["consumable_id"] for row in stored_rows]
    event_ids = [row["event_id"] for row in stored_rows if row["event_id"]]
    consumables = (
        {
            item.id: item
            for item in db.scalars(
                select(Consumable).where(Consumable.id.in_(consumable_ids))
            )
        }
        if consumable_ids
        else {}
    )
    events = (
        {
            item.id: item
            for item in db.scalars(
                select(InventoryEvent).where(InventoryEvent.id.in_(event_ids))
            )
        }
        if event_ids
        else {}
    )

    rows: list[LedgerImportReceiptRowOut] = []
    for row in stored_rows:
        event = events.get(row["event_id"]) if row["event_id"] else None
        event_stock_after = event.stock_after if event is not None else 0
        reconciled = (
            row["consumable_id"] in consumables
            and row["opening_stock"] == event_stock_after
            and (row["opening_stock"] > 0) == (event is not None)
        )
        rows.append(
            LedgerImportReceiptRowOut(
                line_no=row["line_no"],
                consumable_id=row["consumable_id"],
                code=row["code"],
                name=row["name"],
                spec=row["spec"],
                unit=row["unit"],
                opening_stock=row["opening_stock"],
                event_id=row["event_id"],
                event_stock_after=event_stock_after,
                reconciled=reconciled,
            )
        )

    return LedgerImportReceiptOut(
        batch_id=batch.id,
        request_id=batch.request_id,
        source=batch.source,
        counted_at=batch.counted_at,
        operator_name=batch.operator_name,
        created_at=batch.created_at,
        summary=LedgerImportSummaryOut(
            total_rows=batch.total_rows,
            ready_rows=batch.created_kinds,
            existing_rows=batch.skipped_existing,
            error_rows=0,
            total_kinds=batch.created_kinds,
            opening_total=batch.opening_total,
            by_unit=[
                ImportUnitSummaryOut(**item)
                for item in json.loads(batch.by_unit_json or "[]")
            ],
        ),
        rows=rows,
    )


def get_receipt(db: Session, *, batch_id: int, class_id: int) -> LedgerImportReceiptOut:
    batch = db.get(ConsumableImportBatch, batch_id)
    if batch is None or batch.class_id != class_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="导入批次不存在"
        )
    return _receipt_from_batch(db, batch)


def _existing_batch(db: Session, request_id: str) -> ConsumableImportBatch | None:
    return db.scalars(
        select(ConsumableImportBatch).where(
            ConsumableImportBatch.request_id == request_id
        )
    ).first()


def execute_import(
    db: Session, *, operator: User, payload: LedgerImportRequest
) -> tuple[LedgerImportReceiptOut, bool]:
    preview = build_preview(db, class_id=active_class_id(operator), payload=payload)
    request_id = payload.request_id or uuid.uuid4().hex
    signature = _signature(preview)
    existing = _existing_batch(db, request_id)
    if existing is not None:
        if existing.signature != signature:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="请求标识已用于不同内容，请勿复用",
            )
        return _receipt_from_batch(db, existing), False

    if preview.summary.error_rows or preview.summary.ready_rows == 0:
        detail = preview.model_dump(mode="json")
        detail["message"] = (
            "存在行级错误，请修正后重新预览"
            if preview.summary.error_rows
            else "没有可导入的新档案"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=detail
        )

    now = business_time.now().replace(microsecond=0)
    batch = ConsumableImportBatch(
        request_id=request_id,
        signature=signature,
        source=preview.source,
        counted_at=preview.counted_at,
        operator_id=operator.id,
        operator_name=operator.name,
        operator_role=operator.role,
        class_id=active_class_id(operator),
        total_rows=preview.summary.total_rows,
        created_kinds=0,
        skipped_existing=preview.summary.existing_rows,
        opening_total=0,
        by_unit_json="[]",
        receipt_json="{}",
        created_at=now,
    )
    db.add(batch)
    try:
        db.flush()
        receipt_rows: list[dict] = []
        for row in preview.rows:
            if row.status != IMPORT_STATUS_READY:
                continue
            opening_stock = row.opening_stock
            consumable = Consumable(
                code=row.code,
                name=row.name or "",
                spec=row.spec or "",
                unit=row.unit or "",
                location=row.location or "",
                safety_stock=row.safety_stock,
                current_stock=opening_stock,
                class_id=active_class_id(operator),
                status=STATUS_ENABLED,
                created_at=now,
                updated_at=now,
            )
            db.add(consumable)
            db.flush()
            if not consumable.code:
                consumable.code = generate_code(consumable.id)
                db.flush()
            stock_alerts.sync_consumable(db, consumable, source="import", at=now)

            event = None
            if opening_stock > 0:
                event = inventory_ledger.append_event(
                    db,
                    consumable=consumable,
                    event_type=inventory_ledger.EVENT_OPENING,
                    delta=opening_stock,
                    stock_before=0,
                    stock_after=opening_stock,
                    business_at=preview.counted_at,
                    posted_at=now,
                    source_type=SOURCE_LEDGER_IMPORT,
                    source_id=batch.id,
                    line_no=row.line_no,
                    operator=operator,
                    unit_snapshot=consumable.unit,
                )
            db.add(
                OperationLog(
                    operator_id=operator.id,
                    role=operator.role,
                    action="consumable_import",
                    target_type="consumable",
                    target_id=consumable.id,
                    after_value=snapshot(consumable),
                    remark=preview.source,
                )
            )
            receipt_rows.append(
                {
                    "line_no": row.line_no,
                    "consumable_id": consumable.id,
                    "code": consumable.code,
                    "name": consumable.name,
                    "spec": consumable.spec,
                    "unit": consumable.unit,
                    "opening_stock": opening_stock,
                    "event_id": event.id if event is not None else None,
                    "event_stock_after": opening_stock,
                }
            )

        summary = _build_summary(
            [row for row in preview.rows if row.status == IMPORT_STATUS_READY]
        )
        batch.created_kinds = summary.total_kinds
        batch.opening_total = summary.opening_total
        batch.by_unit_json = json.dumps(
            [item.model_dump() for item in summary.by_unit], ensure_ascii=False
        )
        batch.receipt_json = json.dumps(
            {"summary": summary.model_dump(), "rows": receipt_rows},
            ensure_ascii=False,
        )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        conflict = _existing_batch(db, request_id)
        if conflict is not None and conflict.signature == signature:
            return _receipt_from_batch(db, conflict), False
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="导入期间档案被并发创建，请重新预览后再导入",
        ) from error

    db.refresh(batch)
    return _receipt_from_batch(db, batch), True
