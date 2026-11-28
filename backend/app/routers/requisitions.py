import csv
import io
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import and_, case, delete, func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app import business_time, inventory_ledger, stock_alerts
from app import consumption as consumption_core
from app.agent import service
from app.auth_logs import trim_audit_logs
from app.database import get_db
from app.db import concurrency
from app.deps import CurrentUser, require_role
from app.models import (
    Consumable,
    Course,
    CourseRecipeItem,
    CourseRecipeSnapshot,
    CourseSession,
    InventoryCount,
    InventoryCountLine,
    InventoryEvent,
    OperationLog,
    Requisition,
    RequisitionCorrection,
    RequisitionOrder,
    RequisitionReturn,
    RequisitionScrap,
    User,
)
from app.operation_log import record_operation
from app.schemas_consumables import STATUS_ENABLED
from app.schemas_requisitions import (
    MAX_BACKFILL_DAYS,
    ControlledBackfillCreate,
    CorrectionCreate,
    CorrectionHandle,
    CorrectionOut,
    FootprintItemOut,
    FootprintOut,
    FootprintSupportOut,
    FootprintUnitTotalOut,
    RequisitionConsumableOptionOut,
    RequisitionCreate,
    RequisitionCreateOut,
    RequisitionDetailOut,
    RequisitionErrorOut,
    RequisitionExportPreviewOut,
    RequisitionItemOut,
    RequisitionListItemOut,
    RequisitionPageOut,
    RequisitionReceiptLineOut,
    RequisitionReceiptOut,
    RequisitionRecordOut,
    RequisitionRelatedOut,
    RequisitionRequestStatusOut,
    RequisitionReturnOut,
    RequisitionScrapOut,
    ReturnCreate,
    ReversalCreate,
    ScrapCreate,
)

router = APIRouter(prefix="/api/v1/requisitions", tags=["requisitions"])

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
VALID_TYPES = ("normal", "reversal")
VALID_STATUSES = ("active", "reversed")

CSV_EXPORT_LIMIT = 10000
CSV_EXPORT_MAX_ROWS = 200000
EXPORT_COLUMNS = (
    "id",
    "order_no",
    "created_at",
    "posted_at",
    "consumable_name",
    "consumable_spec",
    "unit",
    "quantity",
    "user_name",
    "stock_after",
    "type",
    "status",
    "related",
    "reversal_reason",
    "remark",
    "operator_name",
)
EXPORT_ADMIN_COLUMNS = (
    "user_account",
    "operator_account",
)
EXPORT_COLUMN_LABELS = {
    "id": "流水号",
    "order_no": "单号",
    "created_at": "业务时间",
    "posted_at": "入账时间",
    "consumable_name": "耗材",
    "consumable_spec": "规格",
    "unit": "单位",
    "quantity": "数量",
    "user_name": "领用人",
    "stock_after": "变动后库存",
    "type": "类型",
    "status": "状态",
    "related": "关联红冲",
    "reversal_reason": "撤销原因",
    "remark": "备注",
    "user_account": "领用人账号",
    "operator_name": "经办人",
    "operator_account": "经办账号",
}
EXPORT_NUMERIC_COLUMNS = ("quantity", "stock_after")
TYPE_NAMES = {"normal": "领用", "reversal": "红冲"}
STATUS_NAMES = {"active": "有效", "reversed": "已撤销"}

FOOTPRINT_WINDOWS = (7, 30, 90)
DEFAULT_FOOTPRINT_WINDOW = 90

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
        backfill_reason=order.backfill_reason,
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
        or (order.backfill_reason or None) != (payload.backfill_reason or None)
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



def _returned_total(db: Session, requisition_id: int) -> int:
    value = db.scalar(
        select(func.coalesce(func.sum(RequisitionReturn.quantity), 0)).where(
            RequisitionReturn.requisition_id == requisition_id
        )
    )
    return int(value or 0)


def _scrapped_total(db: Session, requisition_id: int) -> int:
    value = db.scalar(
        select(func.coalesce(func.sum(RequisitionScrap.quantity), 0)).where(
            RequisitionScrap.requisition_id == requisition_id
        )
    )
    return int(value or 0)


def _returnable_quantity(requisition: Requisition, returned: int, scrapped: int) -> int:
    if requisition.type != "normal":
        return 0
    return max(0, requisition.quantity - returned - scrapped)


