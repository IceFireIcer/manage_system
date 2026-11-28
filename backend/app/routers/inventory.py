import json
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import business_time, inventory_audit, inventory_count, inventory_ledger, restock
from app.agent import service
from app.database import get_db
from app.deps import CurrentUser, require_role
from app.models import (
    Consumable,
    InventoryCount,
    InventoryCountLine,
    InventoryEvent,
    Requisition,
    RequisitionCorrection,
    RequisitionOrder,
    RequisitionReturn,
    RequisitionScrap,
    RestockCorrection,
    RestockLine,
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
    InventoryCountCreate,
    InventoryCountLinesUpdate,
    InventoryCountOut,
    InventoryEventOut,
    InventoryEventTraceOut,
    InventoryItemOut,
    InventoryLedgerOut,
    InventoryOverviewOut,
    InventorySummary,
    RestockCreate,
    RestockOut,
    SuggestionListOut,
    TraceDocumentOut,
    TraceEventOut,
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
    event,
    requisition_numbers: dict,
    order_numbers: dict,
    restock_numbers: dict,
    return_numbers: dict | None = None,
    scrap_numbers: dict | None = None,
    count_numbers: dict | None = None,
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
    if event.source_type == inventory_ledger.SOURCE_REQUISITION_RETURN:
        return (return_numbers or {}).get(event.source_id)
    if event.source_type == inventory_ledger.SOURCE_REQUISITION_SCRAP:
        return (scrap_numbers or {}).get(event.source_id)
    if event.source_type == inventory_ledger.SOURCE_INVENTORY_COUNT:
        return (count_numbers or {}).get(event.source_id)
    return None


def _source_ids(events, source_type: str) -> set[int]:
    return {
        event.source_id
        for event in events
        if event.source_type == source_type and event.source_id is not None
    }


def _resolve_count_numbers(db: Session, events) -> dict:
    count_ids = _source_ids(events, inventory_ledger.SOURCE_INVENTORY_COUNT)
    if not count_ids:
        return {}
    return {
        record.id: record.order_no
        for record in db.scalars(
            select(InventoryCount).where(InventoryCount.id.in_(count_ids))
        )
    }


def _resolve_return_numbers(db: Session, events) -> dict:
    return_ids = _source_ids(events, inventory_ledger.SOURCE_REQUISITION_RETURN)
    if not return_ids:
        return {}
    return {
        record.id: record.order_no
        for record in db.scalars(
            select(RequisitionReturn).where(RequisitionReturn.id.in_(return_ids))
        )
    }


def _resolve_scrap_numbers(db: Session, events) -> dict:
    scrap_ids = _source_ids(events, inventory_ledger.SOURCE_REQUISITION_SCRAP)
    if not scrap_ids:
        return {}
    return {
        record.id: record.order_no
        for record in db.scalars(
            select(RequisitionScrap).where(RequisitionScrap.id.in_(scrap_ids))
        )
    }


TRACE_EVENT_LABELS = {
    inventory_ledger.EVENT_OPENING: "期初建账",
    inventory_ledger.EVENT_REQUISITION: "领用出库",
    inventory_ledger.EVENT_REVERSAL: "红冲回补",
    inventory_ledger.EVENT_RESTOCK: "入库上架",
    inventory_ledger.EVENT_RESTOCK_CORRECTION: "入库纠错扣回",
    inventory_ledger.EVENT_RETURN: "退库回补",
    inventory_ledger.EVENT_SCRAP: "报损核销",
    inventory_ledger.EVENT_INVENTORY_ADJUSTMENT: "盘点调整",
}

TRACE_STATUS_LABELS = {
    "requisition": {"active": "有效", "reversed": "已红冲"},
    "order": {"processing": "处理中", "submitted": "已提交", "rejected": "已驳回"},
    "correction": {"submitted": "待处理", "accepted": "已处理", "rejected": "已驳回"},
    "count": {"open": "盘点中", "posted": "已入账"},
}

TRACE_KIND_RANK = {
    "requisition_correction": 10,
    "requisition_order": 11,
    "requisition": 12,
    "requisition_reversal": 13,
    "requisition_reentry": 14,
    "requisition_return": 15,
    "requisition_scrap": 16,
    "restock_order": 20,
    "restock_correction": 21,
    "inventory_count": 30,
    "inventory_count_line": 31,
    "requisition_backfill": 32,
}


def _trace_status_label(group: str, value: str | None) -> str | None:
    if value is None:
        return None
    return TRACE_STATUS_LABELS.get(group, {}).get(value)


def _trace_doc(
    kind: str,
    kind_label: str,
    ref_id: int,
    at: datetime,
    *,
    order_no: str | None = None,
    quantity: int | None = None,
    unit: str | None = None,
    status: str | None = None,
    status_label: str | None = None,
    operator_name: str | None = None,
    reason: str | None = None,
) -> TraceDocumentOut:
    return TraceDocumentOut(
        kind=kind,
        kind_label=kind_label,
        order_no=order_no,
        quantity=quantity,
        unit=unit,
        status=status,
        status_label=status_label,
        ref_id=ref_id,
        at=at,
        operator_name=operator_name,
        reason=reason,
    )


def _receipt_line(order, consumable_id: int, line_no: int | None):
    if not order.receipt_json:
        return None
    try:
        rows = json.loads(order.receipt_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(rows, list):
        return None
    candidates = [
        row
        for row in rows
        if isinstance(row, dict) and row.get("consumable_id") == consumable_id
    ]
    if not candidates:
        return None
    if line_no is not None:
        for row in candidates:
            if row.get("line_no") == line_no:
                return row
    return candidates[0]


def _requisition_chain(db: Session, class_id: int, event) -> dict[int, Requisition]:
    records: dict[int, Requisition] = {}

    def keep(record: Requisition | None) -> bool:
        if record is None or record.class_id != class_id:
            return False
        records[record.id] = record
        return True

    if event.requisition_id is not None:
        keep(db.get(Requisition, event.requisition_id))
    if event.source_type in (
        inventory_ledger.SOURCE_REQUISITION_REVERSAL,
        inventory_ledger.SOURCE_REQUISITION_REENTRY,
    ) and event.source_id is not None:
        keep(db.get(Requisition, event.source_id))

    while records:
        ids = set(records)
        changed = False
        parent_ids = {
            parent
            for record in records.values()
            for parent in (record.reversal_of, record.reentry_of)
            if parent is not None and parent not in records
        }
        for parent_id in parent_ids:
            if keep(db.get(Requisition, parent_id)):
                changed = True
        related = db.scalars(
            select(Requisition).where(
                (Requisition.reversal_of.in_(ids))
                | (Requisition.reentry_of.in_(ids))
            )
        ).all()
        for record in related:
            if record.id not in records and keep(record):
                changed = True
        if not changed:
            break
    return records


def _trace_correction_target(
    db: Session, records: dict[int, Requisition], record: Requisition
) -> int:
    if record.type == "reversal" and record.reversal_of is not None:
        return record.reversal_of
    if record.reentry_of is not None:
        reversal = records.get(record.reentry_of) or db.get(
            Requisition, record.reentry_of
        )
        if reversal is not None:
            return reversal.reversal_of or reversal.id
    return record.id


def _requisition_docs(
    db: Session,
    class_id: int,
    event,
    records: dict[int, Requisition],
    order: RequisitionOrder | None,
) -> list[TraceDocumentOut]:
    docs: list[TraceDocumentOut] = []
    if order is not None:
        line = _receipt_line(order, event.consumable_id, event.line_no)
        docs.append(
            _trace_doc(
                "requisition_order",
                "领用单",
                order.id,
                order.posted_at or order.business_at,
                order_no=order.order_no,
                quantity=line.get("quantity") if line else None,
                unit=(line.get("unit") if line else None) or event.unit_snapshot,
                status=order.status,
                status_label=_trace_status_label("order", order.status),
                operator_name=order.operator_name,
                reason=order.remark,
            )
        )
    for record in records.values():
        if record.type == "reversal":
            kind, kind_label = "requisition_reversal", "红冲单"
        elif record.reentry_of is not None:
            kind, kind_label = "requisition_reentry", "补领单"
        else:
            kind, kind_label = "requisition", "领用流水"
        docs.append(
            _trace_doc(
                kind,
                kind_label,
                record.id,
                record.created_at,
                order_no=record.order_no,
                quantity=record.quantity,
                unit=record.unit_snapshot,
                status=record.status,
                status_label=_trace_status_label("requisition", record.status),
                operator_name=record.operator_name or record.handler_name,
                reason=record.reversal_reason
                or record.backfill_reason
                or record.remark,
            )
        )
    return docs


def _requisition_extra_docs(
    db: Session, class_id: int, records: dict[int, Requisition]
) -> list[TraceDocumentOut]:
    if not records:
        return []
    ids = set(records)
    docs: list[TraceDocumentOut] = []
    target_ids = {
        _trace_correction_target(db, records, record) for record in records.values()
    }
    corrections = db.scalars(
        select(RequisitionCorrection)
        .where(
            RequisitionCorrection.requisition_id.in_(target_ids),
            RequisitionCorrection.class_id == class_id,
        )
        .order_by(RequisitionCorrection.created_at, RequisitionCorrection.id)
    ).all()
    for record in corrections:
        docs.append(
            _trace_doc(
                "requisition_correction",
                "纠错申请",
                record.id,
                record.created_at,
                status=record.status,
                status_label=_trace_status_label("correction", record.status),
                operator_name=record.applicant_name,
                reason=record.reason or record.handling_note,
            )
        )
    returns = db.scalars(
        select(RequisitionReturn)
        .where(
            RequisitionReturn.requisition_id.in_(ids),
            RequisitionReturn.class_id == class_id,
        )
        .order_by(RequisitionReturn.posted_at, RequisitionReturn.id)
    ).all()
    for record in returns:
        origin = records.get(record.requisition_id)
        docs.append(
            _trace_doc(
                "requisition_return",
                "退库单",
                record.id,
                record.posted_at,
                order_no=record.order_no,
                quantity=record.quantity,
                unit=origin.unit_snapshot if origin is not None else None,
                status="effective",
                status_label="已退库",
                operator_name=record.operator_name,
                reason=record.reason or record.remark,
            )
        )
    scraps = db.scalars(
        select(RequisitionScrap)
        .where(
            RequisitionScrap.requisition_id.in_(ids),
            RequisitionScrap.class_id == class_id,
        )
        .order_by(RequisitionScrap.posted_at, RequisitionScrap.id)
    ).all()
    for record in scraps:
        origin = records.get(record.requisition_id)
        docs.append(
            _trace_doc(
                "requisition_scrap",
                "报损单",
                record.id,
                record.posted_at,
                order_no=record.order_no,
                quantity=record.quantity,
                unit=origin.unit_snapshot if origin is not None else None,
                status="effective",
                status_label="已报损",
                operator_name=record.operator_name,
                reason=record.reason or record.remark,
            )
        )
    return docs


def _restock_docs(
    db: Session, class_id: int, event
) -> list[TraceDocumentOut]:
    if (
        event.source_type != inventory_ledger.SOURCE_RESTOCK_ORDER
        or event.source_id is None
    ):
        return []
    order = db.get(RestockOrder, event.source_id)
    if order is None or order.class_id != class_id:
        return []
    lines = db.scalars(
        select(RestockLine)
        .where(RestockLine.order_id == order.id)
        .order_by(RestockLine.line_no, RestockLine.id)
    ).all()
    matching = [
        line
        for line in lines
        if line.consumable_id == event.consumable_id
        and (event.line_no is None or line.line_no == event.line_no)
    ]
    if matching:
        quantity = sum(line.quantity for line in matching)
        unit = matching[0].unit
    else:
        quantity = abs(event.delta) or None
        unit = event.unit_snapshot
    docs = [
        _trace_doc(
            "restock_order",
            "入库单",
            order.id,
            order.posted_at or order.business_at,
            order_no=order.order_no,
            quantity=quantity,
            unit=unit,
            status=order.status,
            status_label=_trace_status_label("order", order.status),
            operator_name=order.operator_name,
            reason=order.remark or order.source,
        )
    ]
    line_nos = {line.line_no for line in matching}
    query = select(RestockCorrection).where(RestockCorrection.order_id == order.id)
    if line_nos:
        query = query.where(RestockCorrection.line_no.in_(line_nos))
    corrections = db.scalars(
        query.order_by(RestockCorrection.posted_at, RestockCorrection.id)
    ).all()
    for record in corrections:
        docs.append(
            _trace_doc(
                "restock_correction",
                "入库纠错",
                record.id,
                record.posted_at,
                order_no=order.order_no,
                quantity=record.quantity,
                unit=unit,
                status="effective",
                status_label="已扣回",
                operator_name=record.operator_name,
                reason=record.reason or record.remark,
            )
        )
    return docs


def _count_docs(
    db: Session, class_id: int, event
) -> list[TraceDocumentOut]:
    if (
        event.source_type != inventory_ledger.SOURCE_INVENTORY_COUNT
        or event.source_id is None
    ):
        return []
    count = db.get(InventoryCount, event.source_id)
    if count is None or count.class_id != class_id:
        return []
    docs = [
        _trace_doc(
            "inventory_count",
            "盘点单",
            count.id,
            count.posted_at or count.count_at,
            order_no=count.order_no,
            status=count.status,
            status_label=_trace_status_label("count", count.status),
            operator_name=count.counter_name,
            reason=count.remark or count.warning,
        )
    ]
    lines = db.scalars(
        select(InventoryCountLine)
        .where(InventoryCountLine.count_id == count.id)
        .order_by(InventoryCountLine.line_no, InventoryCountLine.id)
    ).all()
    for line in lines:
        if line.consumable_id != event.consumable_id:
            continue
        if event.line_no is not None and line.line_no != event.line_no:
            continue
        docs.append(
            _trace_doc(
                "inventory_count_line",
                "盘点明细",
                line.id,
                count.posted_at or count.count_at,
                order_no=count.order_no,
                quantity=line.counted_qty,
                unit=line.unit,
                status=count.status,
                status_label=_trace_status_label("count", count.status),
                operator_name=count.counter_name,
                reason=line.reason,
            )
        )
    backfills = db.scalars(
        select(Requisition)
        .where(
            Requisition.linked_count_id == count.id,
            Requisition.consumable_id == event.consumable_id,
            Requisition.class_id == class_id,
        )
        .order_by(Requisition.created_at, Requisition.id)
    ).all()
    for record in backfills:
        docs.append(
            _trace_doc(
                "requisition_backfill",
                "盘点补录领用",
                record.id,
                record.created_at,
                order_no=record.order_no,
                quantity=record.quantity,
                unit=record.unit_snapshot,
                status=record.status,
                status_label=_trace_status_label("requisition", record.status),
                operator_name=record.operator_name or record.handler_name,
                reason=record.backfill_reason or record.remark,
            )
        )
    return docs


def _trace_reason(db: Session, event) -> tuple[str | None, str | None]:
    if event.source_type == inventory_ledger.SOURCE_REQUISITION_ORDER:
        order = (
            db.get(RequisitionOrder, event.source_id)
            if event.source_id is not None
            else None
        )
        record = (
            db.get(Requisition, event.requisition_id)
            if event.requisition_id is not None
            else None
        )
        return None, (record.remark if record is not None else None) or (
            order.remark if order is not None else None
        )
    if event.source_type == inventory_ledger.SOURCE_REQUISITION_REVERSAL:
        record = (
            db.get(Requisition, event.requisition_id)
            if event.requisition_id is not None
            else None
        )
        if record is None:
            return None, None
        return record.reversal_reason, record.remark
    if event.source_type == inventory_ledger.SOURCE_REQUISITION_REENTRY:
        record = (
            db.get(Requisition, event.source_id)
            if event.source_id is not None
            else None
        )
        if record is None:
            return None, None
        return None, record.remark
    if event.source_type == inventory_ledger.SOURCE_REQUISITION_RETURN:
        record = (
            db.get(RequisitionReturn, event.source_id)
            if event.source_id is not None
            else None
        )
        if record is None:
            return None, None
        return record.reason, record.remark
    if event.source_type == inventory_ledger.SOURCE_REQUISITION_SCRAP:
        record = (
            db.get(RequisitionScrap, event.source_id)
            if event.source_id is not None
            else None
        )
        if record is None:
            return None, None
        return record.reason, record.remark
    if event.source_type == inventory_ledger.SOURCE_RESTOCK_ORDER:
        if event.event_type == inventory_ledger.EVENT_RESTOCK_CORRECTION:
            record = db.scalars(
                select(RestockCorrection).where(
                    RestockCorrection.event_id == event.id
                )
            ).first()
            if record is not None:
                return record.reason, record.remark
        order = (
            db.get(RestockOrder, event.source_id)
            if event.source_id is not None
            else None
        )
        if order is None:
            return None, None
        return order.source, order.remark
    if event.source_type == inventory_ledger.SOURCE_INVENTORY_COUNT:
        line = db.scalars(
            select(InventoryCountLine).where(
                InventoryCountLine.event_id == event.id
            )
        ).first()
        count = (
            db.get(InventoryCount, event.source_id)
            if event.source_id is not None
            else None
        )
        return (
            line.reason if line is not None else None,
            count.remark if count is not None else None,
        )
    return None, None


def _trace_consumable(db: Session, event, class_id: int) -> Consumable:
    consumable = db.get(Consumable, event.consumable_id)
    if consumable is None or consumable.class_id != class_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="库存事件不存在"
        )
    return consumable


@router.get("/events/{event_id}/trace")
def get_inventory_event_trace(
    event_id: int,
    db: Annotated[Session, Depends(get_db)],
    staff: StaffUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> InventoryEventTraceOut:
    event = db.get(InventoryEvent, event_id)
    if event is None or event.class_id != staff.class_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="库存事件不存在"
        )
    consumable = _trace_consumable(db, event, staff.class_id)

    requisition_numbers, order_numbers, restock_numbers = _resolve_order_numbers(
        db, [event]
    )
    order_no = _event_order_no(
        event,
        requisition_numbers,
        order_numbers,
        restock_numbers,
        _resolve_return_numbers(db, [event]),
        _resolve_scrap_numbers(db, [event]),
        _resolve_count_numbers(db, [event]),
    )

    records = _requisition_chain(db, staff.class_id, event)
    order = None
    if (
        event.source_type == inventory_ledger.SOURCE_REQUISITION_ORDER
        and event.source_id is not None
    ):
        order = db.get(RequisitionOrder, event.source_id)
        if order is not None and order.class_id != staff.class_id:
            order = None

    docs: list[TraceDocumentOut] = []
    docs.extend(_requisition_docs(db, staff.class_id, event, records, order))
    docs.extend(_requisition_extra_docs(db, staff.class_id, records))
    docs.extend(_restock_docs(db, staff.class_id, event))
    docs.extend(_count_docs(db, staff.class_id, event))
    docs.sort(
        key=lambda doc: (doc.at, TRACE_KIND_RANK.get(doc.kind, 99), doc.ref_id)
    )
    total = len(docs)
    reason, remark = _trace_reason(db, event)

    return InventoryEventTraceOut(
        event=TraceEventOut(
            id=event.id,
            consumable_id=consumable.id,
            consumable_name=consumable.name,
            consumable_spec=consumable.spec,
            consumable_unit=consumable.unit,
            event_type=event.event_type,
            event_label=TRACE_EVENT_LABELS.get(event.event_type, event.event_type),
            source_type=event.source_type,
            source_id=event.source_id,
            requisition_id=event.requisition_id,
            line_no=event.line_no,
            order_no=order_no,
            delta=event.delta,
            stock_before=event.stock_before,
            stock_after=event.stock_after,
            exec_seq=event.exec_seq,
            unit_snapshot=event.unit_snapshot,
            business_at=event.business_at,
            posted_at=event.posted_at,
            created_at=event.created_at,
            operator_id=event.operator_id,
            operator_name=event.operator_name,
            operator_role=event.operator_role,
            evidence_status=event.evidence_status,
            evidence_label=inventory_audit.EVIDENCE_LABELS.get(
                event.evidence_status, event.evidence_status
            ),
            reason=reason,
            remark=remark,
        ),
        timeline=docs[:limit],
        timeline_total=total,
        limit=limit,
        truncated=total > len(docs[:limit]),
    )


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

    items.sort(key=lambda item: (not item.is_out, not item.low_stock, item.name))

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
    return_ids = {
        event.source_id
        for event in events
        if event.source_type == inventory_ledger.SOURCE_REQUISITION_RETURN
        and event.source_id is not None
    }
    return_numbers = (
        {
            record.id: record.order_no
            for record in db.scalars(
                select(RequisitionReturn).where(RequisitionReturn.id.in_(return_ids))
            )
        }
        if return_ids
        else {}
    )
    scrap_ids = {
        event.source_id
        for event in events
        if event.source_type == inventory_ledger.SOURCE_REQUISITION_SCRAP
        and event.source_id is not None
    }
    scrap_numbers = (
        {
            record.id: record.order_no
            for record in db.scalars(
                select(RequisitionScrap).where(RequisitionScrap.id.in_(scrap_ids))
            )
        }
        if scrap_ids
        else {}
    )
    count_numbers = _resolve_count_numbers(db, events)

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
                    return_numbers,
                    scrap_numbers,
                    count_numbers,
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
    count_numbers = _resolve_count_numbers(db, events)
    by_id = {event.id: event for event in events}

    def to_out(
        event, stock_before: int | None = None, stock_after: int | None = None
    ) -> AuditEventOut:
        order_no = _event_order_no(
            event,
            requisition_numbers,
            order_numbers,
            restock_numbers,
            count_numbers=count_numbers,
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
                event.requisition_id is not None
                or (
                    order_no is not None
                    and event.source_type != inventory_ledger.SOURCE_INVENTORY_COUNT
                )
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


@router.post("/counts")
def create_inventory_count(
    payload: InventoryCountCreate,
    db: Annotated[Session, Depends(get_db)],
    admin: AdminUser,
) -> JSONResponse:
    count, created = inventory_count.create_count(
        db, payload=payload, operator=admin
    )
    service.clear_cache()
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=jsonable_encoder(inventory_count.build_out(db, count)),
    )


@router.get("/counts/{order_no}")
def get_inventory_count(
    order_no: str,
    db: Annotated[Session, Depends(get_db)],
    staff: StaffUser,
) -> InventoryCountOut:
    count = inventory_count.get_count_by_no(db, order_no)
    if count is None or count.class_id != staff.class_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="盘点单不存在"
        )
    return inventory_count.build_out(db, count)


@router.patch("/counts/{order_no}/lines")
def update_inventory_count_lines(
    order_no: str,
    payload: InventoryCountLinesUpdate,
    db: Annotated[Session, Depends(get_db)],
    admin: AdminUser,
) -> InventoryCountOut:
    count = inventory_count.get_count_by_no(db, order_no)
    if count is None or count.class_id != admin.class_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="盘点单不存在"
        )
    inventory_count.update_lines(
        db, count=count, payload=payload, operator=admin
    )
    return inventory_count.build_out(db, count)


@router.post("/counts/{order_no}/post")
def post_inventory_count(
    order_no: str,
    db: Annotated[Session, Depends(get_db)],
    admin: AdminUser,
) -> JSONResponse:
    count = inventory_count.get_count_by_no(db, order_no)
    if count is None or count.class_id != admin.class_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="盘点单不存在"
        )
    count, created = inventory_count.post_count(db, count=count, operator=admin)
    service.clear_cache()
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=jsonable_encoder(inventory_count.build_out(db, count)),
    )
