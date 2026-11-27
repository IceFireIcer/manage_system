"""批量领用登记：整单一个数据库事务，逐行条件原子扣减，任一行失败全部回滚。"""

import json
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import CurrentUser
from app.models import Consumable, OperationLog, Requisition, User
from app.schemas_consumables import STATUS_ENABLED
from app.schemas_requisitions import (
    MAX_BACKFILL_DAYS,
    RequisitionCreate,
    RequisitionCreateOut,
    RequisitionItemOut,
)

router = APIRouter(prefix="/api/v1/requisitions", tags=["requisitions"])


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