def _serialize_row(
    db: Session,
    requisition: Requisition,
    consumable: Consumable,
    user: User,
    related: RequisitionRelatedOut | None = None,
    correction: RequisitionCorrection | None = None,
) -> RequisitionListItemOut:
    linked_count = (
        db.get(InventoryCount, requisition.linked_count_id)
        if requisition.linked_count_id is not None
        else None
    )
    return RequisitionListItemOut(
        id=requisition.id,
        created_at=requisition.created_at,
        posted_at=requisition.posted_at,
        consumable_name=requisition.name_snapshot or consumable.name,
        consumable_spec=requisition.spec_snapshot or consumable.spec,
        unit=requisition.unit_snapshot,
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
        order_no=requisition.order_no,
        reentry_of=requisition.reentry_of,
        correction_status=correction.status if correction is not None else None,
        backfill_reason=requisition.backfill_reason,
        backfill_mode=requisition.backfill_mode,
        linked_event_id=requisition.linked_event_id,
        linked_count_id=requisition.linked_count_id,
        linked_count_order_no=linked_count.order_no if linked_count else None,
        operator_id=requisition.operator_id,
        operator_name=requisition.operator_name,
        operator_role=requisition.operator_role,
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


def _chunked(values: list[int], size: int = 400) -> list[list[int]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _export_related(db: Session, records: list[Requisition]) -> dict[int, Requisition]:
    origin_ids = sorted(
        {record.reversal_of for record in records if record.reversal_of is not None}
    )
    origins: dict[int, Requisition] = {}
    for chunk in _chunked(origin_ids):
        for item in db.scalars(select(Requisition).where(Requisition.id.in_(chunk))):
            origins[item.id] = item

    reversed_ids = sorted(
        {
            record.id
            for record in records
            if record.type == "normal" and record.status == "reversed"
        }
    )
    reversals: dict[int, Requisition] = {}
    for chunk in _chunked(reversed_ids):
        for item in db.scalars(
            select(Requisition)
            .where(Requisition.reversal_of.in_(chunk))
            .order_by(Requisition.id)
        ):
            reversals.setdefault(item.reversal_of, item)

    related: dict[int, Requisition] = {}
    for record in records:
        if record.reversal_of is not None:
            candidate = origins.get(record.reversal_of)
        elif record.type == "normal" and record.status == "reversed":
            candidate = reversals.get(record.id)
        else:
            candidate = None
        if candidate is not None:
            related[record.id] = candidate
    return related


def _export_operator_accounts(db: Session, records: list[Requisition]) -> dict[int, str]:
    operator_ids = sorted(
        {record.operator_id for record in records if record.operator_id is not None}
    )
    accounts: dict[int, str] = {}
    for chunk in _chunked(operator_ids):
        for user_id, account in db.execute(
            select(User.id, User.account).where(User.id.in_(chunk))
        ):
            accounts[user_id] = account
    return accounts


def _export_columns(is_admin: bool) -> tuple[str, ...]:
    if is_admin:
        return EXPORT_COLUMNS + EXPORT_ADMIN_COLUMNS
    return EXPORT_COLUMNS


def _export_related_label(record: Requisition, related: Requisition | None) -> str:
    if related is None:
        return ""
    label = "原领用" if record.reversal_of is not None else "红冲"
    suffix = f" {related.order_no}" if related.order_no else ""
    return f"{label} #{related.id}{suffix}"


def _export_cell(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, int):
        return value
    text = str(value)
    if text.lstrip()[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def _export_row(
    record: Requisition,
    consumable: Consumable,
    user: User,
    related: Requisition | None,
    accounts: dict[int, str],
) -> dict:
    return {
        "id": record.id,
        "order_no": record.order_no,
        "created_at": record.created_at,
        "posted_at": record.posted_at,
        "consumable_name": record.name_snapshot or consumable.name,
        "consumable_spec": record.spec_snapshot or consumable.spec,
        "unit": record.unit_snapshot or consumable.unit,
        "quantity": record.quantity,
        "user_name": user.name,
        "stock_after": record.stock_after,
        "type": TYPE_NAMES.get(record.type, record.type),
        "status": STATUS_NAMES.get(record.status, record.status),
        "related": _export_related_label(record, related),
        "reversal_reason": record.reversal_reason,
        "remark": record.remark,
        "user_account": user.account,
        "operator_name": record.operator_name,
        "operator_account": accounts.get(record.operator_id),
    }


def _export_text(
    columns: tuple[str, ...],
    rows: list,
    related: dict[int, Requisition],
    accounts: dict[int, str],
    *,
    include_header: bool,
) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    if include_header:
        writer.writerow([EXPORT_COLUMN_LABELS[column] for column in columns])
    for record, consumable, user in rows:
        values = _export_row(
            record, consumable, user, related.get(record.id), accounts
        )
        writer.writerow(
            [
                values[column]
                if column in EXPORT_NUMERIC_COLUMNS
                else _export_cell(values.get(column))
                for column in columns
            ]
        )
    prefix = "\ufeff" if include_header else ""
    return prefix + buffer.getvalue()


def _export_filter_snapshot(filters: dict) -> dict:
    return {
        "consumable_id": filters["consumable_id"],
        "user_id": filters["user_id"],
        "start_date": filters["start_date"].isoformat()
        if filters["start_date"] is not None
        else None,
        "end_date": filters["end_date"].isoformat()
        if filters["end_date"] is not None
        else None,
        "type": filters["req_type"],
        "status": filters["req_status"],
    }


def _latest_corrections(
    db: Session, requisition_ids: list[int]
) -> dict[int, RequisitionCorrection]:
    if not requisition_ids:
        return {}
    latest: dict[int, RequisitionCorrection] = {}
    for correction in db.scalars(
        select(RequisitionCorrection)
        .where(RequisitionCorrection.requisition_id.in_(requisition_ids))
        .order_by(RequisitionCorrection.created_at, RequisitionCorrection.id)
    ):
        latest[correction.requisition_id] = correction
    return latest


def _correction_out(
    db: Session, correction: RequisitionCorrection
) -> CorrectionOut:
    reversal = (
        db.get(Requisition, correction.reversal_id)
        if correction.reversal_id is not None
        else None
    )
    reentry = (
        db.get(Requisition, correction.reentry_id)
        if correction.reentry_id is not None
        else None
    )
    return CorrectionOut(
        id=correction.id,
        requisition_id=correction.requisition_id,
        request_id=correction.request_id,
        status=correction.status,
        reason=correction.reason,
        remark=correction.remark,
        applicant_id=correction.applicant_id,
        applicant_name=correction.applicant_name,
        applicant_role=correction.applicant_role,
        handler_id=correction.handler_id,
        handler_name=correction.handler_name,
        handler_role=correction.handler_role,
        handling_note=correction.handling_note,
        reversal_id=correction.reversal_id,
        reversal_order_no=reversal.order_no if reversal is not None else None,
        reentry_id=correction.reentry_id,
        reentry_order_no=reentry.order_no if reentry is not None else None,
        created_at=correction.created_at,
        handled_at=correction.handled_at,
    )


def _can_view_requisition(requisition: Requisition, current_user: User) -> bool:
    if current_user.role == "student":
        return requisition.user_id == current_user.id
    return requisition.class_id == current_user.class_id


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


def estimate_supported_sessions(
    available_stock: int, quantity_per_person: int, headcount: int | None
) -> tuple[int | None, str | None, int | None]:
    if quantity_per_person <= 0:
        return None, "zero_demand", None
    if headcount is None or headcount <= 0:
        return None, "no_headcount", None
    required = quantity_per_person * headcount
    if required <= 0:
        return None, "zero_demand", required
    return max(int(available_stock), 0) // required, None, required


def _footprint_flow_rows(
    db: Session, user_id: int, start: datetime, end: datetime
) -> list[tuple[int, int, int, int, datetime]]:
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
    requisition_count = func.coalesce(
        func.sum(case((Requisition.type == "normal", 1), else_=0)), 0
    )
    rows = db.execute(
        select(
            Requisition.consumable_id,
            issued,
            reversed_quantity,
            requisition_count,
            func.max(Requisition.created_at),
        )
        .where(
            Requisition.user_id == user_id,
            Requisition.created_at >= start,
            Requisition.created_at <= end,
        )
        .group_by(Requisition.consumable_id)
        .order_by(Requisition.consumable_id)
    ).all()
    return [
        (
            int(consumable_id),
            int(issued_total),
            int(reversed_sum),
            int(count),
            last_at,
        )
        for consumable_id, issued_total, reversed_sum, count, last_at in rows
    ]


def _footprint_supports(
    db: Session,
    consumable_ids: list[int],
    class_id: int,
    consumables: dict[int, Consumable],
) -> dict[int, FootprintSupportOut]:
    if not consumable_ids:
        return {}
    recipe_rows = db.execute(
        select(CourseRecipeItem, CourseRecipeSnapshot, Course)
        .join(
            CourseRecipeSnapshot,
            CourseRecipeItem.snapshot_id == CourseRecipeSnapshot.id,
        )
        .join(Course, CourseRecipeSnapshot.course_id == Course.id)
        .where(
            CourseRecipeItem.consumable_id.in_(consumable_ids),
            Course.class_id == class_id,
            Course.status == "enabled",
        )
        .order_by(
            CourseRecipeSnapshot.created_at.desc(),
            CourseRecipeSnapshot.id.desc(),
            CourseRecipeItem.id,
        )
    ).all()
    latest: dict[int, tuple[CourseRecipeItem, CourseRecipeSnapshot, Course]] = {}
    for item, snapshot, course in recipe_rows:
        latest.setdefault(item.consumable_id, (item, snapshot, course))

    planned: dict[int, CourseSession] = {}
    fallback: dict[int, CourseSession] = {}
    course_ids = {course.id for _, _, course in latest.values()}
    if course_ids:
        sessions = db.scalars(
            select(CourseSession)
            .where(
                CourseSession.course_id.in_(course_ids),
                CourseSession.status != "cancelled",
            )
            .order_by(
                CourseSession.session_date.desc(), CourseSession.seq.desc()
            )
        ).all()
        for session in sessions:
            fallback.setdefault(session.course_id, session)
            if session.status == "planned":
                planned.setdefault(session.course_id, session)

    supports: dict[int, FootprintSupportOut] = {}
    for consumable_id in consumable_ids:
        entry = latest.get(consumable_id)
        if entry is None:
            supports[consumable_id] = FootprintSupportOut(reason_code="no_recipe")
            continue
        item, snapshot, course = entry
        session = planned.get(course.id) or fallback.get(course.id)
        headcount = session.headcount if session is not None else None
        consumable = consumables.get(consumable_id)
        available = consumable.current_stock if consumable is not None else 0
        supported, reason, required = estimate_supported_sessions(
            available, item.quantity_per_person, headcount
        )
        supports[consumable_id] = FootprintSupportOut(
            supported_sessions=supported,
            reason_code=reason,
            course_id=course.id,
            course_name=course.name,
            recipe_version=snapshot.version,
            headcount=headcount,
            quantity_per_person=item.quantity_per_person,
            required_quantity=required,
        )
    return supports


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
    corrections = _latest_corrections(db, [record.id for record in records])
    items = [
        _serialize_row(
            db,
            record,
            consumable,
            user,
            related=_list_related(record, origins, reversals),
            correction=corrections.get(record.id),
        )
        for record, consumable, user in rows
    ]
    return RequisitionPageOut(items=items, total=total, page=page, page_size=page_size)


def _export_scope(db: Session, query) -> tuple[int, datetime | None, datetime | None]:
    base = (
        query.with_only_columns(Requisition.created_at, Requisition.id)
        .order_by(None)
        .subquery()
    )
    total = db.scalar(select(func.count()).select_from(base)) or 0
    first_at, last_at = db.execute(
        select(func.min(base.c.created_at), func.max(base.c.created_at))
    ).one()
    return total, first_at, last_at


@router.get("/export/preview")
def preview_requisitions_csv(
    db: Annotated[Session, Depends(get_db)],
    staff: Annotated[User, Depends(require_role("teacher", "admin"))],
    filters: Annotated[dict, Depends(_consume_params)] = None,
) -> RequisitionExportPreviewOut:
    query = _build_query(db=db, current_user=staff, **filters)
    total, first_at, last_at = _export_scope(db, query)
    segments = (
        (total + CSV_EXPORT_LIMIT - 1) // CSV_EXPORT_LIMIT if total > 0 else 0
    )
    return RequisitionExportPreviewOut(
        total=total,
        first_at=first_at,
        last_at=last_at,
        limit=CSV_EXPORT_LIMIT,
        segments=segments,
        fields=list(_export_columns(staff.role == "admin")),
    )


@router.get("/export")
def export_requisitions_csv(
    db: Annotated[Session, Depends(get_db)],
    staff: Annotated[User, Depends(require_role("teacher", "admin"))],
    filters: Annotated[dict, Depends(_consume_params)] = None,
    include_header: Annotated[bool, Query()] = True,
    before_created_at: Annotated[datetime | None, Query()] = None,
    before_id: Annotated[int | None, Query()] = None,
    segment: Annotated[int, Query(ge=1)] = 1,
) -> Response:
    query = _build_query(db=db, current_user=staff, **filters)
    total, first_at, last_at = _export_scope(db, query)
    if total > CSV_EXPORT_MAX_ROWS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"匹配 {total} 条，超过单次完整导出上限 {CSV_EXPORT_MAX_ROWS} 条，"
                "请缩小日期或筛选范围"
            ),
        )

    segment_query = query
    if before_created_at is not None and before_id is not None:
        segment_query = segment_query.where(
            (Requisition.created_at < before_created_at)
            | (
                (Requisition.created_at == before_created_at)
                & (Requisition.id < before_id)
            )
        )
    rows = db.execute(
        segment_query.order_by(
            Requisition.created_at.desc(), Requisition.id.desc()
        ).limit(CSV_EXPORT_LIMIT + 1)
    ).all()
    has_more = len(rows) > CSV_EXPORT_LIMIT
    rows = rows[:CSV_EXPORT_LIMIT]

    records = [record for record, _consumable, _user in rows]
    is_admin = staff.role == "admin"
    columns = _export_columns(is_admin)
    related = _export_related(db, records)
    accounts = _export_operator_accounts(db, records) if is_admin else {}
    content = _export_text(
        columns,
        rows,
        related,
        accounts,
        include_header=include_header,
    )

    headers = {
        "Content-Disposition": (
            "attachment; filename="
            f"requisitions_{business_time.now().strftime('%Y%m%d_%H%M%S')}.csv"
        ),
        "X-Export-Total": str(total),
        "X-Export-This-Segment": str(len(rows)),
        "X-Export-Has-More": "true" if has_more else "false",
        "Cache-Control": "no-store",
    }
    if has_more and rows:
        last = rows[-1][0]
        headers["X-Export-Next-Before-Created-At"] = last.created_at.isoformat(
            sep=" "
        )
        headers["X-Export-Next-Before-Id"] = str(last.id)

    record_operation(
        db,
        action="requisition_export",
        operator=staff,
        target_type="requisition",
        target_label=f"导出领用流水 {len(rows)} 条（匹配 {total} 条）",
        after_value=json.dumps(
            {
                "matched": total,
                "exported": len(rows),
                "segment": segment,
                "has_more": has_more,
                "first_at": first_at.isoformat() if first_at else None,
                "last_at": last_at.isoformat() if last_at else None,
                "filters": _export_filter_snapshot(filters),
                "fields": list(columns),
                "cursor": {
                    "before_created_at": before_created_at.isoformat()
                    if before_created_at
                    else None,
                    "before_id": before_id,
                },
            },
            ensure_ascii=False,
        ),
        remark=f"导出领用流水第 {segment} 段 {len(rows)} 条",
    )
    db.commit()
    trim_audit_logs(db)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers=headers,
    )


