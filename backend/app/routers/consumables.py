from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import business_time, consumable_import, inventory_ledger
from app.agent import service
from app.database import get_db
from app.deps import CurrentUser, require_role
from app.models import Consumable, InventoryEvent, OperationLog, User
from app.schemas_consumables import (
    STATUS_ENABLED,
    ConsumableCreate,
    ConsumableOut,
    ConsumableStatusUpdate,
    ConsumableUpdate,
    LedgerImportPreviewOut,
    LedgerImportReceiptOut,
    LedgerImportRequest,
    snapshot,
)

router = APIRouter(prefix="/api/v1/consumables", tags=["consumables"])

AdminUser = Annotated[User, Depends(require_role("admin"))]


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
    )


def _log(
    db: Session,
    operator: User,
    action: str,
    consumable: Consumable,
    before: str | None,
) -> None:
    db.add(
        OperationLog(
            operator_id=operator.id,
            role=operator.role,
            action=action,
            target_type="consumable",
            target_id=consumable.id,
            before_value=before,
            after_value=snapshot(consumable),
        )
    )


def _get_or_404(db: Session, consumable_id: int) -> Consumable:
    consumable = db.get(Consumable, consumable_id)
    if consumable is None:
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
    current_user: CurrentUser,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> list[ConsumableOut]:
    if status_filter is None:
        status_filter = STATUS_ENABLED

    if status_filter not in ("enabled", "disabled", "all"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="status 参数仅支持 enabled/disabled/all",
        )
    if status_filter in ("disabled", "all") and current_user.role == "student":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权限访问",
        )

    query = (
        select(Consumable)
        .where(Consumable.class_id == current_user.class_id)
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
    class_id = admin.class_id
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
    consumable = _get_or_404(db, consumable_id)
    before = snapshot(consumable)

    data = payload.model_dump(exclude_unset=True)
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


@router.patch("/{consumable_id}/status")
def toggle_consumable_status(
    consumable_id: int,
    payload: ConsumableStatusUpdate,
    db: Annotated[Session, Depends(get_db)],
    admin: AdminUser,
) -> ConsumableOut:
    consumable = _get_or_404(db, consumable_id)
    if consumable.status == payload.status:
        return _serialize(consumable)

    before = snapshot(consumable)
    consumable.status = payload.status
    consumable.updated_at = business_time.now()
    action = "consumable_disable" if payload.status == "disabled" else "consumable_enable"
    _log(db, admin, action, consumable, before)
    db.commit()
    service.clear_cache()
    db.refresh(consumable)
    return _serialize(consumable)

@router.post("/import/preview")
def preview_ledger_import(
    payload: LedgerImportRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: AdminUser,
) -> LedgerImportPreviewOut:
    return consumable_import.build_preview(
        db, class_id=admin.class_id, payload=payload
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
        db, batch_id=batch_id, class_id=admin.class_id
    )
