import csv
import io
import json
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import business_time
from app.agent import service
from app.database import get_db
from app.deps import CurrentUser, require_role
from app.models import Consumable, OperationLog, Requisition, User
from app.schemas_consumables import STATUS_ENABLED
from app.schemas_requisitions import (
    MAX_BACKFILL_DAYS,
    RequisitionCreate,
    RequisitionCreateOut,
    RequisitionDetailOut,
    RequisitionItemOut,
    RequisitionListItemOut,
    RequisitionPageOut,
    RequisitionRecordOut,
    RequisitionRelatedOut,
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


def _row_error(row_number: int, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"第 {row_number} 行：{message}",
    )


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
    elif user_id is not None:
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
    created_at = (
        _validate_created_at(payload.created_at)
        if payload.created_at is not None
        else business_time.now().replace(microsecond=0)
    )

    owner_id = current_user.id
    if payload.user_id is not None and payload.user_id != current_user.id:
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
        if owner.status != "enabled":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"领用人 {owner.name} 已被停用",
            )
        owner_id = owner.id

    results: list[RequisitionItemOut] = []
    created: list[Requisition] = []
    total_quantity = 0
    try:
        for row_number, item in enumerate(payload.items, start=1):
            consumable = db.get(Consumable, item.consumable_id)
            if consumable is None:
                raise _row_error(row_number, f"耗材不存在（id={item.consumable_id}）")
            if consumable.status != STATUS_ENABLED:
                raise _row_error(row_number, f"耗材「{consumable.name}」已停用，不可领用")

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
                raise _row_error(
                    row_number,
                    f"{consumable.name}库存不足，仅剩 {consumable.current_stock}"
                    f"{consumable.unit or '件'}",
                )

            db.flush()
            db.refresh(consumable)
            results.append(
                RequisitionItemOut(
                    consumable_name=consumable.name,
                    quantity=item.quantity,
                    stock_after=consumable.current_stock,
                )
            )
            total_quantity += item.quantity
            requisition = Requisition(
                consumable_id=item.consumable_id,
                user_id=owner_id,
                quantity=item.quantity,
                stock_after=consumable.current_stock,
                type="normal",
                status="active",
                class_id=consumable.class_id,
                created_at=created_at,
                remark=payload.remark,
            )
            db.add(requisition)
            created.append(requisition)

        db.flush()
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
                        "owner_id": owner_id,
                        "row_count": len(results),
                        "total_quantity": total_quantity,
                        "created_at": created_at.isoformat(),
                        "items": [item.model_dump() for item in results],
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
    return RequisitionCreateOut(count=len(results), items=results)


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
    if requisition is None or (
        current_user.role == "student" and requisition.user_id != current_user.id
    ):
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