@router.get("/consumable-options")
def list_consumable_options(
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> list[RequisitionConsumableOptionOut]:
    query = (
        select(Consumable)
        .join(Requisition, Requisition.consumable_id == Consumable.id)
        .where(Requisition.class_id == current_user.class_id)
    )
    if current_user.role == "student":
        query = query.where(Requisition.user_id == current_user.id)
    rows = db.scalars(query.distinct().order_by(Consumable.id)).all()
    return [
        RequisitionConsumableOptionOut(
            id=row.id,
            name=row.name,
            spec=row.spec,
            unit=row.unit,
            status=row.status,
        )
        for row in rows
    ]


@router.get("/my-footprint")
def get_my_footprint(
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
    window: Annotated[int, Query()] = DEFAULT_FOOTPRINT_WINDOW,
) -> FootprintOut:
    if window not in FOOTPRINT_WINDOWS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="window 参数仅支持 7、30 或 90",
        )
    start = consumption_core.window_start(window)
    end = consumption_core.window_end()
    rows = _footprint_flow_rows(db, current_user.id, start, end)
    consumable_ids = [row[0] for row in rows]
    consumables = (
        {
            consumable.id: consumable
            for consumable in db.scalars(
                select(Consumable).where(Consumable.id.in_(consumable_ids))
            ).all()
        }
        if consumable_ids
        else {}
    )
    supports = _footprint_supports(
        db, consumable_ids, current_user.class_id, consumables
    )

    items: list[FootprintItemOut] = []
    unit_rows: dict[str, dict[str, int]] = {}
    for consumable_id, issued, reversed_quantity, count, last_at in rows:
        consumable = consumables.get(consumable_id)
        if consumable is None:
            continue
        net_quantity = max(issued - reversed_quantity, 0)
        items.append(
            FootprintItemOut(
                consumable_id=consumable.id,
                name=consumable.name,
                spec=consumable.spec,
                unit=consumable.unit,
                requisition_count=count,
                issued_quantity=issued,
                reversed_quantity=reversed_quantity,
                net_quantity=net_quantity,
                last_at=last_at,
                support=supports[consumable_id],
            )
        )
        unit_row = unit_rows.setdefault(
            consumable.unit,
            {"requisition_count": 0, "net_quantity": 0, "consumables": 0},
        )
        unit_row["requisition_count"] += count
        unit_row["net_quantity"] += net_quantity
        unit_row["consumables"] += 1

    items.sort(key=lambda row: (-row.net_quantity, row.name, row.consumable_id))
    unit_totals = [
        FootprintUnitTotalOut(
            unit=unit,
            requisition_count=values["requisition_count"],
            net_quantity=values["net_quantity"],
            consumables=values["consumables"],
        )
        for unit, values in sorted(
            unit_rows.items(),
            key=lambda entry: (-entry[1]["net_quantity"], entry[0]),
        )
    ]
    return FootprintOut(
        window=window,
        generated_at=business_time.now(),
        user_id=current_user.id,
        user_name=current_user.name,
        requisition_count=sum(item.requisition_count for item in items),
        items=items,
        unit_totals=unit_totals,
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


@dataclass(frozen=True)
class _BackfillLink:
    mode: str
    event_id: int | None
    count_id: int | None
    count_order_no: str | None


def _find_backfill_link(
    db: Session, consumable: Consumable, business_at: datetime
) -> _BackfillLink | None:
    counted = db.execute(
        select(InventoryCount, InventoryCountLine)
        .join(InventoryCountLine, InventoryCountLine.count_id == InventoryCount.id)
        .where(
            InventoryCount.class_id == consumable.class_id,
            InventoryCount.status == "posted",
            InventoryCount.count_at >= business_at,
            InventoryCountLine.consumable_id == consumable.id,
        )
        .order_by(InventoryCount.count_at.desc(), InventoryCount.id.desc())
    ).first()
    if counted is not None:
        count, line = counted
        return _BackfillLink("counted", line.event_id, count.id, count.order_no)

    opening = db.scalars(
        select(InventoryEvent)
        .where(
            InventoryEvent.consumable_id == consumable.id,
            InventoryEvent.event_type == inventory_ledger.EVENT_OPENING,
            InventoryEvent.source_type == inventory_ledger.SOURCE_LEDGER_IMPORT,
        )
        .order_by(InventoryEvent.exec_seq, InventoryEvent.id)
    ).first()
    if opening is not None and business_at < opening.business_at:
        return _BackfillLink("opening", opening.id, None, None)
    return None


def _validate_created_at(
    value: datetime, max_backfill_days: int | None
) -> datetime:
    normalized = business_time.normalize(value)
    if normalized > business_time.now():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="领用时间不能晚于当前时间",
        )
    if (
        max_backfill_days is not None
        and normalized < business_time.backfill_earliest(max_backfill_days)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"领用时间不能早于 {max_backfill_days} 天前，超出普通补录区间；"
                "请由教师或班委通过受控调整登记"
                "（POST /api/v1/requisitions/backfills/controlled）"
            ),
            headers={"X-Error-Code": "backfill_out_of_range"},
        )
    return normalized.replace(microsecond=0)


