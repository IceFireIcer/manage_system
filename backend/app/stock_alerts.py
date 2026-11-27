"""低库存站内待办：按耗材唯一记录，持续风险去重，风险解除后完成。"""

from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import case, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import business_time
from app.models import Consumable, StockAlert, User
from app.schemas_alerts import StockAlertOut
from app.schemas_consumables import STATUS_ENABLED

STATUS_OPEN = "open"
STATUS_ACKNOWLEDGED = "acknowledged"
STATUS_COMPLETED = "completed"
ACTIVE_STATUSES = (STATUS_OPEN, STATUS_ACKNOWLEDGED)
RISK_LOW = "low"
RISK_OUT = "out"
AUTO_ACTOR = "系统自动"


def _now() -> datetime:
    return business_time.now().replace(microsecond=0)


def _risk(consumable: Consumable) -> tuple[str | None, int]:
    if consumable.status != STATUS_ENABLED or consumable.safety_stock <= 0:
        return None, 0
    if consumable.current_stock >= consumable.safety_stock:
        return None, 0
    gap = consumable.safety_stock - consumable.current_stock
    level = RISK_OUT if consumable.current_stock == 0 else RISK_LOW
    return level, gap


def get_alert(db: Session, class_id: int, consumable_id: int) -> StockAlert | None:
    return db.scalars(
        select(StockAlert).where(
            StockAlert.class_id == class_id,
            StockAlert.consumable_id == consumable_id,
        )
    ).first()


def _snapshot(
    alert: StockAlert,
    consumable: Consumable,
    level: str,
    gap: int,
    timestamp: datetime,
) -> None:
    alert.risk_level = level
    alert.current_stock = consumable.current_stock
    alert.safety_stock = consumable.safety_stock
    alert.gap = gap
    alert.last_detected_at = timestamp
    alert.updated_at = timestamp
    alert.detection_count += 1


def _clear_ack(alert: StockAlert) -> None:
    alert.acknowledged_at = None
    alert.acknowledged_by_id = None
    alert.acknowledged_by_name = None


def _clear_complete(alert: StockAlert) -> None:
    alert.completed_at = None
    alert.completed_by_id = None
    alert.completed_by_name = None
    alert.completed_reason = None


def _reinforce(
    db: Session,
    alert: StockAlert,
    consumable: Consumable,
    level: str,
    gap: int,
    timestamp: datetime,
    order_no: str | None,
    source: str | None,
) -> StockAlert:
    if alert.status == STATUS_COMPLETED:
        alert.status = STATUS_OPEN
        alert.occurrence += 1
        alert.detection_count = 1
        alert.first_detected_at = timestamp
        _clear_ack(alert)
        _clear_complete(alert)
    else:
        if alert.status == STATUS_ACKNOWLEDGED and level == RISK_OUT:
            alert.status = STATUS_OPEN
            _clear_ack(alert)
        _snapshot(alert, consumable, level, gap, timestamp)
    alert.risk_level = level
    alert.current_stock = consumable.current_stock
    alert.safety_stock = consumable.safety_stock
    alert.gap = gap
    alert.last_detected_at = timestamp
    alert.updated_at = timestamp
    if order_no:
        alert.last_order_no = order_no
    if source:
        alert.last_source = source
    db.add(alert)
    return alert


def sync_consumable(
    db: Session,
    consumable: Consumable,
    *,
    order_no: str | None = None,
    source: str | None = None,
    at: datetime | None = None,
) -> StockAlert | None:
    level, gap = _risk(consumable)
    timestamp = at or _now()
    alert = get_alert(db, consumable.class_id, consumable.id)

    if level is None:
        if alert is not None and alert.status != STATUS_COMPLETED:
            alert.status = STATUS_COMPLETED
            alert.completed_at = timestamp
            alert.completed_by_id = None
            alert.completed_by_name = AUTO_ACTOR
            alert.completed_reason = "库存已恢复至安全库存线以上"
            alert.updated_at = timestamp
            db.add(alert)
        return alert

    if alert is None:
        alert = StockAlert(
            class_id=consumable.class_id,
            consumable_id=consumable.id,
            status=STATUS_OPEN,
            risk_level=level,
            current_stock=consumable.current_stock,
            safety_stock=consumable.safety_stock,
            gap=gap,
            occurrence=1,
            detection_count=1,
            first_detected_at=timestamp,
            last_detected_at=timestamp,
            last_order_no=order_no,
            last_source=source,
            created_at=timestamp,
            updated_at=timestamp,
        )
        try:
            with db.begin_nested():
                db.add(alert)
                db.flush()
        except IntegrityError:
            alert = get_alert(db, consumable.class_id, consumable.id)
            if alert is None:
                raise
            return _reinforce(
                db, alert, consumable, level, gap, timestamp, order_no, source
            )
        return alert

    return _reinforce(
        db, alert, consumable, level, gap, timestamp, order_no, source
    )


