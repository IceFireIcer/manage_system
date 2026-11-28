import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import business_time, consumable_import, inventory_ledger, stock_alerts
from app.agent import service
from app.database import get_db
from app.deps import effective_role, require_permission
from app.models import (
    Consumable,
    InventoryEvent,
    OperationLog,
    Requisition,
    RequisitionCorrection,
    RequisitionOrder,
    RestockDisposition,
    RestockOrder,
    User,
)
from app.operation_log import consumable_label, record_operation
from app.schemas_consumables import (
    STATUS_DISABLED,
    STATUS_ENABLED,
    ConsumableCreate,
    ConsumableOut,
    ConsumableStatusOut,
    ConsumableStatusUpdate,
    ConsumableUpdate,
    DeactivationPreviewOut,
    LedgerImportPreviewOut,
    LedgerImportReceiptOut,
    LedgerImportRequest,
    PendingActionOut,
    PendingReceiptOut,
    StatusAuditOut,
    snapshot,
)

router = APIRouter(prefix="/api/v1/consumables", tags=["consumables"])

ConsumableViewer = Annotated[User, Depends(require_permission("consumable.view"))]
AdminUser = Annotated[User, Depends(require_permission("consumable.manage"))]


def _serialize(consumable: Consumable) -> ConsumableOut:
    return ConsumableOut(
        id=consumable.id,
        code=consumable.code,
        name=consumable.name,
        spec=consumable.spec,
        unit=consumable.unit,
        location=consumable.location,
        safety_stock=consumable.safety_stock,
        current_stock=consumable.current_stock,
        class_id=consumable.class_id,
        status=consumable.status,
        low_stock=consumable.safety_stock > 0
        and consumable.current_stock < consumable.safety_stock,
        disabled_reason=consumable.disabled_reason,
        version=consumable.version,
    )


def _log(
    db: Session,
    operator: User,
    action: str,
    consumable: Consumable,
    before: str | None,
    remark: str | None = None,
) -> None:
    record_operation(
        db,
        action=action,
        operator=operator,
        target_type="consumable",
        target_id=consumable.id,
        target_label=consumable_label(consumable),
        before_value=before,
        after_value=snapshot(consumable),
        remark=remark,
    )


def _assert_version(
    db: Session,
    operator: User,
    consumable: Consumable,
    expected_version: int | None,
    attempted: dict,
) -> None:
    if expected_version is None or expected_version == consumable.version:
        return
    record_operation(
        db,
        action="consumable_conflict",
        operator=operator,
        result="failure",
        target_type="consumable",
        target_id=consumable.id,
        target_label=consumable_label(consumable),
        before_value=json.dumps(
            {"expected_version": expected_version, "attempted": attempted},
            ensure_ascii=False,
            default=str,
        ),
        after_value=snapshot(consumable),
        remark="并发修改冲突，未写入",
    )
    db.commit()
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "message": "档案已被他人修改，请重新核对后再提交",
            "version": consumable.version,
            "current": json.loads(snapshot(consumable)),
        },
    )


def _disabled_log(db: Session, consumable_id: int) -> OperationLog | None:
    return db.scalars(
        select(OperationLog)
        .where(
            OperationLog.target_type == "consumable",
            OperationLog.target_id == consumable_id,
            OperationLog.action == "consumable_disable",
        )
        .order_by(OperationLog.id.desc())
    ).first()


def _logged_stock(log: OperationLog | None) -> int | None:
    if log is None or not log.after_value:
        return None
    try:
        value = json.loads(log.after_value).get("current_stock")
    except (TypeError, ValueError, AttributeError):
        return None
    return value if isinstance(value, int) else None


def _signature_ids(value: str | None) -> set[int]:
    if not value:
        return set()
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return set()
    if not isinstance(parsed, list):
        return set()
    return {
        entry[0]
        for entry in parsed
        if isinstance(entry, list) and entry and isinstance(entry[0], int)
    }


