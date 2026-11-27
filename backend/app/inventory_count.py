import json
import uuid
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import business_time, inventory_ledger
from app.models import (
    Consumable,
    InventoryCount,
    InventoryCountLine,
    OperationLog,
    User,
)
from app.schemas_consumables import STATUS_ENABLED
from app.schemas_inventory import (
    InventoryCountCreate,
    InventoryCountLineOut,
    InventoryCountLinesUpdate,
    InventoryCountOut,
)

STAFF_ROLES = ("teacher", "admin")


def _now() -> datetime:
    return business_time.now().replace(microsecond=0)


def _resolve_member(
    db: Session, user_id: int | None, operator: User, label: str
) -> User:
    if user_id is None:
        return operator
    member = db.get(User, user_id)
    if (
        member is None
        or member.class_id != operator.class_id
        or member.role not in STAFF_ROLES
        or member.status != "enabled"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{label}必须是本班在职教师或班委",
        )
    return member


def _normalize_count_at(value: datetime | None) -> datetime:
    if value is None:
        return _now()
    normalized = business_time.normalize(value)
    if normalized > business_time.now():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="盘点时点不能晚于当前时间",
        )
    return normalized.replace(microsecond=0)


def _signature(lines) -> str:
    return json.dumps(
        sorted([[line.consumable_id, line.counted_qty] for line in lines])
    )


def _baseline(db: Session, consumable: Consumable) -> tuple[int, int]:
    events = inventory_ledger.list_events(db, consumable.id)
    if events:
        return events[-1].exec_seq, events[-1].stock_after
    return 0, consumable.current_stock


def _period_delta(db: Session, consumable_id: int, baseline_seq: int) -> int:
    events = inventory_ledger.list_events(db, consumable_id)
    return sum(event.delta for event in events if event.exec_seq > baseline_seq)


def _resolve_repeat(
    db: Session, existing: InventoryCount, payload: InventoryCountCreate, operator: User
) -> InventoryCount:
    if (
        existing.class_id != operator.class_id
        or existing.request_id != payload.request_id
        or existing.reviewer_id != (payload.reviewer_id or operator.id)
        or existing.remark != payload.remark
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="请求标识已用于不同内容，请勿复用",
        )
    rows = db.scalars(
        select(InventoryCountLine).where(InventoryCountLine.count_id == existing.id)
    ).all()
    if _signature(rows) != _signature(payload.lines):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="请求标识已用于不同内容，请勿复用",
        )
    return existing


