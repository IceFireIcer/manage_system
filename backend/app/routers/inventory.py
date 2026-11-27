import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import business_time, inventory_audit, inventory_ledger, restock
from app.agent import service
from app.database import get_db
from app.deps import CurrentUser, require_role
from app.models import (
    Consumable,
    Requisition,
    RequisitionOrder,
    RestockOrder,
    User,
)
from app.schemas_consumables import STATUS_ENABLED
from app.schemas_inventory import (
    AuditAnomalyOut,
    AuditBasisOut,
    AuditEventOut,
    AuditRangeOut,
    InventoryAuditOut,
    InventoryEventOut,
    InventoryItemOut,
    InventoryLedgerOut,
    InventoryOverviewOut,
    InventorySummary,
    RestockCreate,
    RestockOut,
    SuggestionListOut,
)
from app.schemas_restock import (
    RestockCorrectionCreate,
    RestockCorrectionOut,
    RestockItemIn,
    RestockOrderCreate,
    RestockReceiptOut,
    RestockRequestStatusOut,
    SuggestionDispositionOut,
    SuggestionDispositionUpdate,
)

router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"])

StaffUser = Annotated[User, Depends(require_role("teacher", "admin"))]
AdminUser = Annotated[User, Depends(require_role("admin"))]


def _resolve_order_numbers(db: Session, events) -> tuple[dict, dict, dict]:
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
    restock_ids = {
        event.source_id
        for event in events
        if event.source_type == inventory_ledger.SOURCE_RESTOCK_ORDER
        and event.source_id is not None
    }
    restock_numbers = (
        {
            order.id: order.order_no
            for order in db.scalars(
                select(RestockOrder).where(RestockOrder.id.in_(restock_ids))
            )
        }
        if restock_ids
        else {}
    )
    requisition_ids = {
        event.requisition_id
        for event in events
        if event.source_type
        in (
            inventory_ledger.SOURCE_REQUISITION_REVERSAL,
            inventory_ledger.SOURCE_REQUISITION_REENTRY,
        )
        and event.requisition_id is not None
    }
    requisition_numbers = (
        {
            record.id: record.order_no
            for record in db.scalars(
                select(Requisition).where(Requisition.id.in_(requisition_ids))
            )
            if record.order_no
        }
        if requisition_ids
        else {}
    )
    return requisition_numbers, order_numbers, restock_numbers