def _signature_quantity(value: str | None, consumable_id: int) -> int | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, list):
        return None
    for entry in parsed:
        if (
            isinstance(entry, list)
            and len(entry) >= 2
            and entry[0] == consumable_id
            and isinstance(entry[1], int)
        ):
            return entry[1]
    return None


def _deactivation_context(
    db: Session, consumable: Consumable
) -> DeactivationPreviewOut:
    disabled_log = _disabled_log(db, consumable.id)
    disabled_at = disabled_log.created_at if disabled_log else None
    disabled_by_name = None
    if disabled_log and disabled_log.operator_id is not None:
        operator = db.get(User, disabled_log.operator_id)
        disabled_by_name = operator.name if operator else None

    pending_receipts: list[PendingReceiptOut] = []
    processing_restocks = db.scalars(
        select(RestockOrder).where(
            RestockOrder.class_id == consumable.class_id,
            RestockOrder.status == "processing",
        )
    ).all()
    for order in processing_restocks:
        if consumable.id not in _signature_ids(order.items_signature):
            continue
        pending_receipts.append(
            PendingReceiptOut(
                order_no=order.order_no,
                quantity=_signature_quantity(
                    order.items_signature, consumable.id
                )
                or 0,
                created_at=order.created_at,
            )
        )

    pending_actions: list[PendingActionOut] = []

    processing_requisitions = db.scalars(
        select(RequisitionOrder).where(
            RequisitionOrder.class_id == consumable.class_id,
            RequisitionOrder.status == "processing",
        )
    ).all()
    processing_count = sum(
        1
        for order in processing_requisitions
        if consumable.id in _signature_ids(order.items_signature)
    )
    if processing_count:
        pending_actions.append(
            PendingActionOut(
                kind="requisition_order",
                label="处理中的领用登记",
                count=processing_count,
            )
        )

    correction_count = (
        db.scalar(
            select(func.count())
            .select_from(RequisitionCorrection)
            .join(Requisition, RequisitionCorrection.requisition_id == Requisition.id)
            .where(
                Requisition.consumable_id == consumable.id,
                RequisitionCorrection.status == "submitted",
            )
        )
        or 0
    )
    if correction_count:
        pending_actions.append(
            PendingActionOut(
                kind="correction",
                label="待处理纠错申请",
                count=correction_count,
            )
        )

    latest_disposition = db.scalars(
        select(RestockDisposition)
        .where(RestockDisposition.consumable_id == consumable.id)
        .order_by(RestockDisposition.id.desc())
    ).first()
    if latest_disposition is not None and latest_disposition.status != "resolved":
        pending_actions.append(
            PendingActionOut(
                kind="disposition",
                label="未完成的补货处置",
                count=1,
            )
        )

    return DeactivationPreviewOut(
        consumable=_serialize(consumable),
        disabled_stock=_logged_stock(disabled_log),
        disabled_at=disabled_at,
        disabled_by_name=disabled_by_name,
        pending_receipts=pending_receipts,
        pending_actions=pending_actions,
    )


def _get_or_404(
    db: Session, consumable_id: int, class_id: int | None = None
) -> Consumable:
    consumable = db.get(Consumable, consumable_id)
    if consumable is None or (
        class_id is not None and consumable.class_id != class_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="耗材不存在")
    return consumable


def _ensure_identity_unique(
    db: Session,
    class_id: int,
    name: str,
    spec: str,
    exclude_id: int | None = None,
) -> None:
    query = select(Consumable).where(
        Consumable.class_id == class_id,
        Consumable.name == name,
        Consumable.spec == spec,
    )
    if exclude_id is not None:
        query = query.where(Consumable.id != exclude_id)
    if db.scalars(query).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"同班级已存在同名同规格耗材「{name} / {spec}」，请更换规格或名称",
        )


def _ensure_code_unique(
    db: Session,
    class_id: int,
    code: str | None,
    exclude_id: int | None = None,
) -> None:
    if not code:
        return
    query = select(Consumable).where(
        Consumable.class_id == class_id, Consumable.code == code
    )
    if exclude_id is not None:
        query = query.where(Consumable.id != exclude_id)
    if db.scalars(query).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"同班级已存在编码「{code}」的耗材，请更换编码",
        )


