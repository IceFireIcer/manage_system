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
    ConsumptionUnitTotalOut,
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
    unit_rows: dict[str, dict[str, int]] = {}
    for flow in consumption_core.flow_by_consumable(db, start, end):
        consumable = db.get(Consumable, flow.consumable_id)
        if consumable is None:
            continue
        net_quantity = flow.net
        items.append(
            ConsumptionItemOut(
                consumable_id=consumable.id,
                name=consumable.name,
                spec=consumable.spec,
                unit=consumable.unit,
                issued_quantity=flow.issued,
                reversed_quantity=flow.reversed_quantity,
                cross_period_reversed_quantity=flow.cross_period_reversed,
                net_quantity=net_quantity,
                quantity=net_quantity,
                daily_rate=consumption_core.daily_rate(net_quantity, window),
            )
        )
        unit_row = unit_rows.setdefault(
            consumable.unit,
            {
                "issued_quantity": 0,
                "reversed_quantity": 0,
                "cross_period_reversed_quantity": 0,
                "net_quantity": 0,
                "consumables": 0,
            },
        )
        unit_row["issued_quantity"] += flow.issued
        unit_row["reversed_quantity"] += flow.reversed_quantity
        unit_row["cross_period_reversed_quantity"] += flow.cross_period_reversed
        unit_row["net_quantity"] += net_quantity
        unit_row["consumables"] += 1

    unit_totals = [
        ConsumptionUnitTotalOut(
            unit=unit,
            issued_quantity=values["issued_quantity"],
            reversed_quantity=values["reversed_quantity"],
            cross_period_reversed_quantity=values[
                "cross_period_reversed_quantity"
            ],
            net_quantity=values["net_quantity"],
            daily_rate=consumption_core.daily_rate(
                values["net_quantity"], window
            ),
            consumables=values["consumables"],
        )
        for unit, values in sorted(
            unit_rows.items(),
            key=lambda entry: (-entry[1]["net_quantity"], entry[0]),
        )
    ]

    return ConsumptionSummaryOut(
        window=window,
        items=items,
        unit_totals=unit_totals,
        provisioning_scope=consumption_core.PROVISIONING_SCOPE,
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
