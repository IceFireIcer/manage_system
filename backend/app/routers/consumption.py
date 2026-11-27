"""消耗汇总与库存反推审计接口（T09）：教师/班委可见。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import consumption
from app.database import get_db
from app.deps import require_role
from app.models import Consumable, User
from app.schemas_consumption import (
    AuditEventOut,
    ConsumptionAuditOut,
    ConsumptionItemOut,
    ConsumptionSummaryOut,
    ConsumptionTotalsOut,
)

router = APIRouter(prefix="/api/v1/consumption", tags=["consumption"])

StaffUser = Annotated[User, Depends(require_role("teacher", "admin"))]


@router.get("/summary")
def get_consumption_summary(
    db: Annotated[Session, Depends(get_db)],
    _staff: StaffUser,
    window: Annotated[int, Query()] = consumption.DEFAULT_WINDOW,
) -> ConsumptionSummaryOut:
    """近 7/30 天净消耗汇总：仅列窗口内出现过领用/红冲流水的耗材。"""
    if window not in consumption.VALID_WINDOWS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="window 参数仅支持 7 或 30",
        )
    start = consumption.window_start(window)
    end = consumption.window_end()

    items: list[ConsumptionItemOut] = []
    for consumable_id, quantity in consumption.net_by_consumable(db, start, end):
        consumable = db.get(Consumable, consumable_id)
        if consumable is None:
            continue
        items.append(
            ConsumptionItemOut(
                consumable_id=consumable.id,
                name=consumable.name,
                spec=consumable.spec,
                unit=consumable.unit,
                quantity=quantity,
                daily_rate=consumption.daily_rate(quantity, window),
            )
        )

    total_quantity = sum(item.quantity for item in items)
    return ConsumptionSummaryOut(
        window=window,
        items=items,
        totals=ConsumptionTotalsOut(
            quantity=total_quantity,
            daily_rate=consumption.daily_rate(total_quantity, window),
        ),
    )


@router.get("/audit")
def get_consumption_audit(
    db: Annotated[Session, Depends(get_db)],
    _staff: StaffUser,
    consumable_id: Annotated[int, Query()],
) -> ConsumptionAuditOut:
    """库存反推审计：正序变动序列 + 期初库存 + 与各条 stock_after 的一致性。"""
    consumable = db.get(Consumable, consumable_id)
    if consumable is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="耗材不存在"
        )

    events = consumption.audit_events(db, consumable_id)
    reconstruction = consumption.reconstruct_stock(events)
    return ConsumptionAuditOut(
        consumable_id=consumable.id,
        name=consumable.name,
        spec=consumable.spec,
        unit=consumable.unit,
        current_stock=consumable.current_stock,
        opening_stock=reconstruction.opening_stock,
        consistent=reconstruction.consistent,
        events=[
            AuditEventOut(
                time=event["time"],
                source=event["source"],
                delta=event["delta"],
                stock_after=event["stock_after"],
            )
            for event in events
        ],
    )
