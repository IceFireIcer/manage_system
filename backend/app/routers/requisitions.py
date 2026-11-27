import csv
import io
import json
import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import business_time, inventory_ledger
from app.agent import service
from app.database import get_db
from app.deps import CurrentUser, require_role
from app.models import Consumable, OperationLog, Requisition, RequisitionOrder, User
from app.schemas_consumables import STATUS_ENABLED
from app.schemas_requisitions import (
    MAX_BACKFILL_DAYS,
    RequisitionCreate,
    RequisitionCreateOut,
    RequisitionDetailOut,
    RequisitionErrorOut,
    RequisitionItemOut,
    RequisitionListItemOut,
    RequisitionPageOut,
    RequisitionReceiptLineOut,
    RequisitionReceiptOut,
    RequisitionRecordOut,
    RequisitionRelatedOut,
    RequisitionRequestStatusOut,
    ReversalCreate,
)

router = APIRouter(prefix="/api/v1/requisitions", tags=["requisitions"])

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
VALID_TYPES = ("normal", "reversal")
VALID_STATUSES = ("active", "reversed")

CSV_EXPORT_LIMIT = 10000
CSV_HEADERS = ["时间", "耗材", "数量", "领用人", "领后剩余", "类型", "状态", "撤销原因", "备注"]
TYPE_NAMES = {"normal": "领用", "reversal": "红冲"}
STATUS_NAMES = {"active": "有效", "reversed": "已撤销"}

ManagerUser = Annotated[User, Depends(require_role("teacher", "admin"))]


class RowError(Exception):
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


def _submit_signature(items: list) -> list[list[int]]:
    return sorted([[item.consumable_id, item.quantity] for item in items])


def _load_json_list(value: str | None) -> list[dict]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _receipt_from_order(order: RequisitionOrder) -> RequisitionReceiptOut | None:
    rows = _load_json_list(order.receipt_json)
    if not rows:
        return None
    return RequisitionReceiptOut(
        order_no=order.order_no,
        request_id=order.request_id,
        status=order.status,
        owner_id=order.owner_id,
        owner_name=order.owner_name,
        operator_id=order.operator_id,
        operator_name=order.operator_name,
        operator_role=order.operator_role,
        business_at=order.business_at,
        posted_at=order.posted_at,
        remark=order.remark,
        lines=[RequisitionReceiptLineOut(**row) for row in rows],
    )


def _create_out_from_order(order: RequisitionOrder) -> RequisitionCreateOut:
    receipt = _receipt_from_order(order)
    lines = receipt.lines if receipt is not None else []
    items = [
        RequisitionItemOut(
            consumable_name=line.consumable_name,
            quantity=line.quantity,
            stock_after=line.stock_after,
        )
        for line in lines
    ]
    return RequisitionCreateOut(
        count=len(items),
        items=items,
        request_id=order.request_id,
        order_no=order.order_no,
        status=order.status,
        business_at=order.business_at,
        posted_at=order.posted_at,
        receipt=receipt,
    )


def _order_response(order: RequisitionOrder) -> JSONResponse:
    if order.status == "submitted":
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=jsonable_encoder(_create_out_from_order(order)),
        )
    if order.status == "rejected":
        errors = [RequisitionErrorOut(**row) for row in _load_json_list(order.errors_json)]
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=jsonable_encoder(
                {
                    "detail": order.error_detail or "请求已被拒绝",
                    "request_id": order.request_id,
                    "order_no": order.order_no,
                    "status": order.status,
                    "errors": errors,
                }
            ),
        )
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=jsonable_encoder(
            {
                "detail": "请求处理中，请稍后查询",
                "request_id": order.request_id,
                "order_no": order.order_no,
                "status": order.status,
            }
        ),
    )


def _resolve_repeat(
    order: RequisitionOrder, payload: RequisitionCreate, owner: User
) -> JSONResponse:
    signature = json.dumps(_submit_signature(payload.items))
    time_matches = True
    if payload.created_at is not None:
        requested_at = business_time.normalize(payload.created_at).replace(microsecond=0)
        time_matches = requested_at == order.business_at
    if (
        order.owner_id != owner.id
        or order.items_signature != signature
        or order.remark != payload.remark
        or not time_matches
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="请求标识已用于不同内容，请勿复用",
        )
    return _order_response(order)


def _mark_rejected(db: Session, order_id: int, error: RowError) -> None:
    order = db.get(RequisitionOrder, order_id)
    if order is None:
        return
    order.status = "rejected"
    order.error_detail = error.detail()
    order.errors_json = json.dumps([error.as_dict()], ensure_ascii=False)
    order.updated_at = business_time.now().replace(microsecond=0)
    db.commit()


