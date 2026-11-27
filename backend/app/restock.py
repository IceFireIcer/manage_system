import json
import uuid
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import business_time, inventory_ledger, stock_alerts
from app.agent import rules
from app.consumption import local_day_start, window_flow
from app.models import (
    Consumable,
    InventoryEvent,
    OperationLog,
    RestockCorrection,
    RestockDisposition,
    RestockLine,
    RestockOrder,
    User,
)
from app.schemas_consumables import STATUS_ENABLED
from app.schemas_restock import (
    DISPOSITION_STATUSES,
    MAX_BACKFILL_DAYS,
    RestockCorrectionCreate,
    RestockCorrectionOut,
    RestockErrorOut,
    RestockItemIn,
    RestockLineOut,
    RestockReceiptOut,
    RestockRequestStatusOut,
    SuggestionDispositionOut,
)


class RestockRowError(Exception):
    def __init__(
        self,
        *,
        field: str,
        code: str,
        reason: str,
        line_no: int | None = None,
        status_code: int = status.HTTP_400_BAD_REQUEST,
    ) -> None:
        super().__init__(reason)
        self.field = field
        self.code = code
        self.reason = reason
        self.line_no = line_no
        self.status_code = status_code

    def detail(self) -> str:
        if self.line_no is None:
            return self.reason
        return f"第 {self.line_no} 行：{self.reason}"

    def as_dict(self) -> dict:
        return {
            "line_no": self.line_no,
            "field": self.field,
            "code": self.code,
            "reason": self.reason,
        }

    def as_http(self) -> HTTPException:
        headers = {"X-Error-Code": self.code, "X-Error-Field": self.field}
        if self.line_no is not None:
            headers["X-Error-Row"] = str(self.line_no)
        return HTTPException(
            status_code=self.status_code, detail=self.detail(), headers=headers
        )


def _now() -> datetime:
    return business_time.now().replace(microsecond=0)


def _signature(items: list[RestockItemIn]) -> str:
    return json.dumps(sorted([[item.consumable_id, item.quantity] for item in items]))


def _load_json_list(value: str | None) -> list[dict]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _normalize_received_at(value: datetime | None) -> datetime:
    if value is None:
        return _now()
    normalized = business_time.normalize(value)
    if normalized > business_time.now():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="实收日期不能晚于当前时间",
        )
    if normalized < business_time.backfill_earliest(MAX_BACKFILL_DAYS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"实收日期不能早于 {MAX_BACKFILL_DAYS} 天前，不支持补录更早的记录",
        )
    return normalized.replace(microsecond=0)


def _evaluate(db: Session, consumable: Consumable) -> rules.Evaluation | None:
    reference = business_time.now()
    q7_start = local_day_start(rules.WINDOW_DAYS - 1, reference)
    prev_start = local_day_start(2 * rules.WINDOW_DAYS - 1, reference)
    q7_flow = window_flow(db, consumable.id, q7_start, reference)
    prev_flow = window_flow(
        db, consumable.id, prev_start, q7_start, end_inclusive=False
    )
    return rules.evaluate(
        current_stock=consumable.current_stock,
        safety_stock=consumable.safety_stock,
        q7=q7_flow.attributed_net,
        prev_q7=prev_flow.attributed_net,
        issued=q7_flow.issued,
        reversed_quantity=q7_flow.attributed_reversed,
        records=q7_flow.records,
    )


def _latest_disposition(db: Session, consumable_id: int) -> RestockDisposition | None:
    return db.scalars(
        select(RestockDisposition)
        .where(RestockDisposition.consumable_id == consumable_id)
        .order_by(RestockDisposition.id.desc())
    ).first()


def _record_disposition(
    db: Session,
    *,
    consumable: Consumable,
    operator: User,
    order: RestockOrder,
    has_suggestion: bool,
) -> None:
    latest = _latest_disposition(db, consumable.id)
    if not has_suggestion and latest is None:
        return
    if latest is not None and latest.status == "resolved" and not has_suggestion:
        return

    gap = max(consumable.safety_stock - consumable.current_stock, 0)
    next_status = "resolved" if gap == 0 else "partial"
    timestamp = _now()
    if latest is not None and latest.status != "resolved":
        latest.status = next_status
        latest.handler_id = operator.id
        latest.handler_name = operator.name
        latest.order_id = order.id
        latest.order_no = order.order_no
        latest.updated_at = timestamp
        db.add(latest)
        return

    db.add(
        RestockDisposition(
            consumable_id=consumable.id,
            class_id=consumable.class_id,
            status=next_status,
            handler_id=operator.id,
            handler_name=operator.name,
            order_id=order.id,
            order_no=order.order_no,
            updated_at=timestamp,
        )
    )


