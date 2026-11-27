import hashlib
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Consumable, InventoryEvent, User

EVENT_OPENING = "opening"
EVENT_REQUISITION = "requisition"
EVENT_REVERSAL = "reversal"
EVENT_RESTOCK = "restock"
EVENT_RESTOCK_CORRECTION = "restock_correction"
EVENT_RETURN = "return"
EVENT_SCRAP = "scrap"
EVENT_INVENTORY_ADJUSTMENT = "inventory_adjustment"

SOURCE_OPENING = "opening"
SOURCE_REQUISITION_ORDER = "requisition_order"
SOURCE_REQUISITION_REVERSAL = "requisition_reversal"
SOURCE_REQUISITION_REENTRY = "requisition_reentry"
SOURCE_RESTOCK_ORDER = "restock_order"
SOURCE_REQUISITION_RETURN = "requisition_return"
SOURCE_REQUISITION_SCRAP = "requisition_scrap"
SOURCE_INVENTORY_COUNT = "inventory_count"

EVIDENCE_TRUSTED = "trusted"
EVIDENCE_INSUFFICIENT = "insufficient"


def next_exec_seq(db: Session, consumable_id: int) -> int:
    current = db.scalar(
        select(func.max(InventoryEvent.exec_seq)).where(
            InventoryEvent.consumable_id == consumable_id
        )
    )
    return (current or 0) + 1


def append_event(
    db: Session,
    *,
    consumable: Consumable,
    event_type: str,
    delta: int,
    stock_before: int,
    stock_after: int,
    business_at: datetime,
    posted_at: datetime,
    source_type: str,
    source_id: int | None = None,
    requisition_id: int | None = None,
    line_no: int | None = None,
    operator: User | None = None,
    name_snapshot: str | None = None,
    spec_snapshot: str | None = None,
    unit_snapshot: str | None = None,
    evidence_status: str = EVIDENCE_TRUSTED,
) -> InventoryEvent:
    event = InventoryEvent(
        consumable_id=consumable.id,
        event_type=event_type,
        delta=delta,
        stock_before=stock_before,
        stock_after=stock_after,
        exec_seq=next_exec_seq(db, consumable.id),
        business_at=business_at,
        posted_at=posted_at,
        source_type=source_type,
        source_id=source_id,
        requisition_id=requisition_id,
        line_no=line_no,
        operator_id=operator.id if operator else None,
        operator_name=operator.name if operator else None,
        operator_role=operator.role if operator else None,
        name_snapshot=(
            name_snapshot if name_snapshot is not None else consumable.name
        ),
        spec_snapshot=(
            spec_snapshot if spec_snapshot is not None else consumable.spec
        ),
        unit_snapshot=unit_snapshot if unit_snapshot is not None else consumable.unit,
        evidence_status=evidence_status,
        class_id=consumable.class_id,
    )
    db.add(event)
    db.flush()
    return event


def record_opening(
    db: Session, consumable: Consumable, operator: User | None = None
) -> InventoryEvent | None:
    if consumable.current_stock <= 0:
        return None
    return append_event(
        db,
        consumable=consumable,
        event_type=EVENT_OPENING,
        delta=consumable.current_stock,
        stock_before=0,
        stock_after=consumable.current_stock,
        business_at=consumable.created_at,
        posted_at=consumable.created_at,
        source_type=SOURCE_OPENING,
        operator=operator,
    )


def list_events(db: Session, consumable_id: int) -> list[InventoryEvent]:
    return list(
        db.scalars(
            select(InventoryEvent)
            .where(InventoryEvent.consumable_id == consumable_id)
            .order_by(InventoryEvent.exec_seq, InventoryEvent.id)
        ).all()
    )


def order_no_for(request_id: str, business_at: datetime, prefix: str = "REQ") -> str:
    digest = hashlib.sha1(request_id.encode("utf-8")).hexdigest()[:10].upper()
    return f"{prefix}-{business_at.strftime('%Y%m%d')}-{digest}"


def restock_order_no_for(request_id: str, business_at: datetime) -> str:
    return order_no_for(request_id, business_at, prefix="RSK")


def reversal_order_no_for(requisition_id: int, business_at: datetime) -> str:
    return f"RVL-{business_at.strftime('%Y%m%d')}-{requisition_id:06d}"


def reentry_order_no_for(reversal_id: int, business_at: datetime) -> str:
    return f"REN-{business_at.strftime('%Y%m%d')}-{reversal_id:06d}"

def return_order_no_for(request_id: str, business_at: datetime) -> str:
    return order_no_for(request_id, business_at, prefix="RTN")


def scrap_order_no_for(request_id: str, business_at: datetime) -> str:
    return order_no_for(request_id, business_at, prefix="SCP")

def count_order_no_for(request_id: str, business_at: datetime) -> str:
    return order_no_for(request_id, business_at, prefix="CNT")
