"""库存总览（T07）、巡检建议与处置入库（T08）。"""

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

# Asia/Shanghai 无夏令时，固定 UTC+8 等价（Windows 无系统 tz 数据库，不依赖 tzdata）
SHANGHAI_TZ = timezone(timedelta(hours=8))

StaffUser = Annotated[User, Depends(require_role("teacher", "admin"))]
AdminUser = Annotated[User, Depends(require_role("admin"))]


def _today_start_local() -> datetime:
    """Asia/Shanghai 自然日零点对应的本地 naive 时间（流水按本地 naive 时间存储）。"""
    now = datetime.now(SHANGHAI_TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return today_start.astimezone().replace(tzinfo=None)


@router.get("/overview")
def get_inventory_overview(
    db: Annotated[Session, Depends(get_db)],
    _current_user: CurrentUser,
) -> InventoryOverviewOut:
    """库存总览：三角色可见。

    - summary：启用耗材类数、低库存预警数、今日领用笔数（Asia/Shanghai 自然日）。
    - items：全部启用耗材，低库存与缺货置前（low_stock desc, is_out desc, name asc）。
    """
    consumables = db.scalars(
        select(Consumable).where(Consumable.status == STATUS_ENABLED)
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
            )
        )
        or 0
    )

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
    """巡检补货建议：教师/班委可见，结果按耗材缓存 10 分钟，不落库。"""
    return service.inspect(db)


@router.post("/suggestions/refresh")
def refresh_suggestions(
    db: Annotated[Session, Depends(get_db)],
    _staff: StaffUser,
) -> SuggestionListOut:
    """重新巡检：清空缓存后重算全部启用耗材。"""
    service.clear_cache()
    return service.inspect(db)


@router.post("/restock")
def restock_consumable(
    payload: RestockCreate,
    db: Annotated[Session, Depends(get_db)],
    admin: AdminUser,
) -> RestockOut:
    """补货入库（仅班委）：单事务原子加库存并写操作日志，成功后清除该耗材巡检缓存。"""
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