def _event_order_no(
    event, requisition_numbers: dict, order_numbers: dict, restock_numbers: dict
) -> str | None:
    if event.source_type in (
        inventory_ledger.SOURCE_REQUISITION_REVERSAL,
        inventory_ledger.SOURCE_REQUISITION_REENTRY,
    ):
        return requisition_numbers.get(event.requisition_id)
    if event.source_type == inventory_ledger.SOURCE_REQUISITION_ORDER:
        return order_numbers.get(event.source_id)
    if event.source_type == inventory_ledger.SOURCE_RESTOCK_ORDER:
        return restock_numbers.get(event.source_id)
    return None


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
    requisition_numbers, order_numbers, restock_numbers = _resolve_order_numbers(
        db, events
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
                order_no=_event_order_no(
                    event,
                    requisition_numbers,
                    order_numbers,
                    restock_numbers,
                ),
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


@router.get("/audit")
def get_inventory_audit(
    db: Annotated[Session, Depends(get_db)],
    staff: StaffUser,
    consumable_id: Annotated[int, Query(gt=0)],
    posting_cutoff: Annotated[datetime | None, Query()] = None,
    business_cutoff: Annotated[datetime | None, Query()] = None,
) -> InventoryAuditOut:
    consumable = db.get(Consumable, consumable_id)
    if consumable is None or consumable.class_id != staff.class_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="耗材不存在"
        )

    events = inventory_ledger.list_events(db, consumable_id)
    reconciliation = inventory_audit.reconcile(events, consumable.current_stock)
    requisition_numbers, order_numbers, restock_numbers = _resolve_order_numbers(
        db, events
    )
    by_id = {event.id: event for event in events}

    def to_out(
        event, stock_before: int | None = None, stock_after: int | None = None
    ) -> AuditEventOut:
        order_no = _event_order_no(
            event, requisition_numbers, order_numbers, restock_numbers
        )
        return AuditEventOut(
            id=event.id,
            event_type=event.event_type,
            source_type=event.source_type,
            source_id=event.source_id,
            requisition_id=event.requisition_id,
            order_no=order_no,
            line_no=event.line_no,
            business_at=event.business_at,
            posted_at=event.posted_at,
            delta=event.delta,
            stock_before=event.stock_before if stock_before is None else stock_before,
            stock_after=event.stock_after if stock_after is None else stock_after,
            exec_seq=event.exec_seq,
            operator_name=event.operator_name,
            unit_snapshot=event.unit_snapshot,
            evidence_status=event.evidence_status,
            can_open_source=(
                event.requisition_id is not None or order_no is not None
            ),
        )

    posting = inventory_audit.posting_view(events, posting_cutoff)
    business = inventory_audit.business_view(events, business_cutoff)
    business_events: list[AuditEventOut] = []
    if business.opening is not None:
        business_events.append(to_out(business.opening))
    business_events.extend(
        to_out(
            row.event,
            stock_before=row.stock_before,
            stock_after=row.stock_after,
        )
        for row in business.events
    )

    first_anomaly: AuditAnomalyOut | None = None
    anomaly_event = by_id.get(reconciliation.first_anomaly_event_id)
    if anomaly_event is not None and reconciliation.first_anomaly_reason:
        first_anomaly = AuditAnomalyOut(
            event_id=anomaly_event.id,
            exec_seq=anomaly_event.exec_seq,
            at=anomaly_event.posted_at,
            reason=reconciliation.first_anomaly_reason,
        )

    affected_range: AuditRangeOut | None = None
    start_event = by_id.get(reconciliation.affected_start_event_id)
    end_event = by_id.get(reconciliation.affected_end_event_id)
    if start_event is not None and end_event is not None:
        affected_range = AuditRangeOut(
            start_event_id=start_event.id,
            end_event_id=end_event.id,
            start_exec_seq=start_event.exec_seq,
            end_exec_seq=end_event.exec_seq,
            start_at=start_event.posted_at,
            end_at=end_event.posted_at,
        )

    return InventoryAuditOut(
        consumable_id=consumable.id,
        name=consumable.name,
        unit=consumable.unit,
        current_stock=consumable.current_stock,
        opening_stock=reconciliation.opening_stock,
        opening_event_id=reconciliation.opening_event_id,
        opening_source=reconciliation.opening_source,
        ledger_end=reconciliation.ledger_end,
        diff=reconciliation.diff,
        evidence_status=reconciliation.evidence_status,
        evidence_label=inventory_audit.EVIDENCE_LABELS.get(
            reconciliation.evidence_status, reconciliation.evidence_status
        ),
        evidence_reasons=list(reconciliation.evidence_reasons),
        first_anomaly=first_anomaly,
        affected_range=affected_range,
        posting=AuditBasisOut(
            basis=inventory_audit.BASIS_POSTING,
            label=inventory_audit.BASIS_LABELS[inventory_audit.BASIS_POSTING],
            cutoff=posting_cutoff,
            stock_at_cutoff=posting.stock_at_cutoff,
            ledger_end=(
                posting.events[-1].stock_after if posting.events else None
            ),
            events=[to_out(event) for event in posting.events],
        ),
        business=AuditBasisOut(
            basis=inventory_audit.BASIS_BUSINESS,
            label=inventory_audit.BASIS_LABELS[inventory_audit.BASIS_BUSINESS],
            cutoff=business_cutoff,
            stock_at_cutoff=business.stock_at_cutoff,
            ledger_end=(
                business_events[-1].stock_after if business_events else None
            ),
            events=business_events,
        ),
    )