def sync_class(
    db: Session,
    class_id: int,
    *,
    source: str | None = None,
    at: datetime | None = None,
) -> None:
    consumables = db.scalars(
        select(Consumable).where(Consumable.class_id == class_id)
    ).all()
    for consumable in consumables:
        sync_consumable(db, consumable, source=source, at=at)


def build_out(alert: StockAlert, consumable: Consumable) -> StockAlertOut:
    gap = max(consumable.safety_stock - consumable.current_stock, 0)
    return StockAlertOut(
        consumable_id=alert.consumable_id,
        consumable_name=consumable.name,
        unit=consumable.unit,
        status=alert.status,
        risk_level=RISK_OUT if consumable.current_stock == 0 else RISK_LOW,
        current_stock=consumable.current_stock,
        safety_stock=consumable.safety_stock,
        gap=gap,
        partial_arrival=(
            alert.status in ACTIVE_STATUSES
            and alert.last_order_no is not None
            and gap > 0
        ),
        occurrence=alert.occurrence,
        detection_count=alert.detection_count,
        first_detected_at=alert.first_detected_at,
        last_detected_at=alert.last_detected_at,
        acknowledged_at=alert.acknowledged_at,
        acknowledged_by_name=alert.acknowledged_by_name,
        completed_at=alert.completed_at,
        completed_by_name=alert.completed_by_name,
        completed_reason=alert.completed_reason,
        last_order_no=alert.last_order_no,
        last_source=alert.last_source,
        note=alert.note,
        updated_at=alert.updated_at,
    )


def list_alerts(
    db: Session,
    class_id: int,
    *,
    include_completed: bool = False,
) -> tuple[list[StockAlertOut], int]:
    query = (
        select(StockAlert, Consumable)
        .join(Consumable, Consumable.id == StockAlert.consumable_id)
        .where(StockAlert.class_id == class_id)
    )
    if not include_completed:
        query = query.where(StockAlert.status.in_(ACTIVE_STATUSES))
    rows = db.execute(
        query.order_by(
            case((StockAlert.status == STATUS_OPEN, 0), else_=1),
            case((StockAlert.risk_level == RISK_OUT, 0), else_=1),
            StockAlert.last_detected_at.desc(),
            StockAlert.id.desc(),
        )
    ).all()
    items = [build_out(alert, consumable) for alert, consumable in rows]
    active_count = sum(
        1 for item in items if item.status in ACTIVE_STATUSES
    )
    return items, active_count


def acknowledge(db: Session, alert: StockAlert, operator: User, note: str | None) -> None:
    if alert.status == STATUS_COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="待办已完成，重新触发后才能确认",
        )
    timestamp = _now()
    alert.status = STATUS_ACKNOWLEDGED
    alert.acknowledged_at = timestamp
    alert.acknowledged_by_id = operator.id
    alert.acknowledged_by_name = operator.name
    if note:
        alert.note = note
    alert.updated_at = timestamp
    db.add(alert)
    db.commit()


def complete(db: Session, alert: StockAlert, operator: User, note: str | None) -> None:
    if alert.status == STATUS_COMPLETED:
        return
    timestamp = _now()
    alert.status = STATUS_COMPLETED
    alert.completed_at = timestamp
    alert.completed_by_id = operator.id
    alert.completed_by_name = operator.name
    alert.completed_reason = note or "人工确认已处理"
    if note:
        alert.note = note
    alert.updated_at = timestamp
    db.add(alert)
    db.commit()


def retrigger(db: Session, alert: StockAlert, operator: User, note: str | None) -> None:
    if alert.status in ACTIVE_STATUSES:
        if note:
            alert.note = note
            alert.updated_at = _now()
            db.add(alert)
            db.commit()
        return
    timestamp = _now()
    alert.status = STATUS_OPEN
    alert.occurrence += 1
    alert.detection_count = 1
    alert.first_detected_at = timestamp
    alert.last_detected_at = timestamp
    _clear_ack(alert)
    _clear_complete(alert)
    if note:
        alert.note = note
    alert.updated_at = timestamp
    db.add(alert)
    db.commit()


def handle_action(
    db: Session,
    alert: StockAlert,
    operator: User,
    action: str,
    note: str | None,
) -> None:
    if action == "acknowledge":
        acknowledge(db, alert, operator, note)
    elif action == "complete":
        complete(db, alert, operator, note)
    elif action == "retrigger":
        retrigger(db, alert, operator, note)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的待办操作"
        )