def _resolve_owner(
    db: Session, payload: RequisitionCreate, current_user: User
) -> User:
    if payload.user_id is None or payload.user_id == current_user.id:
        return current_user
    if current_user.role == "student":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="学生只能登记本人领用，不能代他人登记",
        )
    owner = db.get(User, payload.user_id)
    if owner is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="领用人不存在",
        )
    return owner


def _can_view_order(order: RequisitionOrder, current_user: User) -> bool:
    if current_user.role == "student":
        return order.owner_id == current_user.id
    return order.class_id == current_user.class_id


def _build_query(
    db: Session,
    current_user: User,
    consumable_id: int | None,
    user_id: int | None,
    start_date: date | None,
    end_date: date | None,
    req_type: str | None,
    req_status: str | None,
):
    query = (
        select(Requisition, Consumable, User)
        .join(Consumable, Requisition.consumable_id == Consumable.id)
        .join(User, Requisition.user_id == User.id)
    )
    if current_user.role == "student":
        query = query.where(Requisition.user_id == current_user.id)
    else:
        query = query.where(Requisition.class_id == current_user.class_id)
        if user_id is not None:
            query = query.where(Requisition.user_id == user_id)
    if consumable_id is not None:
        query = query.where(Requisition.consumable_id == consumable_id)
    if start_date is not None:
        query = query.where(Requisition.created_at >= business_time.day_start(start_date))
    if end_date is not None:
        query = query.where(Requisition.created_at < business_time.next_day_start(end_date))
    if req_type is not None:
        query = query.where(Requisition.type == req_type)
    if req_status is not None:
        query = query.where(Requisition.status == req_status)
    return query


def _serialize_row(
    requisition: Requisition,
    consumable: Consumable,
    user: User,
    related: RequisitionRelatedOut | None = None,
) -> RequisitionListItemOut:
    return RequisitionListItemOut(
        id=requisition.id,
        created_at=requisition.created_at,
        consumable_name=consumable.name,
        consumable_spec=consumable.spec,
        quantity=requisition.quantity,
        user_id=requisition.user_id,
        user_name=user.name,
        stock_after=requisition.stock_after,
        type=requisition.type,
        status=requisition.status,
        reversal_of=requisition.reversal_of,
        reversal_reason=requisition.reversal_reason,
        remark=requisition.remark,
        related=related,
    )


def _related_records(
    db: Session, records: list[Requisition]
) -> tuple[dict[int, Requisition], dict[int, Requisition]]:
    origin_ids = {record.reversal_of for record in records if record.reversal_of is not None}
    reversed_ids = [
        record.id
        for record in records
        if record.type == "normal" and record.status == "reversed"
    ]

    origins = (
        {
            item.id: item
            for item in db.scalars(
                select(Requisition).where(Requisition.id.in_(origin_ids))
            )
        }
        if origin_ids
        else {}
    )
    reversals = (
        {
            item.reversal_of: item
            for item in db.scalars(
                select(Requisition).where(Requisition.reversal_of.in_(reversed_ids))
            )
        }
        if reversed_ids
        else {}
    )
    return origins, reversals


def _list_related(
    record: Requisition,
    origins: dict[int, Requisition],
    reversals: dict[int, Requisition],
) -> RequisitionRelatedOut | None:
    if record.reversal_of is not None:
        origin = origins.get(record.reversal_of)
        return _related_out(origin) if origin is not None else None
    if record.type == "normal" and record.status == "reversed":
        reversal = reversals.get(record.id)
        return _related_out(reversal) if reversal is not None else None
    return None


def _consume_params(
    consumable_id: Annotated[int | None, Query()] = None,
    user_id: Annotated[int | None, Query()] = None,
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
    req_type: Annotated[str | None, Query(alias="type")] = None,
    req_status: Annotated[str | None, Query(alias="status")] = None,
) -> dict:
    if req_type is not None and req_type not in VALID_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="type 参数仅支持 normal/reversal",
        )
    if req_status is not None and req_status not in VALID_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="status 参数仅支持 active/reversed",
        )
    if business_time.reversed_range(start_date, end_date):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=business_time.REVERSE_RANGE_DETAIL,
        )
    return {
        "consumable_id": consumable_id,
        "user_id": user_id,
        "start_date": start_date,
        "end_date": end_date,
        "req_type": req_type,
        "req_status": req_status,
    }


