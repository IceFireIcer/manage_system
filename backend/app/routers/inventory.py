"""库存总览（T07）：指标卡口径 + 启用耗材低库存排序。"""

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import CurrentUser
from app.models import Consumable, Requisition
from app.schemas_consumables import STATUS_ENABLED
from app.schemas_inventory import InventoryItemOut, InventoryOverviewOut, InventorySummary

router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"])

# Asia/Shanghai 无夏令时，固定 UTC+8 等价（Windows 无系统 tz 数据库，不依赖 tzdata）
SHANGHAI_TZ = timezone(timedelta(hours=8))


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