def _create_requisition_impl(
    db: Session,
    payload: RequisitionCreate,
    current_user: User,
    *,
    max_backfill_days: int | None,
    controlled: bool,
) -> RequisitionCreateOut | JSONResponse:
    request_id = payload.request_id or uuid.uuid4().hex
    owner = _resolve_owner(db, payload, current_user)

    existing = db.scalars(
        select(RequisitionOrder).where(RequisitionOrder.request_id == request_id)
    ).first()
    if existing is not None:
        return _resolve_repeat(existing, payload, owner)

    is_backfill = payload.created_at is not None
    submitted_at = business_time.now().replace(microsecond=0)
    business_at = (
        _validate_created_at(payload.created_at, max_backfill_days)
        if is_backfill
        else submitted_at
    )
    backfill_reason: str | None = None
    if is_backfill:
        backfill_reason = payload.backfill_reason or (
            "受控调整" if controlled else "补录登记"
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
        backfill_reason=backfill_reason,
        items_signature=json.dumps(_submit_signature(payload.items)),
    )
    db.add(order)
    try:
        db.flush()
        order_id = order.id
        db.commit()
    except IntegrityError:
        db.rollback()
        conflict = db.scalars(
            select(RequisitionOrder).where(RequisitionOrder.request_id == request_id)
        ).first()
        if conflict is None:
            raise
        return _resolve_repeat(conflict, payload, owner)

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

        consumables = concurrency.lock_consumables(
            db, [item.consumable_id for item in payload.items]
        )
        for row_number, item in enumerate(payload.items, start=1):
            field_path = f"items[{row_number - 1}]"
            consumable = consumables.get(item.consumable_id)
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

            line_posted_at = business_time.now().replace(microsecond=0)
            link = (
                _find_backfill_link(db, consumable, business_at)
                if is_backfill
                else None
            )
            line_mode: str | None = None
            linked_count_order_no: str | None = None
            event: InventoryEvent | None = None

            if link is None:
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
                if is_backfill:
                    line_mode = "controlled" if controlled else "normal"
            else:
                stock_before = consumable.current_stock
                stock_after = stock_before
                line_mode = link.mode
                linked_count_order_no = link.count_order_no

            requisition = Requisition(
                consumable_id=item.consumable_id,
                name_snapshot=consumable.name,
                spec_snapshot=consumable.spec,
                unit_snapshot=consumable.unit,
                user_id=owner.id,
                quantity=item.quantity,
                stock_after=stock_after,
                type="normal",
                status="active",
                class_id=consumable.class_id,
                created_at=business_at,
                posted_at=line_posted_at,
                remark=payload.remark,
                backfill_reason=backfill_reason,
                backfill_mode=line_mode,
                linked_event_id=link.event_id if link is not None else None,
                linked_count_id=link.count_id if link is not None else None,
                operator_id=current_user.id,
                operator_name=current_user.name,
                operator_role=current_user.role,
            )
            db.add(requisition)
            db.flush()

            if link is None:
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
                    event_id=event.id if event is not None else None,
                    requisition_id=requisition.id,
                    consumable_id=consumable.id,
                    consumable_name=consumable.name,
                    unit=consumable.unit,
                    quantity=item.quantity,
                    stock_before=stock_before,
                    stock_after=stock_after,
                    business_at=business_at,
                    posted_at=line_posted_at,
                    backfill_mode=line_mode,
                    linked_count_order_no=linked_count_order_no,
                )
            )
            stock_alerts.sync_consumable(
                db, consumable, source="requisition", at=line_posted_at
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
                        "backfill_reason": backfill_reason,
                        "controlled": controlled,
                        "backfill_modes": [
                            line.backfill_mode for line in receipt_lines
                        ],
                        "linked_count_order_nos": [
                            line.linked_count_order_no
                            for line in receipt_lines
                            if line.linked_count_order_no
                        ],
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
    except OperationalError as exc:
        db.rollback()
        if not concurrency.is_lock_conflict(exc):
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
        db.execute(delete(RequisitionOrder).where(RequisitionOrder.id == order_id))
        db.commit()
        raise concurrency.retry_required_error() from exc
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


@router.post("", status_code=status.HTTP_201_CREATED)
def create_requisition(
    payload: RequisitionCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> RequisitionCreateOut:
    return _create_requisition_impl(
        db,
        payload,
        current_user,
        max_backfill_days=MAX_BACKFILL_DAYS,
        controlled=False,
    )


@router.post("/backfills/controlled", status_code=status.HTTP_201_CREATED)
def create_controlled_backfill(
    payload: ControlledBackfillCreate,
    db: Annotated[Session, Depends(get_db)],
    manager: ManagerUser,
) -> RequisitionCreateOut:
    create_payload = RequisitionCreate(
        items=payload.items,
        user_id=payload.user_id,
        created_at=payload.business_at,
        remark=payload.remark,
        backfill_reason=payload.reason,
        request_id=payload.request_id,
    )
    result = _create_requisition_impl(
        db,
        create_payload,
        manager,
        max_backfill_days=None,
        controlled=True,
    )
    if isinstance(result, JSONResponse):
        return result
    db.add(
        OperationLog(
            operator_id=manager.id,
            role=manager.role,
            action="requisition_backfill_controlled",
            target_type="requisition_order",
            target_id=None,
            before_value=None,
            after_value=json.dumps(
                {
                    "reason": payload.reason,
                    "order_no": result.order_no,
                    "business_at": business_time.normalize(
                        payload.business_at
                    ).replace(microsecond=0).isoformat(),
                },
                ensure_ascii=False,
            ),
        )
    )
    db.commit()
    service.clear_cache()
    return result


def _record_out(
    db: Session,
    record: Requisition,
    consumable: Consumable | None,
    user: User | None,
    correction: CorrectionOut | None = None,
) -> RequisitionRecordOut:
    linked_count = (
        db.get(InventoryCount, record.linked_count_id)
        if record.linked_count_id is not None
        else None
    )
    return RequisitionRecordOut(
        id=record.id,
        consumable_id=record.consumable_id,
        consumable_name=(
            record.name_snapshot
            or (consumable.name if consumable else f"耗材#{record.consumable_id}")
        ),
        unit=record.unit_snapshot,
        user_id=record.user_id,
        user_name=user.name if user else f"用户#{record.user_id}",
        quantity=record.quantity,
        stock_after=record.stock_after,
        type=record.type,
        status=record.status,
        reversal_of=record.reversal_of,
        reversal_reason=record.reversal_reason,
        created_at=record.created_at,
        posted_at=record.posted_at,
        remark=record.remark,
        order_no=record.order_no,
        handler_name=record.handler_name,
        reentry_of=record.reentry_of,
        backfill_reason=record.backfill_reason,
        backfill_mode=record.backfill_mode,
        linked_event_id=record.linked_event_id,
        linked_count_id=record.linked_count_id,
        linked_count_order_no=linked_count.order_no if linked_count else None,
        operator_id=record.operator_id,
        operator_name=record.operator_name,
        operator_role=record.operator_role,
        correction=correction,
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
        order_no=record.order_no,
    )


def _latest_correction_for(
    db: Session, requisition_id: int
) -> RequisitionCorrection | None:
    return db.scalars(
        select(RequisitionCorrection)
        .where(RequisitionCorrection.requisition_id == requisition_id)
        .order_by(RequisitionCorrection.created_at.desc(), RequisitionCorrection.id.desc())
    ).first()


def _return_out(db: Session, record: RequisitionReturn) -> RequisitionReturnOut:
    consumable = db.get(Consumable, record.consumable_id)
    origin = db.get(Requisition, record.requisition_id)
    return RequisitionReturnOut(
        id=record.id,
        order_no=record.order_no,
        request_id=record.request_id,
        requisition_id=record.requisition_id,
        requisition_order_no=origin.order_no if origin is not None else None,
        consumable_id=record.consumable_id,
        consumable_name=consumable.name if consumable else f"耗材#{record.consumable_id}",
        unit=consumable.unit if consumable else "",
        quantity=record.quantity,
        stock_before=record.stock_before,
        stock_after=record.stock_after,
        reason=record.reason,
        remark=record.remark,
        operator_id=record.operator_id,
        operator_name=record.operator_name,
        operator_role=record.operator_role,
        event_id=record.event_id,
        created_at=record.created_at,
    )


def _scrap_out(db: Session, record: RequisitionScrap) -> RequisitionScrapOut:
    consumable = db.get(Consumable, record.consumable_id)
    origin = db.get(Requisition, record.requisition_id)
    return RequisitionScrapOut(
        id=record.id,
        order_no=record.order_no,
        request_id=record.request_id,
        requisition_id=record.requisition_id,
        requisition_order_no=origin.order_no if origin is not None else None,
        consumable_id=record.consumable_id,
        consumable_name=consumable.name if consumable else f"耗材#{record.consumable_id}",
        unit=consumable.unit if consumable else "",
        quantity=record.quantity,
        stock_before=record.stock_before,
        stock_after=record.stock_after,
        reason=record.reason,
        remark=record.remark,
        operator_id=record.operator_id,
        operator_name=record.operator_name,
        operator_role=record.operator_role,
        event_id=record.event_id,
        created_at=record.created_at,
    )


def _detail_out(db: Session, requisition: Requisition) -> RequisitionDetailOut:
    correction_target_id = requisition.id
    origin_reversal: Requisition | None = None
    if requisition.type == "reversal" and requisition.reversal_of is not None:
        correction_target_id = requisition.reversal_of
    elif requisition.reentry_of is not None:
        origin_reversal = db.get(Requisition, requisition.reentry_of)
        if origin_reversal is not None:
            correction_target_id = (
                origin_reversal.reversal_of or origin_reversal.id
            )

    correction = _latest_correction_for(db, correction_target_id)
    detail = RequisitionDetailOut(
        **_record_out(
            db,
            requisition,
            db.get(Consumable, requisition.consumable_id),
            db.get(User, requisition.user_id),
            correction=_correction_out(db, correction) if correction else None,
        ).model_dump()
    )

    if requisition.reversal_of is not None:
        origin = db.get(Requisition, requisition.reversal_of)
        if origin is not None:
            detail.origin = _related_out(origin)

    if requisition.type == "reversal":
        reentry = db.scalars(
            select(Requisition).where(Requisition.reentry_of == requisition.id)
        ).first()
        if reentry is not None:
            detail.reentry = _related_out(reentry)
    elif origin_reversal is not None:
        detail.reversal = _related_out(origin_reversal)
        if origin_reversal.reversal_of is not None:
            origin = db.get(Requisition, origin_reversal.reversal_of)
            if origin is not None:
                detail.origin = _related_out(origin)
    else:
        reversal = db.scalars(
            select(Requisition).where(Requisition.reversal_of == requisition.id)
        ).first()
        if reversal is not None:
            detail.reversal = _related_out(reversal)
            reentry = db.scalars(
                select(Requisition).where(Requisition.reentry_of == reversal.id)
            ).first()
            if reentry is not None:
                detail.reentry = _related_out(reentry)

    detail.returned_quantity = _returned_total(db, requisition.id)
    detail.scrapped_quantity = _scrapped_total(db, requisition.id)
    detail.returnable_quantity = _returnable_quantity(
        requisition, detail.returned_quantity, detail.scrapped_quantity
    )
    detail.returns = [
        _return_out(db, record)
        for record in db.scalars(
            select(RequisitionReturn)
            .where(RequisitionReturn.requisition_id == requisition.id)
            .order_by(RequisitionReturn.created_at, RequisitionReturn.id)
        )
    ]
    detail.scraps = [
        _scrap_out(db, record)
        for record in db.scalars(
            select(RequisitionScrap)
            .where(RequisitionScrap.requisition_id == requisition.id)
            .order_by(RequisitionScrap.created_at, RequisitionScrap.id)
        )
    ]
    return detail


@router.post("/{requisition_id}/corrections", status_code=status.HTTP_201_CREATED)
def create_correction(
    requisition_id: int,
    payload: CorrectionCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> JSONResponse:
    requisition = db.get(Requisition, requisition_id)
    if requisition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="流水不存在"
        )
    if current_user.role == "student":
        if requisition.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="学生只能申请本人记录的纠错",
            )
    elif requisition.class_id != current_user.class_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="无权限申请该流水的纠错"
        )
    if requisition.type != "normal" or requisition.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅有效领用记录可以申请纠错",
        )

    request_id = payload.request_id or uuid.uuid4().hex
    existing = db.scalars(
        select(RequisitionCorrection).where(
            RequisitionCorrection.request_id == request_id
        )
    ).first()
    if existing is not None:
        if (
            existing.requisition_id != requisition.id
            or existing.applicant_id != current_user.id
            or existing.reason != payload.reason
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="请求标识已用于不同内容，请勿复用",
            )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=jsonable_encoder(_correction_out(db, existing)),
        )

    pending = db.scalars(
        select(RequisitionCorrection).where(
            RequisitionCorrection.requisition_id == requisition.id,
            RequisitionCorrection.applicant_id == current_user.id,
            RequisitionCorrection.status == "submitted",
        )
    ).first()
    if pending is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该记录已有待处理的纠错申请",
        )

    created_at = business_time.now().replace(microsecond=0)
    correction = RequisitionCorrection(
        requisition_id=requisition.id,
        request_id=request_id,
        status="submitted",
        reason=payload.reason,
        remark=payload.remark,
        applicant_id=current_user.id,
        applicant_name=current_user.name,
        applicant_role=current_user.role,
        class_id=requisition.class_id,
        created_at=created_at,
    )
    db.add(correction)
    db.flush()
    db.add(
        OperationLog(
            operator_id=current_user.id,
            role=current_user.role,
            action="requisition_correction_submit",
            target_type="requisition",
            target_id=requisition.id,
            before_value=None,
            after_value=json.dumps(
                {
                    "correction_id": correction.id,
                    "request_id": request_id,
                    "status": "submitted",
                    "reason": correction.reason,
                },
                ensure_ascii=False,
            ),
        )
    )
    db.commit()
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_correction_out(db, correction)),
    )


