from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import business_time
from app.agent import service
from app.database import get_db
from app.deps import CurrentUser, require_role
from app.models import Consumable, OperationLog, User
from app.schemas_consumables import (
    STATUS_ENABLED,
    ConsumableCreate,
    ConsumableOut,
    ConsumableStatusUpdate,
    ConsumableUpdate,
    snapshot,
)

router = APIRouter(prefix="/api/v1/consumables", tags=["consumables"])

AdminUser = Annotated[User, Depends(require_role("admin"))]


def _serialize(consumable: Consumable) -> ConsumableOut:
    return ConsumableOut(
        id=consumable.id,
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


def _ensure_name_unique(
    db: Session, class_id: int, name: str, exclude_id: int | None = None
) -> None:
    query = select(Consumable).where(Consumable.class_id == class_id, Consumable.name == name)
    if exclude_id is not None:
        query = query.where(Consumable.id != exclude_id)
    if db.scalars(query).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"同班级已存在同名耗材「{name}」，请更换名称",
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
    _ensure_name_unique(db, class_id, payload.name.strip())

    consumable = Consumable(
        name=payload.name.strip(),
        spec=payload.spec.strip(),
        unit=payload.unit.strip(),
        location=payload.location.strip(),
        safety_stock=payload.safety_stock,
        current_stock=payload.current_stock,
        class_id=class_id,
        status=STATUS_ENABLED,
    )
    db.add(consumable)
    db.flush()
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
    if "name" in data:
        name = data["name"].strip()
        _ensure_name_unique(db, consumable.class_id, name, exclude_id=consumable.id)
        consumable.name = name
    for field in ("spec", "unit", "location"):
        if field in data:
            setattr(consumable, field, data[field].strip())
    if "safety_stock" in data:
        consumable.safety_stock = data["safety_stock"]

    consumable.updated_at = business_time.now()
    _log(db, admin, "consumable_update", consumable, before)
    db.commit()
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
