import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import business_time, inventory_ledger
from app.agent import service
from app.database import get_db
from app.deps import CurrentUser, require_role
from app.models import Consumable, OperationLog, Requisition, RequisitionOrder, User
from app.schemas_consumables import STATUS_ENABLED
from app.schemas_inventory import (
    InventoryEventOut,
    InventoryItemOut,
    InventoryLedgerOut,
    InventoryOverviewOut,
    InventorySummary,
    RestockCreate,
    RestockOut,
    SuggestionListOut,
)

router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"])

StaffUser = Annotated[User, Depends(require_role("teacher", "admin"))]
AdminUser = Annotated[User, Depends(require_role("admin"))]


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

    today_start = business_time.day_start(business_time.today())
    tomorrow_start = business_time.next_day_start(business_time.today())
    today_requisitions = (
        db.scalar(
            select(func.count())
            .select_from(Requisition)
            .where(
                Requisition.type == "normal",
                Requisition.status == "active",
                Requisition.created_at >= today_start,
                Requisition.created_at < tomorrow_start,
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


@router.get("/ledger")
def get_inventory_ledger(
    db: Annotated[Session, Depends(get_db)],
    staff: StaffUser,
    consumable_id: Annotated[int, Query(gt=0)],
) -> InventoryLedgerOut:
    consumable = db.get(Consumable, consumable_id)
    if consumable is None or consumable.class_id != staff.class_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="耗材不存在"
        )

    events = inventory_ledger.list_events(db, consumable_id)
    order_ids = {
        event.source_id
        for event in events
        if event.source_type == inventory_ledger.SOURCE_REQUISITION_ORDER
        and event.source_id is not None
    }
    order_numbers = (
        {
            order.id: order.order_no
            for order in db.scalars(
                select(RequisitionOrder).where(RequisitionOrder.id.in_(order_ids))
            )
        }
        if order_ids
        else {}
    )

    consistent = bool(events) and events[-1].stock_after == consumable.current_stock
    if consistent:
        previous_after: int | None = None
        for event in events:
            if previous_after is not None and event.stock_before != previous_after:
                consistent = False
                break
            previous_after = event.stock_after
    opening_stock = (
        events[0].stock_after
        if events and events[0].event_type == inventory_ledger.EVENT_OPENING
        else None
    )

    return InventoryLedgerOut(
        consumable_id=consumable.id,
        name=consumable.name,
        unit=consumable.unit,
        current_stock=consumable.current_stock,
        opening_stock=opening_stock,
        consistent=consistent,
        events=[
            InventoryEventOut(
                id=event.id,
                event_type=event.event_type,
                delta=event.delta,
                stock_before=event.stock_before,
                stock_after=event.stock_after,
                exec_seq=event.exec_seq,
                business_at=event.business_at,
                posted_at=event.posted_at,
                source_type=event.source_type,
                source_id=event.source_id,
                order_no=order_numbers.get(event.source_id),
                requisition_id=event.requisition_id,
                line_no=event.line_no,
                operator_id=event.operator_id,
                operator_name=event.operator_name,
                operator_role=event.operator_role,
                unit_snapshot=event.unit_snapshot,
                evidence_status=event.evidence_status,
            )
            for event in events
        ],
    )


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
        consumable.updated_at = business_time.now()

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
