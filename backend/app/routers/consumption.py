from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import consumption as consumption_core
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
    window: Annotated[int, Query()] = consumption_core.DEFAULT_WINDOW,
) -> ConsumptionSummaryOut:
    if window not in consumption_core.VALID_WINDOWS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="window 参数仅支持 7 或 30",
        )
    start = consumption_core.window_start(window)
    end = consumption_core.window_end()

    items: list[ConsumptionItemOut] = []
    for consumable_id, quantity in consumption_core.net_by_consumable(db, start, end):
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
                daily_rate=consumption_core.daily_rate(quantity, window),
            )
        )

    total_quantity = sum(item.quantity for item in items)
    return ConsumptionSummaryOut(
        window=window,
        items=items,
        totals=ConsumptionTotalsOut(
            quantity=total_quantity,
            daily_rate=consumption_core.daily_rate(total_quantity, window),
        ),
    )


@router.get("/audit")
def get_consumption_audit(
    db: Annotated[Session, Depends(get_db)],
    _staff: StaffUser,
    consumable_id: Annotated[int, Query()],
) -> ConsumptionAuditOut:
    consumable = db.get(Consumable, consumable_id)
    if consumable is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="耗材不存在"
        )

    events = consumption_core.audit_events(db, consumable_id)
    reconstruction = consumption_core.reconstruct_stock(events)
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
