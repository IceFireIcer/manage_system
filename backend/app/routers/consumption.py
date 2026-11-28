from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app import business_time
from app import consumption as consumption_core
from app.database import get_db
from app.deps import require_permission
from app.models import (
    Consumable,
    CourseSession,
    HandoverChecklist,
    IncidentReport,
    InventoryCount,
    InventoryCountLine,
    Requisition,
    User,
)
from app.schemas_consumption import (
    AuditEventOut,
    ConsumptionAuditOut,
    ConsumptionItemOut,
    ConsumptionSummaryOut,
    ConsumptionUnitTotalOut,
    PositiveFeedbackOut,
    TeachingInsightOut,
)

router = APIRouter(prefix="/api/v1/consumption", tags=["consumption"])

StaffUser = Annotated[User, Depends(require_permission("consumption.view"))]

TEACHING_INSIGHT_WINDOWS = consumption_core.VALID_WINDOWS
NON_TRAINING_GROWTH_THRESHOLD = 0.5
COUNT_POSTED_STATUS = "posted"
OPEN_INCIDENT_STATUSES = ("open", "confirmed")

POSITIVE_FEEDBACK_MESSAGE = "本班近期无未闭合异常、账实一致且交接已确认。"

InsightState = Literal[
    "training_growth",
    "non_training_growth",
    "decline",
    "stable",
    "no_calendar",
]
FeedbackReason = Literal[
    "open_incidents",
    "stock_diff_unavailable",
    "stock_diff_mismatch",
    "no_confirmed_handover",
]


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


def _net_quantity(
    db: Session,
    class_id: int,
    start: datetime,
    end: datetime,
    *,
    end_inclusive: bool,
) -> int:
    issued = func.coalesce(
        func.sum(case((Requisition.type == "normal", Requisition.quantity), else_=0)), 0
    )
    reversed_quantity = func.coalesce(
        func.sum(
            case(
                (
                    and_(
                        Requisition.type == "reversal",
                        Requisition.status == "active",
                    ),
                    Requisition.quantity,
                ),
                else_=0,
            )
        ),
        0,
    )
    end_filter = (
        Requisition.created_at <= end
        if end_inclusive
        else Requisition.created_at < end
    )
    rows = db.execute(
        select(Requisition.consumable_id, issued, reversed_quantity)
        .where(
            Requisition.class_id == class_id,
            Requisition.created_at >= start,
            end_filter,
        )
        .group_by(Requisition.consumable_id)
    ).all()
    return sum(
        max(int(issued_total) - int(reversed_sum), 0)
        for _, issued_total, reversed_sum in rows
    )


def _change_ratio(current: int, previous: int) -> float | None:
    if previous <= 0:
        return None
    return round((current - previous) / previous, 2)


def _growth_exceeds_threshold(current: int, previous: int) -> bool:
    if current <= previous:
        return False
    if previous <= 0:
        return True
    return (current - previous) / previous > NON_TRAINING_GROWTH_THRESHOLD


def _insight_state(
    *,
    calendar_available: bool,
    training_session_count: int,
    current: int,
    previous: int,
) -> InsightState:
    if not calendar_available:
        return "no_calendar"
    if current < previous:
        return "decline"
    if current > previous:
        if training_session_count > 0:
            return "training_growth"
        if _growth_exceeds_threshold(current, previous):
            return "non_training_growth"
    return "stable"


def _insight_message(
    state: InsightState, training_session_count: int, change_ratio: float | None
) -> str:
    if state == "no_calendar":
        return "本班暂未录入课次，暂不做实训周解读。"
    if state == "training_growth":
        return f"本窗口有 {training_session_count} 次计划课次，消耗上升与实训节奏相符。"
    if state == "non_training_growth":
        return "本窗口无计划课次，消耗上升高于常规，请核对领用。"
    if state == "decline":
        return "消耗较前一窗口下降。"
    return "消耗与前一窗口基本持平。"


def _positive_feedback(db: Session, class_id: int) -> PositiveFeedbackOut:
    open_incidents = (
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
    if open_incidents:
        return PositiveFeedbackOut(eligible=False, reason="open_incidents")

    latest = db.scalars(
        select(InventoryCount)
        .where(
            InventoryCount.class_id == class_id,
            InventoryCount.status == COUNT_POSTED_STATUS,
        )
        .order_by(InventoryCount.posted_at.desc(), InventoryCount.id.desc())
    ).first()
    if latest is None:
        return PositiveFeedbackOut(eligible=False, reason="stock_diff_unavailable")
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
    if diff_count:
        return PositiveFeedbackOut(eligible=False, reason="stock_diff_mismatch")

    confirmed_handover = (
        db.scalar(
            select(func.count())
            .select_from(HandoverChecklist)
            .where(
                HandoverChecklist.class_id == class_id,
                HandoverChecklist.status == "confirmed",
            )
        )
        or 0
    )
    if not confirmed_handover:
        return PositiveFeedbackOut(eligible=False, reason="no_confirmed_handover")

    return PositiveFeedbackOut(eligible=True, message=POSITIVE_FEEDBACK_MESSAGE)


@router.get("/teaching-insight")
def get_teaching_insight(
    db: Annotated[Session, Depends(get_db)],
    staff: StaffUser,
    window: Annotated[int, Query()] = consumption_core.DEFAULT_WINDOW,
) -> TeachingInsightOut:
    if window not in TEACHING_INSIGHT_WINDOWS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="window 参数仅支持 7 或 30",
        )
    start = consumption_core.window_start(window)
    end = consumption_core.window_end()
    class_id = staff.active_class_id

    current = _net_quantity(db, class_id, start, end, end_inclusive=True)
    previous = _net_quantity(
        db,
        class_id,
        consumption_core.local_day_start(2 * window - 1),
        start,
        end_inclusive=False,
    )
    training_session_count = (
        db.scalar(
            select(func.count())
            .select_from(CourseSession)
            .where(
                CourseSession.class_id == class_id,
                CourseSession.status == "planned",
                CourseSession.session_date >= start.date(),
                CourseSession.session_date <= end.date(),
            )
        )
        or 0
    )
    calendar_available = bool(
        db.scalar(
            select(func.count())
            .select_from(CourseSession)
            .where(CourseSession.class_id == class_id)
        )
    )

    state = _insight_state(
        calendar_available=calendar_available,
        training_session_count=training_session_count,
        current=current,
        previous=previous,
    )
    change_ratio = _change_ratio(current, previous)
    return TeachingInsightOut(
        window=window,
        generated_at=business_time.now().replace(microsecond=0),
        class_id=class_id,
        state=state,
        message=_insight_message(state, training_session_count, change_ratio),
        calendar_available=calendar_available,
        training_session_count=training_session_count,
        current_net_quantity=current,
        previous_net_quantity=previous,
        change_quantity=current - previous,
        change_ratio=change_ratio,
        positive_feedback=_positive_feedback(db, class_id),
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
