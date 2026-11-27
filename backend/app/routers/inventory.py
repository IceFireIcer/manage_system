import json
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.agent import service
from app.database import get_db
from app.deps import CurrentUser, require_role
from app.models import Consumable, OperationLog, Requisition, User
from app.schemas_consumables import STATUS_ENABLED
from app.schemas_inventory import (
    InventoryItemOut,
    InventoryOverviewOut,
    InventorySummary,
    RestockCreate,
    RestockOut,
    SuggestionListOut,
)

router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"])

SHANGHAI_TZ = timezone(timedelta(hours=8))

StaffUser = Annotated[User, Depends(require_role("teacher", "admin"))]
AdminUser = Annotated[User, Depends(require_role("admin"))]


def _today_start_local() -> datetime:
    now = datetime.now(SHANGHAI_TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return today_start.astimezone().replace(tzinfo=None)


@router.get("/overview", response_model_exclude_none=True)
def get_inventory_overview(
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> InventoryOverviewOut:
    consumables = db.scalars(
        select(Consumable).where(
            Consumable.status == STATUS_ENABLED,
            Consumable.class_id == current_user.class_id,
        )
    ).all()

    items: list[InventoryItemOut] = []
    low_stock_count = 0
    for consumable in consumables:
        is_out = consumable.current_stock == 0
        low_stock = (
            consumable.safety_stock > 0 and consumable.current_stock < consumable.safety_stock
        )
        if low_stock:
            low_stock_count += 1
        items.append(
            InventoryItemOut(
                id=consumable.id,
                name=consumable.name,
                spec=consumable.spec,
                unit=consumable.unit,
                location=consumable.location,
                current_stock=consumable.current_stock,
                safety_stock=consumable.safety_stock,
                low_stock=low_stock,
                is_out=is_out,
            )
        )

    items.sort(key=lambda item: (not item.low_stock, not item.is_out, item.name))

    today_start = _today_start_local()
    today_requisitions = (
        db.scalar(
            select(func.count())
            .select_from(Requisition)
            .where(
                Requisition.type == "normal",
                Requisition.status == "active",
                Requisition.created_at >= today_start,
                Requisition.class_id == current_user.class_id,
            )
        )
        or 0
    )

    if current_user.role == "student":
        return InventoryOverviewOut(items=items)

    return InventoryOverviewOut(
        summary=InventorySummary(
            total_kinds=len(consumables),
            low_stock_count=low_stock_count,
            today_requisitions=today_requisitions,
        ),
        items=items,
    )


@router.get("/suggestions")
def list_suggestions(
    db: Annotated[Session, Depends(get_db)],
    _staff: StaffUser,
) -> SuggestionListOut:
    return service.inspect(db)


@router.post("/suggestions/refresh")
def refresh_suggestions(
    db: Annotated[Session, Depends(get_db)],
    _staff: StaffUser,
) -> SuggestionListOut:
    service.clear_cache()
    return service.inspect(db)


@router.post("/restock")
def restock_consumable(
    payload: RestockCreate,
    db: Annotated[Session, Depends(get_db)],
    admin: AdminUser,
) -> RestockOut:
    consumable = db.get(Consumable, payload.consumable_id)
    if consumable is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="耗材不存在"
        )
    if consumable.status != STATUS_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"耗材「{consumable.name}」已停用，不可入库",
        )

    stock_before = consumable.current_stock
    try:
        matched = db.execute(
            update(Consumable)
            .where(
                Consumable.id == payload.consumable_id,
                Consumable.status == STATUS_ENABLED,
            )
            .values(current_stock=Consumable.current_stock + payload.quantity)
        )
        if matched.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"耗材「{consumable.name}」状态已变更，不可入库",
            )
        db.flush()
        db.refresh(consumable)
        stock_after = consumable.current_stock
        consumable.updated_at = datetime.now()

        db.add(
            OperationLog(
                operator_id=admin.id,
                role=admin.role,
                action="restock",
                target_type="consumable",
                target_id=consumable.id,
                before_value=json.dumps(
                    {"consumable_id": consumable.id, "current_stock": stock_before},
                    ensure_ascii=False,
                ),
                after_value=json.dumps(
                    {
                        "consumable_id": consumable.id,
                        "current_stock": stock_after,
                        "quantity": payload.quantity,
                    },
                    ensure_ascii=False,
                ),
                remark=payload.remark,
            )
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise

    service.clear_cache(consumable.id)
    db.refresh(consumable)
    return RestockOut(
        consumable_id=consumable.id,
        consumable_name=consumable.name,
        unit=consumable.unit,
        quantity=payload.quantity,
        stock_before=stock_before,
        stock_after=stock_after,
    )