@router.get("")
def list_requisitions(
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    filters: Annotated[dict, Depends(_consume_params)] = None,
) -> RequisitionPageOut:
    query = _build_query(db=db, current_user=current_user, **filters)
    total = db.scalar(
        select(func.count()).select_from(query.subquery())
    ) or 0

    rows = db.execute(
        query.order_by(Requisition.created_at.desc(), Requisition.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    records = [record for record, _consumable, _user in rows]
    origins, reversals = _related_records(db, records)
    items = [
        _serialize_row(
            record,
            consumable,
            user,
            related=_list_related(record, origins, reversals),
        )
        for record, consumable, user in rows
    ]
    return RequisitionPageOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/export")
def export_requisitions_csv(
    db: Annotated[Session, Depends(get_db)],
    staff: Annotated[User, Depends(require_role("teacher", "admin"))],
    filters: Annotated[dict, Depends(_consume_params)] = None,
) -> Response:
    query = _build_query(db=db, current_user=staff, **filters)
    rows = db.execute(
        query.order_by(Requisition.created_at.desc(), Requisition.id.desc()).limit(CSV_EXPORT_LIMIT)
    ).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADERS)
    for record, consumable, user in rows:
        spec_suffix = f"（{consumable.spec}）" if consumable.spec else ""
        writer.writerow(
            [
                record.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                f"{consumable.name}{spec_suffix}",
                record.quantity,
                user.name,
                record.stock_after,
                TYPE_NAMES.get(record.type, record.type),
                STATUS_NAMES.get(record.status, record.status),
                record.reversal_reason or "",
                record.remark or "",
            ]
        )

    filename = f"requisitions_{business_time.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        content="\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/requests/{request_id}")
def get_request_status(
    request_id: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> RequisitionRequestStatusOut:
    order = db.scalars(
        select(RequisitionOrder).where(RequisitionOrder.request_id == request_id)
    ).first()
    if order is None or not _can_view_order(order, current_user):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="请求不存在"
        )
    receipt = _receipt_from_order(order) if order.status == "submitted" else None
    return RequisitionRequestStatusOut(
        request_id=order.request_id,
        order_no=order.order_no,
        status=order.status,
        receipt=receipt,
        errors=[RequisitionErrorOut(**row) for row in _load_json_list(order.errors_json)],
        error_detail=order.error_detail,
    )


@router.get("/orders/{order_no}")
def get_order_receipt(
    order_no: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> RequisitionReceiptOut:
    order = db.scalars(
        select(RequisitionOrder).where(RequisitionOrder.order_no == order_no)
    ).first()
    if order is None or not _can_view_order(order, current_user):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="回执不存在"
        )
    receipt = _receipt_from_order(order)
    if order.status != "submitted" or receipt is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="回执尚未生成，请按请求标识查询处理状态",
        )
    return receipt


