import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app import business_time, restock
from app.database import get_db
from app.deps import require_permission
from app.models import (
    Consumable,
    HandoverChecklist,
    HandoverItem,
    IncidentReport,
    InventoryCount,
    InventoryCountLine,
    RequisitionOrder,
    RestockOrder,
    StockAlert,
    User,
)
from app.routers.courses import _build_risks as _build_course_risks
from app.routers.courses import _upcoming_entries as _upcoming_course_entries
from app.schemas_consumables import STATUS_ENABLED
from app.schemas_handover import (
    KIND_COUNT,
    KIND_INCIDENT,
    KIND_REQUISITION,
    KIND_RESTOCK,
    KIND_STOCK,
    KIND_SUGGESTION,
    STATUS_CONFIRMED,
    STATUS_PENDING,
    HandoverChecklistCreate,
    HandoverChecklistOut,
    HandoverChecklistPageOut,
    HandoverChecklistSummaryOut,
    HandoverHealthExpiryOut,
    HandoverHealthOut,
    HandoverItemOut,
)
from app.stock_alerts import ACTIVE_STATUSES as ALERT_ACTIVE_STATUSES

router = APIRouter(prefix="/api/v1/handover", tags=["handover"])

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
PROCESSING_STATUS = "processing"
INCIDENT_OPEN_STATUS = "open"
COUNT_OPEN_STATUS = "open"
COUNT_POSTED_STATUS = "posted"
OPEN_INCIDENT_STATUSES = (INCIDENT_OPEN_STATUS, STATUS_CONFIRMED)
COURSE_RISK_DAYS = 14
COURSE_RISK_LEAD_DAYS = 1
COURSE_RISK_LEVELS = ("insufficient", "critical")
EXPIRY_LABEL = "未启用"

HandoverViewer = Annotated[User, Depends(require_permission("handover.view"))]
HandoverManager = Annotated[User, Depends(require_permission("handover.manage"))]


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _draft(
    kind: str, ref_type: str, ref_id: int | None, label: str, payload: dict
) -> dict:
    return {
        "kind": kind,
        "ref_type": ref_type,
        "ref_id": ref_id,
        "label": label,
        "payload": payload,
    }


def _stock_drafts(db: Session, class_id: int) -> list[dict]:
    rows = db.execute(
        select(StockAlert, Consumable)
        .join(Consumable, Consumable.id == StockAlert.consumable_id)
        .where(
            StockAlert.class_id == class_id,
            StockAlert.status.in_(ALERT_ACTIVE_STATUSES),
        )
        .order_by(StockAlert.gap.desc(), StockAlert.id)
    ).all()
    return [
        _draft(
            KIND_STOCK,
            "stock_alert",
            alert.id,
            f"库存异常：{consumable.name}",
            {
                "consumable_id": consumable.id,
                "name": consumable.name,
                "unit": consumable.unit,
                "current_stock": consumable.current_stock,
                "safety_stock": consumable.safety_stock,
                "gap": alert.gap,
                "risk_level": alert.risk_level,
                "alert_status": alert.status,
            },
        )
        for alert, consumable in rows
    ]


def _restock_drafts(db: Session, class_id: int) -> list[dict]:
    orders = db.scalars(
        select(RestockOrder)
        .where(
            RestockOrder.class_id == class_id,
            RestockOrder.status == PROCESSING_STATUS,
        )
        .order_by(RestockOrder.id)
    ).all()
    return [
        _draft(
            KIND_RESTOCK,
            "restock_order",
            order.id,
            f"未闭合入库单 {order.order_no}",
            {
                "order_no": order.order_no,
                "status": order.status,
                "business_at": _iso(order.business_at),
            },
        )
        for order in orders
    ]


def _requisition_drafts(db: Session, class_id: int) -> list[dict]:
    orders = db.scalars(
        select(RequisitionOrder)
        .where(
            RequisitionOrder.class_id == class_id,
            RequisitionOrder.status == PROCESSING_STATUS,
        )
        .order_by(RequisitionOrder.id)
    ).all()
    return [
        _draft(
            KIND_REQUISITION,
            "requisition_order",
            order.id,
            f"未闭合领用单 {order.order_no}",
            {
                "order_no": order.order_no,
                "status": order.status,
                "business_at": _iso(order.business_at),
            },
        )
        for order in orders
    ]