@router.get("/{requisition_id}/corrections")
def list_corrections(
    requisition_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> list[CorrectionOut]:
    requisition = db.get(Requisition, requisition_id)
    if requisition is None or not _can_view_requisition(requisition, current_user):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="流水不存在"
        )
    corrections = db.scalars(
        select(RequisitionCorrection)
        .where(RequisitionCorrection.requisition_id == requisition.id)
        .order_by(
            RequisitionCorrection.created_at.desc(), RequisitionCorrection.id.desc()
        )
    ).all()
    return [_correction_out(db, correction) for correction in corrections]


@router.patch("/{requisition_id}/corrections/{correction_id}")
def handle_correction(
    requisition_id: int,
    correction_id: int,
    payload: CorrectionHandle,
    db: Annotated[Session, Depends(get_db)],
    manager: ManagerUser,
) -> CorrectionOut:
    requisition = db.get(Requisition, requisition_id)
    if requisition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="流水不存在"
        )
    if requisition.class_id != manager.class_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="无权限处理该流水"
        )
    correction = db.get(RequisitionCorrection, correction_id)
    if (
        correction is None
        or correction.requisition_id != requisition.id
        or correction.class_id != manager.class_id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="纠错申请不存在"
        )
    if correction.status != "submitted":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该纠错申请已处理",
        )

    handled_at = business_time.now().replace(microsecond=0)
    correction.status = payload.status
    correction.handler_id = manager.id
    correction.handler_name = manager.name
    correction.handler_role = manager.role
    correction.handling_note = payload.handling_note
    correction.handled_at = handled_at
    db.add(
        OperationLog(
            operator_id=manager.id,
            role=manager.role,
            action="requisition_correction_handle",
            target_type="requisition_correction",
            target_id=correction.id,
            before_value=json.dumps({"status": "submitted"}, ensure_ascii=False),
            after_value=json.dumps(
                {
                    "status": correction.status,
                    "requisition_id": requisition.id,
                    "handling_note": correction.handling_note,
                },
                ensure_ascii=False,
            ),
        )
    )
    db.commit()
    db.refresh(correction)
    return _correction_out(db, correction)