@router.post("/restock")
def restock_consumable(
    payload: RestockCreate,
    db: Annotated[Session, Depends(get_db)],
    admin: AdminUser,
) -> RestockOut:
    order = restock.create_restock_order(
        db,
        items=[
            RestockItemIn(
                consumable_id=payload.consumable_id, quantity=payload.quantity
            )
        ],
        operator=admin,
        request_id=payload.request_id,
        received_at=payload.received_at,
        source=payload.source or "manual",
        remark=payload.remark,
    )
    receipt = restock.receipt_from_order(db, order)
    if not receipt.lines:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="入库结果不完整，请凭请求标识查询处理状态",
        )
    line = receipt.lines[0]
    service.clear_cache(line.consumable_id)
    return RestockOut(
        consumable_id=line.consumable_id,
        consumable_name=line.consumable_name,
        unit=line.unit,
        quantity=line.quantity,
        stock_before=line.stock_before,
        stock_after=line.stock_after,
        order_no=receipt.order_no,
        request_id=receipt.request_id,
        source=receipt.source,
        business_at=receipt.business_at,
        posted_at=receipt.posted_at,
    )


@router.post("/restock/orders")
def create_restock_order(
    payload: RestockOrderCreate,
    db: Annotated[Session, Depends(get_db)],
    admin: AdminUser,
) -> JSONResponse:
    request_id = payload.request_id or uuid.uuid4().hex
    existing = restock.get_order_by_request(db, request_id)
    order = restock.create_restock_order(
        db,
        items=payload.items,
        operator=admin,
        request_id=request_id,
        received_at=payload.received_at,
        source=payload.source,
        remark=payload.remark,
    )
    service.clear_cache()
    if order.status == "submitted":
        created = existing is None
        return JSONResponse(
            status_code=(
                status.HTTP_201_CREATED if created else status.HTTP_200_OK
            ),
            content=jsonable_encoder(restock.receipt_from_order(db, order)),
        )
    if order.status == "rejected":
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=jsonable_encoder(restock.request_status(db, order)),
        )
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=jsonable_encoder(restock.request_status(db, order)),
    )


@router.get("/restock/requests/{request_id}")
def get_restock_request_status(
    request_id: str,
    db: Annotated[Session, Depends(get_db)],
    staff: StaffUser,
) -> RestockRequestStatusOut:
    order = restock.get_order_by_request(db, request_id)
    if order is None or order.class_id != staff.class_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="请求不存在"
        )
    return restock.request_status(db, order)


@router.get("/restock/orders/{order_no}")
def get_restock_receipt(
    order_no: str,
    db: Annotated[Session, Depends(get_db)],
    staff: StaffUser,
) -> RestockReceiptOut:
    order = restock.get_order_by_no(db, order_no)
    if order is None or order.class_id != staff.class_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="入库单不存在"
        )
    if order.status != "submitted":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="回执尚未生成，请按请求标识查询处理状态",
        )
    return restock.receipt_from_order(db, order)


@router.post("/restock/orders/{order_no}/corrections")
def correct_restock_order(
    order_no: str,
    payload: RestockCorrectionCreate,
    db: Annotated[Session, Depends(get_db)],
    admin: AdminUser,
) -> RestockCorrectionOut:
    order = restock.get_order_by_no(db, order_no)
    if order is None or order.class_id != admin.class_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="入库单不存在"
        )
    if order.status != "submitted":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅已提交的入库单可以纠错",
        )
    result = restock.correct_restock_order(
        db, order=order, payload=payload, operator=admin
    )
    service.clear_cache(result.consumable_id)
    return result


@router.patch("/suggestions/{consumable_id}/disposition")
def update_suggestion_disposition(
    consumable_id: int,
    payload: SuggestionDispositionUpdate,
    db: Annotated[Session, Depends(get_db)],
    admin: AdminUser,
) -> SuggestionDispositionOut:
    consumable = db.get(Consumable, consumable_id)
    if consumable is None or consumable.class_id != admin.class_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="耗材不存在"
        )
    result = restock.update_disposition(
        db,
        consumable=consumable,
        operator=admin,
        next_status=payload.status,
        note=payload.note,
    )
    service.clear_cache(consumable_id)
    return result