def _touch_disposition_after_correction(
    db: Session, *, consumable: Consumable, operator: User, order: RestockOrder
) -> None:
    if consumable.safety_stock <= consumable.current_stock:
        return
    latest = _latest_disposition(db, consumable.id)
    timestamp = _now()
    if latest is not None and latest.status != "resolved":
        latest.status = "partial"
        latest.handler_id = operator.id
        latest.handler_name = operator.name
        latest.order_id = order.id
        latest.order_no = order.order_no
        latest.updated_at = timestamp
        db.add(latest)
        return
    db.add(
        RestockDisposition(
            consumable_id=consumable.id,
            class_id=consumable.class_id,
            status="pending",
            handler_id=operator.id,
            handler_name=operator.name,
            order_id=order.id,
            order_no=order.order_no,
            updated_at=timestamp,
        )
    )


def _mark_rejected(db: Session, order_id: int, error: RestockRowError) -> None:
    order = db.get(RestockOrder, order_id)
    if order is None:
        return
    order.status = "rejected"
    order.error_detail = error.detail()
    order.errors_json = json.dumps([error.as_dict()], ensure_ascii=False)
    order.updated_at = _now()
    db.commit()


def _resolve_repeat(
    order: RestockOrder,
    *,
    items: list[RestockItemIn],
    operator: User,
    signature: str,
    source: str | None,
    remark: str | None,
    received_at: datetime | None,
) -> None:
    time_matches = True
    if received_at is not None:
        time_matches = _normalize_received_at(received_at) == order.business_at
    if (
        order.operator_id != operator.id
        or order.class_id != operator.class_id
        or order.items_signature != signature
        or order.source != source
        or order.remark != remark
        or not time_matches
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="请求标识已用于不同内容，请勿复用",
        )