def _suggestion_drafts(db: Session, class_id: int) -> list[dict]:
    consumables = db.scalars(
        select(Consumable)
        .where(Consumable.class_id == class_id, Consumable.status == STATUS_ENABLED)
        .order_by(Consumable.id)
    ).all()
    drafts: list[dict] = []
    for consumable in consumables:
        evaluation = restock._evaluate(db, consumable)
        if evaluation is None:
            continue
        drafts.append(
            _draft(
                KIND_SUGGESTION,
                "consumable",
                consumable.id,
                f"补货建议：{consumable.name}",
                {
                    "consumable_id": consumable.id,
                    "name": consumable.name,
                    "unit": consumable.unit,
                    "tier": evaluation.tier,
                    "gap": evaluation.gap,
                    "q7": evaluation.q7,
                    "recommended_quantity": evaluation.recommended_quantity,
                    "reasons": list(evaluation.reasons),
                },
            )
        )
    return drafts


def _incident_drafts(db: Session, class_id: int) -> list[dict]:
    rows = db.execute(
        select(IncidentReport, Consumable)
        .join(Consumable, Consumable.id == IncidentReport.consumable_id)
        .where(
            IncidentReport.class_id == class_id,
            IncidentReport.status == INCIDENT_OPEN_STATUS,
        )
        .order_by(IncidentReport.id)
    ).all()
    return [
        _draft(
            KIND_INCIDENT,
            "incident_report",
            report.id,
            f"未闭合异常：{consumable.name}",
            {
                "incident_id": report.id,
                "consumable_id": consumable.id,
                "consumable_name": consumable.name,
                "location": report.location,
                "status": report.status,
                "created_at": _iso(report.created_at),
            },
        )
        for report, consumable in rows
    ]


def _count_drafts(db: Session, class_id: int) -> list[dict]:
    counts = db.scalars(
        select(InventoryCount)
        .where(
            InventoryCount.class_id == class_id,
            InventoryCount.status == COUNT_OPEN_STATUS,
        )
        .order_by(InventoryCount.id)
    ).all()
    return [
        _draft(
            KIND_COUNT,
            "inventory_count",
            count.id,
            f"盘点未过账 {count.order_no}",
            {
                "order_no": count.order_no,
                "count_at": _iso(count.count_at),
                "status": count.status,
            },
        )
        for count in counts
    ]


def _collect_drafts(db: Session, class_id: int) -> list[dict]:
    drafts: list[dict] = []
    drafts.extend(_stock_drafts(db, class_id))
    drafts.extend(_restock_drafts(db, class_id))
    drafts.extend(_requisition_drafts(db, class_id))
    drafts.extend(_suggestion_drafts(db, class_id))
    drafts.extend(_incident_drafts(db, class_id))
    drafts.extend(_count_drafts(db, class_id))
    return drafts


def _stock_diff_health(db: Session, class_id: int) -> tuple[int, bool]:
    latest = db.scalars(
        select(InventoryCount)
        .where(
            InventoryCount.class_id == class_id,
            InventoryCount.status == COUNT_POSTED_STATUS,
        )
        .order_by(InventoryCount.posted_at.desc(), InventoryCount.id.desc())
    ).first()
    if latest is None:
        return 0, False
    diff_count = (
        db.scalar(
            select(func.count())
            .select_from(InventoryCountLine)
            .where(
                InventoryCountLine.count_id == latest.id,
                InventoryCountLine.diff.is_not(None),
                InventoryCountLine.diff != 0,
            )
        )
        or 0
    )
    return diff_count, True


def _open_incident_health(db: Session, class_id: int) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(IncidentReport)
            .where(
                IncidentReport.class_id == class_id,
                IncidentReport.status.in_(OPEN_INCIDENT_STATUSES),
            )
        )
        or 0
    )


def _course_risk_health(db: Session, class_id: int) -> int:
    entries = _upcoming_course_entries(db, class_id, COURSE_RISK_DAYS)
    risks = _build_course_risks(db, entries, COURSE_RISK_LEAD_DAYS)
    return sum(1 for risk in risks if risk.level in COURSE_RISK_LEVELS)