@router.post("/{requisition_id}/reversal")
def reverse_requisition(
    requisition_id: int,
    payload: ReversalCreate,
    db: Annotated[Session, Depends(get_db)],
    manager: ManagerUser,
) -> RequisitionDetailOut:
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
    if (
        payload.reentry is not None
        and payload.reentry.quantity > requisition.quantity
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="重登数量不能超过原领用数量",
        )

    correction: RequisitionCorrection | None = None
    if payload.correction_id is not None:
        correction = db.get(RequisitionCorrection, payload.correction_id)
        if (
            correction is None
            or correction.requisition_id != requisition.id
            or correction.class_id != manager.class_id
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="纠错申请不存在"
            )
        if correction.status != "submitted":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该纠错申请已处理",
            )
    else:
        correction = db.scalars(
            select(RequisitionCorrection)
            .where(
                RequisitionCorrection.requisition_id == requisition.id,
                RequisitionCorrection.status == "submitted",
            )
            .order_by(
                RequisitionCorrection.created_at.desc(),
                RequisitionCorrection.id.desc(),
            )
        ).first()

    try:
        consumable = db.get(
            Consumable, requisition.consumable_id, with_for_update=True
        )
    except OperationalError as exc:
        db.rollback()
        if concurrency.is_lock_conflict(exc):
            raise concurrency.retry_required_error() from exc
        raise
    if consumable is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="流水不存在"
        )
    origin_quantity = requisition.quantity
    origin_user_id = requisition.user_id
    origin_class_id = requisition.class_id

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

        posted_at = business_time.now().replace(microsecond=0)
        db.refresh(consumable)
        stock_before = consumable.current_stock

        db.execute(
            update(Consumable)
            .where(Consumable.id == requisition.consumable_id)
            .values(current_stock=Consumable.current_stock + origin_quantity)
        )
        db.flush()
        db.refresh(consumable)
        stock_after = consumable.current_stock

        reversal = Requisition(
            consumable_id=requisition.consumable_id,
            name_snapshot=consumable.name,
            spec_snapshot=consumable.spec,
            unit_snapshot=consumable.unit,
            user_id=origin_user_id,
            quantity=origin_quantity,
            stock_after=stock_after,
            type="reversal",
            status="active",
            reversal_of=requisition.id,
            reversal_reason=payload.reason,
            order_no=inventory_ledger.reversal_order_no_for(
                requisition.id, posted_at
            ),
            handler_id=manager.id,
            handler_name=manager.name,
            handler_role=manager.role,
            class_id=origin_class_id,
            created_at=posted_at,
            remark=payload.remark,
        )
        db.add(reversal)
        db.flush()

        reversal_event = inventory_ledger.append_event(
            db,
            consumable=consumable,
            event_type=inventory_ledger.EVENT_REVERSAL,
            delta=origin_quantity,
            stock_before=stock_before,
            stock_after=stock_after,
            business_at=posted_at,
            posted_at=posted_at,
            source_type=inventory_ledger.SOURCE_REQUISITION_REVERSAL,
            source_id=requisition.id,
            requisition_id=reversal.id,
            operator=manager,
            unit_snapshot=consumable.unit,
        )

        reentry: Requisition | None = None
        reentry_event_id: int | None = None
        if payload.reentry is not None:
            if consumable.status != STATUS_ENABLED:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"耗材「{consumable.name}」已停用，无法生成重登单",
                )
            reentry_stock_before = consumable.current_stock
            matched = db.execute(
                update(Consumable)
                .where(
                    Consumable.id == consumable.id,
                    Consumable.current_stock >= payload.reentry.quantity,
                    Consumable.status == STATUS_ENABLED,
                )
                .values(
                    current_stock=Consumable.current_stock
                    - payload.reentry.quantity
                )
            )
            if matched.rowcount == 0:
                db.flush()
                db.refresh(consumable)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"{consumable.name}库存不足，仅剩 "
                        f"{consumable.current_stock}{consumable.unit or '件'}"
                    ),
                )
            db.flush()
            db.refresh(consumable)
            reentry_stock_after = consumable.current_stock
            reentry = Requisition(
                consumable_id=consumable.id,
                name_snapshot=consumable.name,
                spec_snapshot=consumable.spec,
                unit_snapshot=consumable.unit,
                user_id=origin_user_id,
                quantity=payload.reentry.quantity,
                stock_after=reentry_stock_after,
                type="normal",
                status="active",
                reentry_of=reversal.id,
                order_no=inventory_ledger.reentry_order_no_for(
                    reversal.id, posted_at
                ),
                handler_id=manager.id,
                handler_name=manager.name,
                handler_role=manager.role,
                class_id=origin_class_id,
                created_at=posted_at,
                remark=payload.reentry.remark,
            )
            db.add(reentry)
            db.flush()
            reentry_event = inventory_ledger.append_event(
                db,
                consumable=consumable,
                event_type=inventory_ledger.EVENT_REQUISITION,
                delta=-payload.reentry.quantity,
                stock_before=reentry_stock_before,
                stock_after=reentry_stock_after,
                business_at=posted_at,
                posted_at=posted_at,
                source_type=inventory_ledger.SOURCE_REQUISITION_REENTRY,
                source_id=reentry.id,
                requisition_id=reentry.id,
                operator=manager,
                unit_snapshot=consumable.unit,
            )
            reentry_event_id = reentry_event.id

        stock_alerts.sync_consumable(
            db, consumable, source="reversal", at=posted_at
        )
        if correction is not None:
            correction.status = "accepted"
            correction.handler_id = manager.id
            correction.handler_name = manager.name
            correction.handler_role = manager.role
            correction.handling_note = payload.remark or payload.reason
            correction.reversal_id = reversal.id
            correction.reentry_id = reentry.id if reentry is not None else None
            correction.handled_at = posted_at

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
                        "reversal_order_no": reversal.order_no,
                        "status": "reversed",
                        "stock_before": stock_before,
                        "stock_after": stock_after,
                        "consumable_id": requisition.consumable_id,
                        "quantity": origin_quantity,
                        "reason": payload.reason,
                        "event_id": reversal_event.id,
                        "correction_id": correction.id if correction else None,
                        "reentry_id": reentry.id if reentry else None,
                        "reentry_order_no": reentry.order_no if reentry else None,
                    },
                    ensure_ascii=False,
                ),
            )
        )
        if reentry is not None:
            db.add(
                OperationLog(
                    operator_id=manager.id,
                    role=manager.role,
                    action="requisition_reentry",
                    target_type="requisition",
                    target_id=reentry.id,
                    before_value=None,
                    after_value=json.dumps(
                        {
                            "reentry_id": reentry.id,
                            "reentry_order_no": reentry.order_no,
                            "reversal_id": reversal.id,
                            "origin_requisition_id": requisition.id,
                            "quantity": reentry.quantity,
                            "event_id": reentry_event_id,
                        },
                        ensure_ascii=False,
                    ),
                )
            )
        if correction is not None:
            db.add(
                OperationLog(
                    operator_id=manager.id,
                    role=manager.role,
                    action="requisition_correction_accept",
                    target_type="requisition_correction",
                    target_id=correction.id,
                    before_value=json.dumps(
                        {"status": "submitted"}, ensure_ascii=False
                    ),
                    after_value=json.dumps(
                        {
                            "status": "accepted",
                            "requisition_id": requisition.id,
                            "reversal_id": reversal.id,
                            "reversal_order_no": reversal.order_no,
                            "reentry_id": reentry.id if reentry else None,
                            "handling_note": correction.handling_note,
                        },
                        ensure_ascii=False,
                    ),
                )
            )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except OperationalError as exc:
        db.rollback()
        if concurrency.is_lock_conflict(exc):
            raise concurrency.retry_required_error() from exc
        raise

    service.clear_cache()
    db.refresh(reversal)
    return _detail_out(db, reversal)


