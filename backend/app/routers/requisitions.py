"""批量领用登记与红冲撤销：整单一个数据库事务，逐行条件原子扣减，任一行失败全部回滚。"""

import json
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

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

ManagerUser = Annotated[User, Depends(require_role("teacher", "admin"))]


def _row_error(row_number: int, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"第 {row_number} 行：{message}",
    )


def _validate_created_at(value: datetime) -> datetime:
    now = datetime.now()
    if value > now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="领用时间不能晚于当前时间",
        )
    if value < now - timedelta(days=MAX_BACKFILL_DAYS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"领用时间不能早于 {MAX_BACKFILL_DAYS} 天前，不支持补录更早的记录",
        )
    return value.replace(microsecond=0)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_requisition(
    payload: RequisitionCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> RequisitionCreateOut:
    """学生与教师/班委批量登记领用（最多 5 行），整单原子扣减库存并生成流水。

    - 学生只能登记本人领用，传入他人 user_id 返回 403。
    - 教师/班委可代登记（代填领用人）。
    - created_at 可选，默认当前时间；不能是未来，也不能早于 30 天前。
    """
    created_at = (
        _validate_created_at(payload.created_at)
        if payload.created_at is not None
        else datetime.now().replace(microsecond=0)
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
    total_quantity = 0
    try:
        for row_number, item in enumerate(payload.items, start=1):
            consumable = db.get(Consumable, item.consumable_id)
            if consumable is None:
                raise _row_error(row_number, f"耗材不存在（id={item.consumable_id}）")
            if consumable.status != STATUS_ENABLED:
                raise _row_error(row_number, f"耗材「{consumable.name}」已停用，不可领用")

            # 条件原子更新：库存足够且启用才扣减，影响行数=0 视为失败
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
            db.add(
                Requisition(
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
            )

        db.add(
            OperationLog(
                operator_id=current_user.id,
                role=current_user.role,
                action="requisition_create",
                target_type="requisition",
                target_id=None,
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
    """教师/班委撤销一笔有效领用（红冲原则：不删除不编辑原流水）。

    - 单事务内：原流水标记 reversed、库存原子回加、生成红冲流水、写操作日志。
    - 停用耗材也允许撤销回加（库存保留在停用档案上，符合"带库存停用"语义）。
    """
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
        # 条件原子回加：不校验耗材状态，停用档案同样回加
        db.execute(
            update(Consumable)
            .where(Consumable.id == requisition.consumable_id)
            .values(current_stock=Consumable.current_stock + requisition.quantity)
        )
        db.flush()
        db.refresh(consumable)
        stock_after = consumable.current_stock

        requisition.status = "reversed"
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

    db.refresh(reversal)
    db.refresh(consumable)
    return _record_out(reversal, consumable, db.get(User, reversal.user_id))


@router.get("")
def list_requisitions(
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    type_filter: Annotated[str | None, Query(alias="type")] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> RequisitionPageOut:
    """领用流水列表：按流水 id 倒序分页；学生仅可见本人流水。

    - 可选过滤参数：type=normal/reversal、status=active/reversed（组合可用，完整筛选由 T07 提供）。
    """
    if type_filter is not None and type_filter not in VALID_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="type 参数仅支持 normal/reversal",
        )
    if status_filter is not None and status_filter not in VALID_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="status 参数仅支持 active/reversed",
        )

    conditions = []
    if current_user.role == "student":
        conditions.append(Requisition.user_id == current_user.id)
    if type_filter is not None:
        conditions.append(Requisition.type == type_filter)
    if status_filter is not None:
        conditions.append(Requisition.status == status_filter)

    statement = select(Requisition)
    if conditions:
        statement = statement.where(*conditions)
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    records = db.scalars(
        statement.order_by(Requisition.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    consumables = {
        item.id: item
        for item in db.scalars(
            select(Consumable).where(
                Consumable.id.in_({record.consumable_id for record in records})
            )
        ).all()
    }
    users = {
        item.id: item
        for item in db.scalars(
            select(User).where(User.id.in_({record.user_id for record in records}))
        ).all()
    }
    items = [
        _record_out(
            record,
            consumables.get(record.consumable_id),
            users.get(record.user_id),
        )
        for record in records
    ]
    return RequisitionPageOut(
        items=items, total=total, page=page, page_size=page_size
    )


@router.get("/{requisition_id}")
def get_requisition(
    requisition_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> RequisitionDetailOut:
    """流水详情：红冲流水带原流水信息（origin），被撤销的原流水带红冲引用（reversal）。"""
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