def _resolve_incoming(
    db: Session, payload: HandoverChecklistCreate, staff: User
) -> tuple[int | None, str | None, str | None]:
    if payload.incoming_id is not None:
        member = db.get(User, payload.incoming_id)
    elif payload.incoming_account:
        member = db.scalars(
            select(User).where(
                User.account == payload.incoming_account,
                User.class_id == staff.active_class_id,
                User.status == "enabled",
            )
        ).first()
    else:
        return None, payload.incoming_name, None
    if member is None or member.class_id != staff.active_class_id or member.status != "enabled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="接班账号不存在或不属于本班",
        )
    return member.id, member.name, member.account


def _load_item_payload(item: HandoverItem) -> dict:
    try:
        parsed = json.loads(item.snapshot_payload)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _item_out(item: HandoverItem) -> HandoverItemOut:
    return HandoverItemOut(
        id=item.id,
        seq=item.seq,
        kind=item.kind,
        ref_type=item.ref_type,
        ref_id=item.ref_id,
        label=item.label,
        payload=_load_item_payload(item),
        confirmed=item.confirmed,
        confirmed_by_name=item.confirmed_by_name,
        confirmed_at=item.confirmed_at,
    )


def _summary(
    checklist: HandoverChecklist, item_count: int, confirmed_count: int
) -> HandoverChecklistSummaryOut:
    return HandoverChecklistSummaryOut(
        id=checklist.id,
        class_id=checklist.class_id,
        version=checklist.version,
        status=checklist.status,
        outgoing_id=checklist.outgoing_id,
        outgoing_name=checklist.outgoing_name,
        outgoing_account=checklist.outgoing_account,
        incoming_id=checklist.incoming_id,
        incoming_name=checklist.incoming_name,
        incoming_account=checklist.incoming_account,
        item_count=item_count,
        confirmed_count=confirmed_count,
        snapshot_at=checklist.snapshot_at,
        confirmed_at=checklist.confirmed_at,
        confirmed_by_id=checklist.confirmed_by_id,
        confirmed_by_name=checklist.confirmed_by_name,
        confirmed_by_account=checklist.confirmed_by_account,
        created_at=checklist.created_at,
    )


def _detail(db: Session, checklist: HandoverChecklist) -> HandoverChecklistOut:
    items = db.scalars(
        select(HandoverItem)
        .where(HandoverItem.checklist_id == checklist.id)
        .order_by(HandoverItem.seq)
    ).all()
    confirmed_count = sum(1 for item in items if item.confirmed)
    summary = _summary(checklist, len(items), confirmed_count)
    return HandoverChecklistOut(
        **summary.model_dump(), items=[_item_out(item) for item in items]
    )


def _get_checklist(db: Session, checklist_id: int, user: User) -> HandoverChecklist:
    checklist = db.get(HandoverChecklist, checklist_id)
    if checklist is None or checklist.class_id != user.active_class_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="交接清单不存在"
        )
    return checklist


@router.post("/checklists", status_code=status.HTTP_201_CREATED)
def create_checklist(
    payload: HandoverChecklistCreate,
    db: Annotated[Session, Depends(get_db)],
    staff: HandoverManager,
) -> HandoverChecklistOut:
    incoming_id, incoming_name, incoming_account = _resolve_incoming(db, payload, staff)
    version = (
        db.scalar(
            select(func.max(HandoverChecklist.version)).where(
                HandoverChecklist.class_id == staff.active_class_id
            )
        )
        or 0
    ) + 1
    now = business_time.now().replace(microsecond=0)
    checklist = HandoverChecklist(
        class_id=staff.active_class_id,
        version=version,
        status=STATUS_PENDING,
        outgoing_id=staff.id,
        outgoing_name=staff.name,
        outgoing_account=staff.account,
        incoming_id=incoming_id,
        incoming_name=incoming_name,
        incoming_account=incoming_account,
        snapshot_at=now,
        created_at=now,
    )
    db.add(checklist)
    db.flush()
    for seq, draft in enumerate(_collect_drafts(db, staff.active_class_id), start=1):
        db.add(
            HandoverItem(
                checklist_id=checklist.id,
                seq=seq,
                kind=draft["kind"],
                ref_type=draft["ref_type"],
                ref_id=draft["ref_id"],
                label=draft["label"],
                snapshot_payload=json.dumps(draft["payload"], ensure_ascii=False),
                confirmed=False,
                created_at=now,
            )
        )
    db.commit()
    db.refresh(checklist)
    return _detail(db, checklist)