def _load_action_target(
    db: Session,
    requisition_id: int,
    current_user: User,
    *,
    staff_only: bool,
) -> Requisition:
    requisition = db.get(Requisition, requisition_id, with_for_update=True)
    if requisition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="流水不存在"
        )
    if staff_only:
        if current_user.role == "student":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="学生不能登记报损，请由教师或班委处理",
            )
        if requisition.class_id != current_user.class_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="无权限处理该流水"
            )
    elif current_user.role == "student":
        if requisition.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="学生只能登记本人领用的退库",
            )
    elif requisition.class_id != current_user.class_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="无权限处理该流水"
        )
    if requisition.type != "normal" or requisition.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅有效领用记录可以登记退库或报损",
        )
    return requisition


@router.post("/{requisition_id}/returns", status_code=status.HTTP_201_CREATED)
def create_return(
    requisition_id: int,
    payload: ReturnCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> JSONResponse:
    requisition = _load_action_target(
        db, requisition_id, current_user, staff_only=False
    )
    request_id = payload.request_id or uuid.uuid4().hex
    existing = db.scalars(
        select(RequisitionReturn).where(RequisitionReturn.request_id == request_id)
    ).first()
    if existing is not None:
        if (
            existing.requisition_id != requisition.id
            or existing.quantity != payload.quantity
            or (existing.reason or None) != payload.reason
            or (existing.remark or None) != payload.remark
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="请求标识已用于不同内容，请勿复用",
            )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=jsonable_encoder(_return_out(db, existing)),
        )

    consumable = db.get(Consumable, requisition.consumable_id, with_for_update=True)
    if consumable is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="流水不存在"
        )
    returnable = _returnable_quantity(
        requisition,
        _returned_total(db, requisition.id),
        _scrapped_total(db, requisition.id),
    )
    if payload.quantity > returnable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"退库数量不能超过可退数量 {returnable}",
        )

    posted_at = business_time.now().replace(microsecond=0)
    stock_before = consumable.current_stock
    db.execute(
        update(Consumable)
        .where(Consumable.id == consumable.id)
        .values(current_stock=Consumable.current_stock + payload.quantity)
    )
    db.flush()
    db.refresh(consumable)
    stock_after = consumable.current_stock

    record = RequisitionReturn(
        order_no=inventory_ledger.return_order_no_for(request_id, posted_at),
        request_id=request_id,
        requisition_id=requisition.id,
        consumable_id=requisition.consumable_id,
        quantity=payload.quantity,
        stock_before=stock_before,
        stock_after=stock_after,
        reason=payload.reason,
        remark=payload.remark,
        operator_id=current_user.id,
        operator_name=current_user.name,
        operator_role=current_user.role,
        class_id=requisition.class_id,
        business_at=posted_at,
        posted_at=posted_at,
        created_at=posted_at,
    )
    db.add(record)
    db.flush()
    event = inventory_ledger.append_event(
        db,
        consumable=consumable,
        event_type=inventory_ledger.EVENT_RETURN,
        delta=payload.quantity,
        stock_before=stock_before,
        stock_after=stock_after,
        business_at=posted_at,
        posted_at=posted_at,
        source_type=inventory_ledger.SOURCE_REQUISITION_RETURN,
        source_id=record.id,
        requisition_id=requisition.id,
        operator=current_user,
        unit_snapshot=consumable.unit,
    )
    record.event_id = event.id
    db.add(
        OperationLog(
            operator_id=current_user.id,
            role=current_user.role,
            action="requisition_return",
            target_type="requisition",
            target_id=requisition.id,
            before_value=json.dumps(
                {"status": requisition.status, "stock_before": stock_before},
                ensure_ascii=False,
            ),
            after_value=json.dumps(
                {
                    "return_id": record.id,
                    "return_order_no": record.order_no,
                    "requisition_id": requisition.id,
                    "quantity": payload.quantity,
                    "reason": payload.reason,
                    "stock_before": stock_before,
                    "stock_after": stock_after,
                    "event_id": event.id,
                },
                ensure_ascii=False,
            ),
        )
    )
    stock_alerts.sync_consumable(db, consumable, source="return", at=posted_at)
    db.commit()
    service.clear_cache(consumable.id)
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_return_out(db, record)),
    )