def create_restock_order(
    db: Session,
    *,
    items: list[RestockItemIn],
    operator: User,
    request_id: str | None = None,
    received_at: datetime | None = None,
    source: str | None = None,
    remark: str | None = None,
) -> RestockOrder:
    request_id = request_id or uuid.uuid4().hex
    source = (source or "").strip() or None
    signature = _signature(items)

    existing = db.scalars(
        select(RestockOrder).where(RestockOrder.request_id == request_id)
    ).first()
    if existing is not None:
        _resolve_repeat(
            existing,
            items=items,
            operator=operator,
            signature=signature,
            source=source,
            remark=remark,
            received_at=received_at,
        )
        return existing

    submitted_at = _now()
    business_at = _normalize_received_at(received_at)
    order = RestockOrder(
        order_no=inventory_ledger.restock_order_no_for(request_id, business_at),
        request_id=request_id,
        status="processing",
        source=source,
        operator_id=operator.id,
        operator_name=operator.name,
        operator_role=operator.role,
        class_id=operator.class_id,
        business_at=business_at,
        remark=remark,
        items_signature=signature,
        created_at=submitted_at,
        updated_at=submitted_at,
    )
    db.add(order)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        conflict = db.scalars(
            select(RestockOrder).where(RestockOrder.request_id == request_id)
        ).first()
        if conflict is None:
            raise
        _resolve_repeat(
            conflict,
            items=items,
            operator=operator,
            signature=signature,
            source=source,
            remark=remark,
            received_at=received_at,
        )
        return conflict
    order_id = order.id
    db.commit()

    receipt_lines: list[RestockLineOut] = []
    try:
        for row_number, item in enumerate(items, start=1):
            field_path = f"items[{row_number - 1}]"
            consumable = db.get(Consumable, item.consumable_id, with_for_update=True)
            if consumable is None or consumable.class_id != operator.class_id:
                raise RestockRowError(
                    line_no=row_number,
                    field=f"{field_path}.consumable_id",
                    code="consumable_not_found",
                    reason=f"耗材不存在（id={item.consumable_id}）",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            if consumable.status != STATUS_ENABLED:
                raise RestockRowError(
                    line_no=row_number,
                    field=f"{field_path}.consumable_id",
                    code="consumable_disabled",
                    reason=f"耗材「{consumable.name}」已停用，不可入库",
                )

            has_suggestion = _evaluate(db, consumable) is not None
            stock_before = consumable.current_stock
            matched = db.execute(
                update(Consumable)
                .where(
                    Consumable.id == item.consumable_id,
                    Consumable.status == STATUS_ENABLED,
                )
                .values(current_stock=Consumable.current_stock + item.quantity)
            )
            if matched.rowcount == 0:
                db.flush()
                db.refresh(consumable)
                raise RestockRowError(
                    line_no=row_number,
                    field=f"{field_path}.consumable_id",
                    code="consumable_disabled",
                    reason=f"耗材「{consumable.name}」状态已变更，不可入库",
                )

            db.flush()
            db.refresh(consumable)
            stock_after = consumable.current_stock
            line_posted_at = _now()
            consumable.updated_at = line_posted_at

            line = RestockLine(
                order_id=order_id,
                line_no=row_number,
                consumable_id=consumable.id,
                consumable_name=consumable.name,
                unit=consumable.unit,
                quantity=item.quantity,
                stock_before=stock_before,
                stock_after=stock_after,
                corrected_quantity=0,
                created_at=line_posted_at,
            )
            db.add(line)
            db.flush()

            event = inventory_ledger.append_event(
                db,
                consumable=consumable,
                event_type=inventory_ledger.EVENT_RESTOCK,
                delta=item.quantity,
                stock_before=stock_before,
                stock_after=stock_after,
                business_at=business_at,
                posted_at=line_posted_at,
                source_type=inventory_ledger.SOURCE_RESTOCK_ORDER,
                source_id=order_id,
                line_no=row_number,
                operator=operator,
                unit_snapshot=consumable.unit,
            )
            line.event_id = event.id

            db.add(
                OperationLog(
                    operator_id=operator.id,
                    role=operator.role,
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
                            "quantity": item.quantity,
                            "order_no": order.order_no,
                        },
                        ensure_ascii=False,
                    ),
                    remark=remark,
                )
            )
            _record_disposition(
                db,
                consumable=consumable,
                operator=operator,
                order=order,
                has_suggestion=has_suggestion,
            )
            stock_alerts.sync_consumable(
                db,
                consumable,
                order_no=order.order_no,
                source="restock",
                at=line_posted_at,
            )
            receipt_lines.append(
                RestockLineOut(
                    line_no=row_number,
                    event_id=event.id,
                    consumable_id=consumable.id,
                    consumable_name=consumable.name,
                    unit=consumable.unit,
                    quantity=item.quantity,
                    stock_before=stock_before,
                    stock_after=stock_after,
                    corrected_quantity=0,
                    business_at=business_at,
                    posted_at=line_posted_at,
                )
            )

        posted_at = _now()
        order.status = "submitted"
        order.posted_at = posted_at
        order.receipt_json = json.dumps(
            [line.model_dump(mode="json") for line in receipt_lines],
            ensure_ascii=False,
        )
        order.updated_at = posted_at
        db.commit()
    except RestockRowError as exc:
        db.rollback()
        _mark_rejected(db, order_id, exc)
        raise exc.as_http() from None
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        _mark_rejected(
            db,
            order_id,
            RestockRowError(
                field="request",
                code="internal_error",
                reason="提交处理失败，请稍后凭请求标识查询结果",
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="入库失败，请稍后凭请求标识查询结果",
        ) from exc

    db.refresh(order)
    return order


def get_order_by_no(db: Session, order_no: str) -> RestockOrder | None:
    return db.scalars(
        select(RestockOrder).where(RestockOrder.order_no == order_no)
    ).first()


def get_order_by_request(db: Session, request_id: str) -> RestockOrder | None:
    return db.scalars(
        select(RestockOrder).where(RestockOrder.request_id == request_id)
    ).first()


def _correction_out(db: Session, correction: RestockCorrection) -> RestockCorrectionOut:
    order = db.get(RestockOrder, correction.order_id)
    line = db.scalars(
        select(RestockLine).where(
            RestockLine.order_id == correction.order_id,
            RestockLine.line_no == correction.line_no,
        )
    ).first()
    event = db.get(InventoryEvent, correction.event_id) if correction.event_id else None
    return RestockCorrectionOut(
        correction_id=correction.id,
        request_id=correction.request_id,
        order_no=order.order_no if order else f"#{correction.order_id}",
        line_no=correction.line_no,
        consumable_id=line.consumable_id if line else (event.consumable_id if event else 0),
        consumable_name=line.consumable_name if line else "",
        unit=line.unit if line else (event.unit_snapshot if event else ""),
        quantity=correction.quantity,
        stock_before=event.stock_before if event else (line.stock_before if line else 0),
        stock_after=event.stock_after if event else (line.stock_after if line else 0),
        reason=correction.reason,
        business_at=correction.business_at,
        posted_at=correction.posted_at,
        event_id=correction.event_id,
    )


def receipt_from_order(db: Session, order: RestockOrder) -> RestockReceiptOut:
    rows = db.scalars(
        select(RestockLine)
        .where(RestockLine.order_id == order.id)
        .order_by(RestockLine.line_no)
    ).all()
    corrections = db.scalars(
        select(RestockCorrection)
        .where(RestockCorrection.order_id == order.id)
        .order_by(RestockCorrection.id)
    ).all()
    lines = [
        RestockLineOut(
            line_no=row.line_no,
            event_id=row.event_id or 0,
            consumable_id=row.consumable_id,
            consumable_name=row.consumable_name,
            unit=row.unit,
            quantity=row.quantity,
            stock_before=row.stock_before,
            stock_after=row.stock_after,
            corrected_quantity=row.corrected_quantity,
            business_at=order.business_at,
            posted_at=order.posted_at or order.created_at,
        )
        for row in rows
    ]
    return RestockReceiptOut(
        order_no=order.order_no,
        request_id=order.request_id,
        status=order.status,
        source=order.source,
        operator_id=order.operator_id,
        operator_name=order.operator_name,
        operator_role=order.operator_role,
        business_at=order.business_at,
        posted_at=order.posted_at,
        remark=order.remark,
        lines=lines,
        corrections=[_correction_out(db, item) for item in corrections],
    )


def request_status(db: Session, order: RestockOrder) -> RestockRequestStatusOut:
    receipt = receipt_from_order(db, order) if order.status == "submitted" else None
    return RestockRequestStatusOut(
        request_id=order.request_id,
        order_no=order.order_no,
        status=order.status,
        receipt=receipt,
        errors=[RestockErrorOut(**row) for row in _load_json_list(order.errors_json)],
        error_detail=order.error_detail,
    )


def correct_restock_order(
    db: Session,
    *,
    order: RestockOrder,
    payload: RestockCorrectionCreate,
    operator: User,
) -> RestockCorrectionOut:
    request_id = payload.request_id or uuid.uuid4().hex
    existing = db.scalars(
        select(RestockCorrection).where(RestockCorrection.request_id == request_id)
    ).first()
    if existing is not None:
        if (
            existing.order_id != order.id
            or existing.line_no != payload.line_no
            or existing.quantity != payload.quantity
            or existing.reason != payload.reason
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="请求标识已用于不同内容，请勿复用",
            )
        return _correction_out(db, existing)

    line = db.scalars(
        select(RestockLine).where(
            RestockLine.order_id == order.id,
            RestockLine.line_no == payload.line_no,
        )
    ).first()
    if line is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"原入库单不存在第 {payload.line_no} 行",
        )
    remaining = line.quantity - line.corrected_quantity
    if payload.quantity > remaining:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"该行最多可扣回 {remaining}{line.unit}",
        )

    consumable = db.get(Consumable, line.consumable_id, with_for_update=True)
    if consumable is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="耗材不存在"
        )
    if consumable.current_stock < payload.quantity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"当前库存仅 {consumable.current_stock}{consumable.unit}，不足以扣回",
        )

    business_at = _normalize_received_at(payload.received_at)
    stock_before = consumable.current_stock
    db.execute(
        update(Consumable)
        .where(Consumable.id == consumable.id)
        .values(current_stock=Consumable.current_stock - payload.quantity)
    )
    db.flush()
    db.refresh(consumable)
    stock_after = consumable.current_stock
    posted_at = _now()
    consumable.updated_at = posted_at

    correction = RestockCorrection(
        order_id=order.id,
        line_no=line.line_no,
        request_id=request_id,
        quantity=payload.quantity,
        reason=payload.reason,
        operator_id=operator.id,
        operator_name=operator.name,
        operator_role=operator.role,
        class_id=order.class_id,
        business_at=business_at,
        posted_at=posted_at,
        remark=payload.remark,
        created_at=posted_at,
    )
    db.add(correction)
    db.flush()

    event = inventory_ledger.append_event(
        db,
        consumable=consumable,
        event_type=inventory_ledger.EVENT_RESTOCK_CORRECTION,
        delta=-payload.quantity,
        stock_before=stock_before,
        stock_after=stock_after,
        business_at=business_at,
        posted_at=posted_at,
        source_type=inventory_ledger.SOURCE_RESTOCK_ORDER,
        source_id=order.id,
        line_no=line.line_no,
        operator=operator,
        unit_snapshot=consumable.unit,
    )
    correction.event_id = event.id
    line.corrected_quantity += payload.quantity

    db.add(
        OperationLog(
            operator_id=operator.id,
            role=operator.role,
            action="restock_correction",
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
                    "quantity": -payload.quantity,
                    "order_no": order.order_no,
                    "reason": payload.reason,
                },
                ensure_ascii=False,
            ),
            remark=payload.remark,
        )
    )
    _touch_disposition_after_correction(
        db, consumable=consumable, operator=operator, order=order
    )
    stock_alerts.sync_consumable(
        db,
        consumable,
        order_no=order.order_no,
        source="restock_correction",
        at=posted_at,
    )
    db.commit()
    db.refresh(correction)
    return _correction_out(db, correction)