def _validate_created_at(value: datetime) -> datetime:
    normalized = business_time.normalize(value)
    if normalized > business_time.now():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="领用时间不能晚于当前时间",
        )
    if normalized < business_time.backfill_earliest(MAX_BACKFILL_DAYS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"领用时间不能早于 {MAX_BACKFILL_DAYS} 天前，不支持补录更早的记录",
        )
    return normalized.replace(microsecond=0)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_requisition(
    payload: RequisitionCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> RequisitionCreateOut:
    request_id = payload.request_id or uuid.uuid4().hex
    owner = _resolve_owner(db, payload, current_user)

    existing = db.scalars(
        select(RequisitionOrder).where(RequisitionOrder.request_id == request_id)
    ).first()
    if existing is not None:
        return _resolve_repeat(existing, payload, owner)

    submitted_at = business_time.now().replace(microsecond=0)
    business_at = (
        _validate_created_at(payload.created_at)
        if payload.created_at is not None
        else submitted_at
    )

    order = RequisitionOrder(
        order_no=inventory_ledger.order_no_for(request_id, business_at),
        request_id=request_id,
        status="processing",
        owner_id=owner.id,
        owner_name=owner.name,
        operator_id=current_user.id,
        operator_name=current_user.name,
        operator_role=current_user.role,
        class_id=owner.class_id,
        business_at=business_at,
        remark=payload.remark,
        items_signature=json.dumps(_submit_signature(payload.items)),
    )
    db.add(order)
    db.flush()
    order_id = order.id
    db.commit()

    results: list[RequisitionItemOut] = []
    receipt_lines: list[RequisitionReceiptLineOut] = []
    created: list[Requisition] = []
    total_quantity = 0
    try:
        if owner.status != "enabled":
            raise RowError(
                field="user_id",
                code="owner_disabled",
                reason=f"领用人 {owner.name} 已被停用",
            )

        for row_number, item in enumerate(payload.items, start=1):
            field_path = f"items[{row_number - 1}]"
            consumable = db.get(Consumable, item.consumable_id, with_for_update=True)
            if consumable is None:
                raise RowError(
                    line_no=row_number,
                    field=f"{field_path}.consumable_id",
                    code="consumable_not_found",
                    reason=f"耗材不存在（id={item.consumable_id}）",
                )
            if consumable.status != STATUS_ENABLED:
                raise RowError(
                    line_no=row_number,
                    field=f"{field_path}.consumable_id",
                    code="consumable_disabled",
                    reason=f"耗材「{consumable.name}」已停用，不可领用",
                )

            stock_before = consumable.current_stock
            matched = db.execute(
                update(Consumable)
                .where(
                    Consumable.id == item.consumable_id,
                    Consumable.current_stock >= item.quantity,
                    Consumable.status == STATUS_ENABLED,
                )
                .values(current_stock=Consumable.current_stock - item.quantity)
            )
            if matched.rowcount == 0:
                db.flush()
                db.refresh(consumable)
                raise RowError(
                    line_no=row_number,
                    field=f"{field_path}.quantity",
                    code="insufficient_stock",
                    reason=(
                        f"{consumable.name}库存不足，仅剩 {consumable.current_stock}"
                        f"{consumable.unit or '件'}"
                    ),
                )

            db.flush()
            db.refresh(consumable)
            stock_after = consumable.current_stock
            line_posted_at = business_time.now().replace(microsecond=0)

            requisition = Requisition(
                consumable_id=item.consumable_id,
                user_id=owner.id,
                quantity=item.quantity,
                stock_after=stock_after,
                type="normal",
                status="active",
                class_id=consumable.class_id,
                created_at=business_at,
                remark=payload.remark,
            )
            db.add(requisition)
            db.flush()

            event = inventory_ledger.append_event(
                db,
                consumable=consumable,
                event_type=inventory_ledger.EVENT_REQUISITION,
                delta=-item.quantity,
                stock_before=stock_before,
                stock_after=stock_after,
                business_at=business_at,
                posted_at=line_posted_at,
                source_type=inventory_ledger.SOURCE_REQUISITION_ORDER,
                source_id=order_id,
                requisition_id=requisition.id,
                line_no=row_number,
                operator=current_user,
                unit_snapshot=consumable.unit,
            )
            results.append(
                RequisitionItemOut(
                    consumable_name=consumable.name,
                    quantity=item.quantity,
                    stock_after=stock_after,
                )
            )
            receipt_lines.append(
                RequisitionReceiptLineOut(
                    line_no=row_number,
                    event_id=event.id,
                    requisition_id=requisition.id,
                    consumable_id=consumable.id,
                    consumable_name=consumable.name,
                    unit=consumable.unit,
                    quantity=item.quantity,
                    stock_before=stock_before,
                    stock_after=stock_after,
                    business_at=business_at,
                    posted_at=line_posted_at,
                )
            )
            created.append(requisition)
            total_quantity += item.quantity

        posted_at = business_time.now().replace(microsecond=0)
        order.status = "submitted"
        order.posted_at = posted_at
        order.receipt_json = json.dumps(
            [line.model_dump(mode="json") for line in receipt_lines],
            ensure_ascii=False,
        )
        order.updated_at = posted_at
        db.add(
            OperationLog(
                operator_id=current_user.id,
                role=current_user.role,
                action="requisition_create",
                target_type="requisition",
                target_id=created[0].id if created else None,
                before_value=None,
                after_value=json.dumps(
                    {
                        "order_no": order.order_no,
                        "request_id": request_id,
                        "owner_id": owner.id,
                        "row_count": len(results),
                        "total_quantity": total_quantity,
                        "business_at": business_at.isoformat(),
                        "posted_at": posted_at.isoformat(),
                        "event_ids": [line.event_id for line in receipt_lines],
                        "items": [item.model_dump() for item in results],
                    },
                    ensure_ascii=False,
                ),
            )
        )
        db.commit()
    except RowError as exc:
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
            RowError(
                field="request",
                code="internal_error",
                reason="提交处理失败，请稍后凭请求标识查询结果",
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="提交失败，请稍后凭请求标识查询结果",
        ) from exc

    service.clear_cache()
    db.refresh(order)
    return _create_out_from_order(order)


def _record_out(
    record: Requisition, consumable: Consumable | None, user: User | None
) -> RequisitionRecordOut:
    return RequisitionRecordOut(
        id=record.id,
        consumable_id=record.consumable_id,
        consumable_name=consumable.name if consumable else f"耗材#{record.consumable_id}",
        unit=consumable.unit if consumable else "",
        user_id=record.user_id,
        user_name=user.name if user else f"用户#{record.user_id}",
        quantity=record.quantity,
        stock_after=record.stock_after,
        type=record.type,
        status=record.status,
        reversal_of=record.reversal_of,
        reversal_reason=record.reversal_reason,
        created_at=record.created_at,
        remark=record.remark,
    )


def _related_out(record: Requisition) -> RequisitionRelatedOut:
    return RequisitionRelatedOut(
        id=record.id,
        type=record.type,
        status=record.status,
        quantity=record.quantity,
        stock_after=record.stock_after,
        created_at=record.created_at,
        reversal_reason=record.reversal_reason,
    )


@router.post("/{requisition_id}/reversal")
def reverse_requisition(
    requisition_id: int,
    payload: ReversalCreate,
    db: Annotated[Session, Depends(get_db)],
    manager: ManagerUser,
) -> RequisitionRecordOut:
    requisition = db.get(Requisition, requisition_id)
    if requisition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="流水不存在"
        )
    if requisition.class_id != manager.class_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="无权限处理该流水"
        )
    if requisition.type == "reversal":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="红冲流水不可被撤销"
        )
    if requisition.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该流水已撤销，不可重复撤销",
        )

    consumable = db.get(Consumable, requisition.consumable_id)
    if consumable is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="流水不存在"
        )
    stock_before = consumable.current_stock

    try:
        claimed = db.execute(
            update(Requisition)
            .where(
                Requisition.id == requisition_id,
                Requisition.type == "normal",
                Requisition.status == "active",
            )
            .values(status="reversed")
        )
        if claimed.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该流水已撤销，不可重复撤销",
            )
        db.expire(requisition)

        db.execute(
            update(Consumable)
            .where(Consumable.id == requisition.consumable_id)
            .values(current_stock=Consumable.current_stock + requisition.quantity)
        )
        db.flush()
        db.refresh(consumable)
        stock_after = consumable.current_stock

        reversal = Requisition(
            consumable_id=requisition.consumable_id,
            user_id=requisition.user_id,
            quantity=requisition.quantity,
            stock_after=stock_after,
            type="reversal",
            status="active",
            reversal_of=requisition.id,
            reversal_reason=payload.reason,
            class_id=requisition.class_id,
            created_at=business_time.now().replace(microsecond=0),
            remark=payload.remark,
        )
        db.add(reversal)
        db.flush()

        db.add(
            OperationLog(
                operator_id=manager.id,
                role=manager.role,
                action="requisition_reverse",
                target_type="requisition",
                target_id=requisition.id,
                before_value=json.dumps(
                    {
                        "requisition_id": requisition.id,
                        "status": "active",
                        "stock_before": stock_before,
                    },
                    ensure_ascii=False,
                ),
                after_value=json.dumps(
                    {
                        "requisition_id": requisition.id,
                        "reversal_id": reversal.id,
                        "status": "reversed",
                        "stock_before": stock_before,
                        "stock_after": stock_after,
                        "consumable_id": requisition.consumable_id,
                        "quantity": requisition.quantity,
                        "reason": payload.reason,
                    },
                    ensure_ascii=False,
                ),
            )
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise

    service.clear_cache()
    db.refresh(reversal)
    db.refresh(consumable)
    return _record_out(reversal, consumable, db.get(User, reversal.user_id))


@router.get("/{requisition_id}")
def get_requisition(
    requisition_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> RequisitionDetailOut:
    requisition = db.get(Requisition, requisition_id)
    if requisition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="流水不存在"
        )
    if current_user.role == "student":
        if requisition.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="流水不存在"
            )
    elif requisition.class_id != current_user.class_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="流水不存在"
        )

    detail = RequisitionDetailOut(
        **_record_out(
            requisition,
            db.get(Consumable, requisition.consumable_id),
            db.get(User, requisition.user_id),
        ).model_dump()
    )

    if requisition.reversal_of is not None:
        origin = db.get(Requisition, requisition.reversal_of)
        if origin is not None:
            detail.origin = _related_out(origin)
    reversal = db.scalars(
        select(Requisition).where(Requisition.reversal_of == requisition.id)
    ).first()
    if reversal is not None:
        detail.reversal = _related_out(reversal)
    return detail