@router.post("/{requisition_id}/scraps", status_code=status.HTTP_201_CREATED)
def create_scrap(
    requisition_id: int,
    payload: ScrapCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> JSONResponse:
    requisition = _load_action_target(
        db, requisition_id, current_user, staff_only=True
    )
    request_id = payload.request_id or uuid.uuid4().hex
    existing = db.scalars(
        select(RequisitionScrap).where(RequisitionScrap.request_id == request_id)
    ).first()
    if existing is not None:
        if (
            existing.requisition_id != requisition.id
            or existing.quantity != payload.quantity
            or (existing.reason or None) != payload.reason
            or (existing.remark or None) != payload.remark
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="请求标识已用于不同内容，请勿复用",
            )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=jsonable_encoder(_scrap_out(db, existing)),
        )

    consumable = db.get(Consumable, requisition.consumable_id, with_for_update=True)
    if consumable is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="流水不存在"
        )
    returnable = _returnable_quantity(
        requisition,
        _returned_total(db, requisition.id),
        _scrapped_total(db, requisition.id),
    )
    if payload.quantity > returnable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"报损数量不能超过可退数量 {returnable}",
        )

    posted_at = business_time.now().replace(microsecond=0)
    stock_before = consumable.current_stock
    stock_after = stock_before

    record = RequisitionScrap(
        order_no=inventory_ledger.scrap_order_no_for(request_id, posted_at),
        request_id=request_id,
        requisition_id=requisition.id,
        consumable_id=requisition.consumable_id,
        quantity=payload.quantity,
        stock_before=stock_before,
        stock_after=stock_after,
        reason=payload.reason,
        remark=payload.remark,
        operator_id=current_user.id,
        operator_name=current_user.name,
        operator_role=current_user.role,
        class_id=requisition.class_id,
        business_at=posted_at,
        posted_at=posted_at,
        created_at=posted_at,
    )
    db.add(record)
    db.flush()
    event = inventory_ledger.append_event(
        db,
        consumable=consumable,
        event_type=inventory_ledger.EVENT_SCRAP,
        delta=0,
        stock_before=stock_before,
        stock_after=stock_after,
        business_at=posted_at,
        posted_at=posted_at,
        source_type=inventory_ledger.SOURCE_REQUISITION_SCRAP,
        source_id=record.id,
        requisition_id=requisition.id,
        operator=current_user,
        unit_snapshot=consumable.unit,
    )
    record.event_id = event.id
    db.add(
        OperationLog(
            operator_id=current_user.id,
            role=current_user.role,
            action="requisition_scrap",
            target_type="requisition",
            target_id=requisition.id,
            before_value=json.dumps(
                {"status": requisition.status, "current_stock": stock_before},
                ensure_ascii=False,
            ),
            after_value=json.dumps(
                {
                    "scrap_id": record.id,
                    "scrap_order_no": record.order_no,
                    "requisition_id": requisition.id,
                    "quantity": payload.quantity,
                    "reason": payload.reason,
                    "current_stock": stock_after,
                    "event_id": event.id,
                },
                ensure_ascii=False,
            ),
        )
    )
    stock_alerts.sync_consumable(db, consumable, source="scrap", at=posted_at)
    db.commit()
    service.clear_cache(consumable.id)
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_scrap_out(db, record)),
    )


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
    if not _can_view_requisition(requisition, current_user):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="流水不存在"
        )
    return _detail_out(db, requisition)