def _creation_conflict_detail(
    db: Session, class_id: int, name: str, spec: str, code: str | None
) -> str:
    if code:
        existing = db.scalars(
            select(Consumable).where(
                Consumable.class_id == class_id, Consumable.code == code
            )
        ).first()
        if existing is not None:
            return f"同班级已存在编码「{code}」的耗材，请更换编码"
    return (
        f"同班级已存在同名同规格耗材「{name} / {spec}」，请更换规格或名称"
    )


@router.get("")
def list_consumables(
    db: Annotated[Session, Depends(get_db)],
    current_user: ConsumableViewer,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> list[ConsumableOut]:
    if status_filter is None:
        status_filter = STATUS_ENABLED

    if status_filter not in ("enabled", "disabled", "all"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="status 参数仅支持 enabled/disabled/all",
        )
    if status_filter in ("disabled", "all") and effective_role(current_user) == "student":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权限访问",
        )

    query = (
        select(Consumable)
        .where(Consumable.class_id == current_user.active_class_id)
        .order_by(Consumable.id)
    )
    if status_filter != "all":
        query = query.where(Consumable.status == status_filter)
    return [_serialize(item) for item in db.scalars(query).all()]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_consumable(
    payload: ConsumableCreate,
    db: Annotated[Session, Depends(get_db)],
    admin: AdminUser,
) -> ConsumableOut:
    class_id = admin.active_class_id
    name = payload.name.strip()
    spec = payload.spec.strip()
    _ensure_identity_unique(db, class_id, name, spec)
    _ensure_code_unique(db, class_id, payload.code)

    consumable = Consumable(
        code=payload.code,
        name=name,
        spec=spec,
        unit=payload.unit.strip(),
        location=payload.location.strip(),
        safety_stock=payload.safety_stock,
        current_stock=payload.current_stock,
        class_id=class_id,
        status=STATUS_ENABLED,
    )
    db.add(consumable)
    try:
        db.flush()
        if not consumable.code:
            consumable.code = consumable_import.generate_code(consumable.id)
            db.flush()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_creation_conflict_detail(
                db, class_id, name, spec, payload.code
            ),
        ) from error
    inventory_ledger.record_opening(db, consumable, admin)
    stock_alerts.sync_consumable(db, consumable, source="consumable")
    _log(db, admin, "consumable_create", consumable, None)
    db.commit()
    service.clear_cache()
    db.refresh(consumable)
    return _serialize(consumable)


@router.put("/{consumable_id}")
def update_consumable(
    consumable_id: int,
    payload: ConsumableUpdate,
    db: Annotated[Session, Depends(get_db)],
    admin: AdminUser,
) -> ConsumableOut:
    consumable = _get_or_404(db, consumable_id, admin.active_class_id)
    data = payload.model_dump(exclude_unset=True)
    data.pop("expected_version", None)
    _assert_version(db, admin, consumable, payload.expected_version, data)
    before = snapshot(consumable)

    name = consumable.name
    spec = consumable.spec
    if "name" in data or "spec" in data:
        name = (data.get("name") or consumable.name).strip()
        spec = (data.get("spec") or consumable.spec).strip()
        _ensure_identity_unique(
            db,
            consumable.class_id,
            name,
            spec,
            exclude_id=consumable.id,
        )
        consumable.name = name
        consumable.spec = spec
    if "unit" in data:
        new_unit = data["unit"].strip()
        if new_unit != consumable.unit:
            has_events = db.scalars(
                select(InventoryEvent.id)
                .where(InventoryEvent.consumable_id == consumable.id)
                .limit(1)
            ).first()
            if consumable.current_stock != 0 or has_events is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "耗材已有余额或历史流水，不能直接修改计量单位；"
                        "请新建档案并在备注中保留旧单位、换算依据与来源"
                    ),
                )
            consumable.unit = new_unit
    if "location" in data:
        consumable.location = data["location"].strip()
    if "safety_stock" in data:
        consumable.safety_stock = data["safety_stock"]

    consumable.updated_at = business_time.now()
    consumable.version += 1
    stock_alerts.sync_consumable(db, consumable, source="consumable")
    _log(db, admin, "consumable_update", consumable, before)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_creation_conflict_detail(
                db,
                consumable.class_id,
                consumable.name,
                consumable.spec,
                consumable.code,
            ),
        ) from error
    service.clear_cache()
    db.refresh(consumable)
    return _serialize(consumable)


