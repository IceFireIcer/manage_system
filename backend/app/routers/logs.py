from datetime import date, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_role
from app.models import OperationLog, User
from app.schemas_logs import OperationLogItemOut, OperationLogPageOut

router = APIRouter(prefix="/api/v1/logs", tags=["logs"])

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

StaffUser = Annotated[User, Depends(require_role("teacher", "admin"))]


@router.get("")
def list_operation_logs(
    db: Annotated[Session, Depends(get_db)],
    _staff: StaffUser,
    action: Annotated[str | None, Query()] = None,
    operator_id: Annotated[int | None, Query()] = None,
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> OperationLogPageOut:
    filters = []
    if action is not None and action.strip():
        filters.append(OperationLog.action.contains(action.strip()))
    if operator_id is not None:
        filters.append(OperationLog.operator_id == operator_id)
    if start_date is not None:
        filters.append(OperationLog.created_at >= datetime.combine(start_date, time.min))
    if end_date is not None:
        filters.append(
            OperationLog.created_at
            <= datetime.combine(end_date, time.min) + timedelta(days=1) - timedelta(seconds=1)
        )

    total = db.scalar(
        select(func.count()).select_from(OperationLog).where(*filters)
    ) or 0

    rows = db.execute(
        select(OperationLog, User.name)
        .outerjoin(User, OperationLog.operator_id == User.id)
        .where(*filters)
        .order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    items = [
        OperationLogItemOut(
            id=log.id,
            created_at=log.created_at,
            operator_name=operator_name,
            role=log.role,
            action=log.action,
            target_type=log.target_type,
            target_id=log.target_id,
            before_value=log.before_value,
            after_value=log.after_value,
            remark=log.remark,
        )
        for log, operator_name in rows
    ]
    return OperationLogPageOut(
        items=items, total=total, page=page, page_size=page_size
    )