@router.get("/checklists")
def list_checklists(
    db: Annotated[Session, Depends(get_db)],
    staff: HandoverViewer,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> HandoverChecklistPageOut:
    filters = [HandoverChecklist.class_id == staff.active_class_id]
    total = (
        db.scalar(select(func.count()).select_from(HandoverChecklist).where(*filters))
        or 0
    )
    rows = db.scalars(
        select(HandoverChecklist)
        .where(*filters)
        .order_by(HandoverChecklist.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    ids = [row.id for row in rows]
    counts: dict[int, tuple[int, int]] = {}
    if ids:
        stats = db.execute(
            select(
                HandoverItem.checklist_id,
                func.count(),
                func.sum(case((HandoverItem.confirmed.is_(True), 1), else_=0)),
            )
            .where(HandoverItem.checklist_id.in_(ids))
            .group_by(HandoverItem.checklist_id)
        ).all()
        counts = {
            checklist_id: (item_count, int(confirmed_count or 0))
            for checklist_id, item_count, confirmed_count in stats
        }
    return HandoverChecklistPageOut(
        items=[
            _summary(row, *counts.get(row.id, (0, 0))) for row in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/checklists/{checklist_id}")
def get_checklist(
    checklist_id: int,
    db: Annotated[Session, Depends(get_db)],
    staff: HandoverViewer,
) -> HandoverChecklistOut:
    return _detail(db, _get_checklist(db, checklist_id, staff))


@router.post("/checklists/{checklist_id}/items/{item_id}/confirm")
def confirm_item(
    checklist_id: int,
    item_id: int,
    db: Annotated[Session, Depends(get_db)],
    staff: HandoverManager,
) -> HandoverItemOut:
    checklist = _get_checklist(db, checklist_id, staff)
    if checklist.status == STATUS_CONFIRMED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="交接清单已确认"
        )
    item = db.scalars(
        select(HandoverItem).where(
            HandoverItem.id == item_id,
            HandoverItem.checklist_id == checklist.id,
        )
    ).first()
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="交接事项不存在"
        )
    if not item.confirmed:
        item.confirmed = True
        item.confirmed_by_id = staff.id
        item.confirmed_by_name = staff.name
        item.confirmed_at = business_time.now().replace(microsecond=0)
        db.commit()
        db.refresh(item)
    return _item_out(item)


@router.post("/checklists/{checklist_id}/confirm")
def confirm_checklist(
    checklist_id: int,
    db: Annotated[Session, Depends(get_db)],
    staff: HandoverManager,
) -> HandoverChecklistOut:
    checklist = _get_checklist(db, checklist_id, staff)
    if checklist.status == STATUS_CONFIRMED:
        return _detail(db, checklist)
    remaining = (
        db.scalar(
            select(func.count())
            .select_from(HandoverItem)
            .where(
                HandoverItem.checklist_id == checklist.id,
                HandoverItem.confirmed.is_(False),
            )
        )
        or 0
    )
    if remaining > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"仍有 {remaining} 项未确认",
        )
    now = business_time.now().replace(microsecond=0)
    checklist.status = STATUS_CONFIRMED
    checklist.confirmed_at = now
    checklist.confirmed_by_id = staff.id
    checklist.confirmed_by_name = staff.name
    checklist.confirmed_by_account = staff.account
    db.commit()
    db.refresh(checklist)
    return _detail(db, checklist)


@router.get("/health")
def get_handover_health(
    db: Annotated[Session, Depends(get_db)],
    staff: HandoverViewer,
) -> HandoverHealthOut:
    stock_diff_count, stock_diff_available = _stock_diff_health(db, staff.active_class_id)
    return HandoverHealthOut(
        generated_at=business_time.now().replace(microsecond=0),
        class_id=staff.active_class_id,
        stock_diff_count=stock_diff_count,
        stock_diff_available=stock_diff_available,
        open_incident_count=_open_incident_health(db, staff.active_class_id),
        course_risk_count=_course_risk_health(db, staff.active_class_id),
        expiry=HandoverHealthExpiryOut(
            supported=False,
            label=EXPIRY_LABEL,
            count=None,
        ),
    )