def create_count(
    db: Session, *, payload: InventoryCountCreate, operator: User
) -> tuple[InventoryCount, bool]:
    request_id = payload.request_id or uuid.uuid4().hex
    payload.request_id = request_id
    if payload.counter_id is None:
        payload.counter_id = operator.id
    if payload.reviewer_id is None:
        payload.reviewer_id = operator.id

    existing = db.scalars(
        select(InventoryCount).where(InventoryCount.request_id == request_id)
    ).first()
    if existing is not None:
        return _resolve_repeat(db, existing, payload, operator), False

    seen: set[int] = set()
    for line in payload.lines:
        if line.consumable_id in seen:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="盘点单内不能重复出现同一耗材",
            )
        seen.add(line.consumable_id)

    counter = _resolve_member(db, payload.counter_id, operator, "盘点人")
    reviewer = _resolve_member(db, payload.reviewer_id, operator, "复核人")
    count_at = _normalize_count_at(payload.count_at)
    timestamp = _now()

    count = InventoryCount(
        order_no=inventory_ledger.count_order_no_for(request_id, count_at),
        request_id=request_id,
        status="open",
        count_at=count_at,
        counter_id=counter.id,
        counter_name=counter.name,
        counter_role=counter.role,
        reviewer_id=reviewer.id,
        reviewer_name=reviewer.name,
        reviewer_role=reviewer.role,
        operator_id=operator.id,
        operator_name=operator.name,
        operator_role=operator.role,
        class_id=operator.class_id,
        remark=payload.remark,
        created_at=timestamp,
        updated_at=timestamp,
    )
    db.add(count)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        conflict = db.scalars(
            select(InventoryCount).where(InventoryCount.request_id == request_id)
        ).first()
        if conflict is None:
            raise
        return _resolve_repeat(db, conflict, payload, operator), False

    for row_number, item in enumerate(payload.lines, start=1):
        consumable = db.get(Consumable, item.consumable_id, with_for_update=True)
        if consumable is None or consumable.class_id != operator.class_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"耗材不存在（id={item.consumable_id}）",
            )
        if consumable.status != STATUS_ENABLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"耗材「{consumable.name}」已停用，不可盘点",
            )
        baseline_seq, baseline_stock = _baseline(db, consumable)
        db.add(
            InventoryCountLine(
                count_id=count.id,
                line_no=row_number,
                consumable_id=consumable.id,
                consumable_name=consumable.name,
                unit=consumable.unit,
                baseline_stock=baseline_stock,
                baseline_seq=baseline_seq,
                counted_qty=item.counted_qty,
                period_delta=0,
                diff=(
                    item.counted_qty - baseline_stock
                    if item.counted_qty is not None
                    else None
                ),
                reason=item.reason,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
    db.commit()
    db.refresh(count)
    return count, True


def get_count_by_no(db: Session, order_no: str) -> InventoryCount | None:
    return db.scalars(
        select(InventoryCount).where(InventoryCount.order_no == order_no)
    ).first()


def update_lines(
    db: Session,
    *,
    count: InventoryCount,
    payload: InventoryCountLinesUpdate,
    operator: User,
) -> InventoryCount:
    if count.status != "open":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="已过账的盘点单不可修改",
        )
    rows = db.scalars(
        select(InventoryCountLine).where(InventoryCountLine.count_id == count.id)
    ).all()
    by_consumable = {row.consumable_id: row for row in rows}
    timestamp = _now()
    seen: set[int] = set()
    for item in payload.lines:
        if item.consumable_id in seen:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="同一次更新不能重复提交同一耗材",
            )
        seen.add(item.consumable_id)
        row = by_consumable.get(item.consumable_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"盘点单不包含耗材（id={item.consumable_id}）",
            )
        row.counted_qty = item.counted_qty
        row.reason = item.reason
        row.updated_at = timestamp
        db.add(row)
    count.updated_at = timestamp
    db.commit()
    return count