@router.get("/{consumable_id}/deactivation-preview")
def preview_deactivation(
    consumable_id: int,
    db: Annotated[Session, Depends(get_db)],
    admin: AdminUser,
) -> DeactivationPreviewOut:
    consumable = _get_or_404(db, consumable_id, admin.active_class_id)
    return _deactivation_context(db, consumable)


@router.patch("/{consumable_id}/status")
def toggle_consumable_status(
    consumable_id: int,
    payload: ConsumableStatusUpdate,
    db: Annotated[Session, Depends(get_db)],
    admin: AdminUser,
) -> ConsumableStatusOut:
    consumable = _get_or_404(db, consumable_id, admin.active_class_id)
    _assert_version(
        db,
        admin,
        consumable,
        payload.expected_version,
        {"status": payload.status, "reason": payload.reason},
    )

    disabled_log = _disabled_log(db, consumable.id)
    disabled_stock = _logged_stock(disabled_log)
    if consumable.status == payload.status:
        return ConsumableStatusOut(
            **_serialize(consumable).model_dump(),
            audit=StatusAuditOut(
                previous_stock=disabled_stock,
                current_stock=consumable.current_stock,
                balance_changed=False,
                reason=payload.reason,
            ),
        )

    before = snapshot(consumable)
    if payload.status == STATUS_DISABLED:
        consumable.disabled_reason = payload.reason
    else:
        consumable.disabled_reason = None
    consumable.status = payload.status
    consumable.version += 1
    consumable.updated_at = business_time.now()
    stock_alerts.sync_consumable(db, consumable, source="consumable")
    action = (
        "consumable_disable"
        if payload.status == STATUS_DISABLED
        else "consumable_enable"
    )
    _log(db, admin, action, consumable, before, remark=payload.reason)
    db.commit()
    service.clear_cache()
    db.refresh(consumable)

    if payload.status == STATUS_DISABLED:
        audit = StatusAuditOut(
            previous_stock=consumable.current_stock,
            current_stock=consumable.current_stock,
            balance_changed=False,
            reason=payload.reason,
        )
    else:
        audit = StatusAuditOut(
            previous_stock=disabled_stock,
            current_stock=consumable.current_stock,
            balance_changed=(
                disabled_stock is not None
                and disabled_stock != consumable.current_stock
            ),
            reason=payload.reason,
        )
    return ConsumableStatusOut(
        **_serialize(consumable).model_dump(), audit=audit
    )


@router.post("/import/preview")
def preview_ledger_import(
    payload: LedgerImportRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: AdminUser,
) -> LedgerImportPreviewOut:
    return consumable_import.build_preview(
        db, class_id=admin.active_class_id, payload=payload
    )


@router.post("/import")
def import_ledger(
    payload: LedgerImportRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: AdminUser,
) -> JSONResponse:
    receipt, created = consumable_import.execute_import(
        db, operator=admin, payload=payload
    )
    service.clear_cache()
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=jsonable_encoder(receipt),
    )


@router.get("/import/batches/{batch_id}")
def get_import_batch(
    batch_id: int,
    db: Annotated[Session, Depends(get_db)],
    admin: AdminUser,
) -> LedgerImportReceiptOut:
    return consumable_import.get_receipt(
        db, batch_id=batch_id, class_id=admin.active_class_id
    )