def update_disposition(
    db: Session,
    *,
    consumable: Consumable,
    operator: User,
    next_status: str,
    note: str | None,
) -> SuggestionDispositionOut:
    if next_status not in DISPOSITION_STATUSES or next_status == "partial":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="处置状态仅支持 pending/awaiting/resolved",
        )
    latest = _latest_disposition(db, consumable.id)
    timestamp = _now()
    if latest is not None and latest.status != "resolved":
        latest.status = next_status
        latest.handler_id = operator.id
        latest.handler_name = operator.name
        latest.note = note
        latest.updated_at = timestamp
        db.add(latest)
        target = latest
    else:
        target = RestockDisposition(
            consumable_id=consumable.id,
            class_id=consumable.class_id,
            status=next_status,
            handler_id=operator.id,
            handler_name=operator.name,
            note=note,
            updated_at=timestamp,
        )
        db.add(target)
    db.commit()
    db.refresh(target)
    return SuggestionDispositionOut(
        consumable_id=consumable.id,
        status=target.status,
        handler_name=target.handler_name,
        order_no=target.order_no,
        note=target.note,
        updated_at=target.updated_at,
    )


def attach_dispositions(
    db: Session, items: list
) -> list:
    if not items:
        return items
    ids = [item.consumable_id for item in items]
    rows = db.scalars(
        select(RestockDisposition)
        .where(RestockDisposition.consumable_id.in_(ids))
        .order_by(RestockDisposition.id)
    ).all()
    latest: dict[int, RestockDisposition] = {row.consumable_id: row for row in rows}
    result = []
    for item in items:
        row = latest.get(item.consumable_id)
        if row is None:
            disposition_status = "pending"
        elif item.gap > 0:
            disposition_status = "pending" if row.status == "resolved" else row.status
        else:
            disposition_status = "resolved"
        result.append(
            item.model_copy(
                update={
                    "disposition_status": disposition_status,
                    "disposition_handler_name": row.handler_name if row else None,
                    "disposition_order_no": row.order_no if row else None,
                    "disposition_updated_at": row.updated_at if row else None,
                }
            )
        )
    return result