def post_count(
    db: Session, *, count: InventoryCount, operator: User
) -> tuple[InventoryCount, bool]:
    if count.status == "posted":
        return count, False

    rows = db.scalars(
        select(InventoryCountLine)
        .where(InventoryCountLine.count_id == count.id)
        .order_by(InventoryCountLine.line_no)
    ).all()
    for row in rows:
        if row.counted_qty is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"第 {row.line_no} 行尚未录入实盘量",
            )

    posted_at = _now()
    warnings: list[str] = []
    for row in rows:
        consumable = db.get(Consumable, row.consumable_id, with_for_update=True)
        if consumable is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"耗材不存在（id={row.consumable_id}）",
            )
        period_delta = _period_delta(db, consumable.id, row.baseline_seq)
        expected_stock = row.baseline_stock + period_delta
        diff = row.counted_qty - row.baseline_stock
        stock_before = consumable.current_stock
        if stock_before + diff < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"第 {row.line_no} 行调整后库存为负（当前 {stock_before}"
                    f"{consumable.unit}，差异 {diff}），请重新核对实盘量"
                ),
            )
        if diff != 0:
            db.execute(
                update(Consumable)
                .where(Consumable.id == consumable.id)
                .values(current_stock=Consumable.current_stock + diff)
            )
            db.flush()
            db.refresh(consumable)
            stock_after = consumable.current_stock
            event = inventory_ledger.append_event(
                db,
                consumable=consumable,
                event_type=inventory_ledger.EVENT_INVENTORY_ADJUSTMENT,
                delta=diff,
                stock_before=stock_before,
                stock_after=stock_after,
                business_at=count.count_at,
                posted_at=posted_at,
                source_type=inventory_ledger.SOURCE_INVENTORY_COUNT,
                source_id=count.id,
                line_no=row.line_no,
                operator=operator,
                unit_snapshot=consumable.unit,
            )
            row.event_id = event.id
            db.add(
                OperationLog(
                    operator_id=operator.id,
                    role=operator.role,
                    action="inventory_adjustment",
                    target_type="consumable",
                    target_id=consumable.id,
                    before_value=json.dumps(
                        {
                            "consumable_id": consumable.id,
                            "current_stock": stock_before,
                        },
                        ensure_ascii=False,
                    ),
                    after_value=json.dumps(
                        {
                            "consumable_id": consumable.id,
                            "current_stock": stock_after,
                            "diff": diff,
                            "count_order_no": count.order_no,
                            "reason": row.reason,
                        },
                        ensure_ascii=False,
                    ),
                    remark=count.remark,
                )
            )
            consumable.updated_at = posted_at
        elif row.event_id is None:
            row.event_id = None

        row.diff = diff
        row.period_delta = period_delta
        row.updated_at = posted_at
        db.add(row)

        if expected_stock != consumable.current_stock - diff:
            warnings.append(
                f"第 {row.line_no} 行「{row.consumable_name}」盘点时点系统量 "
                f"{row.baseline_stock}，期间净变动 {period_delta}，应有 {expected_stock}"
                f"，实际 {consumable.current_stock - diff}，已按盘点时点快照调整"
            )

    count.status = "posted"
    count.posted_at = posted_at
    count.posted_by_id = operator.id
    count.posted_by_name = operator.name
    count.warning = "；".join(warnings) or None
    count.updated_at = posted_at
    db.add(count)
    db.commit()
    db.refresh(count)
    return count, True


def build_out(db: Session, count: InventoryCount) -> InventoryCountOut:
    rows = db.scalars(
        select(InventoryCountLine)
        .where(InventoryCountLine.count_id == count.id)
        .order_by(InventoryCountLine.line_no)
    ).all()
    lines: list[InventoryCountLineOut] = []
    recheck_required = False
    for row in rows:
        consumable = db.get(Consumable, row.consumable_id)
        current_stock = consumable.current_stock if consumable is not None else 0
        if count.status == "posted":
            period_delta = row.period_delta
            diff = row.diff
            row_recheck = False
        else:
            period_delta = _period_delta(db, row.consumable_id, row.baseline_seq)
            diff = (
                row.counted_qty - row.baseline_stock
                if row.counted_qty is not None
                else None
            )
            row_recheck = row.baseline_stock + period_delta != current_stock
        recheck_required = recheck_required or row_recheck
        lines.append(
            InventoryCountLineOut(
                line_no=row.line_no,
                consumable_id=row.consumable_id,
                consumable_name=row.consumable_name,
                unit=row.unit,
                baseline_stock=row.baseline_stock,
                baseline_seq=row.baseline_seq,
                counted_qty=row.counted_qty,
                period_delta=period_delta,
                expected_stock=row.baseline_stock + period_delta,
                current_stock=current_stock,
                diff=diff,
                reason=row.reason,
                event_id=row.event_id,
                recheck_required=row_recheck,
            )
        )
    return InventoryCountOut(
        id=count.id,
        order_no=count.order_no,
        request_id=count.request_id,
        status=count.status,
        count_at=count.count_at,
        counter_id=count.counter_id,
        counter_name=count.counter_name,
        counter_role=count.counter_role,
        reviewer_id=count.reviewer_id,
        reviewer_name=count.reviewer_name,
        reviewer_role=count.reviewer_role,
        posted_at=count.posted_at,
        posted_by_name=count.posted_by_name,
        remark=count.remark,
        warning=count.warning,
        recheck_required=recheck_required,
        lines=lines,
    )
