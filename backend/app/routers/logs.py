import csv
import io
import json
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import business_time
from app.auth_logs import trim_audit_logs
from app.database import get_db
from app.deps import effective_role, require_permission
from app.models import OperationLog, User
from app.operation_log import (
    SENSITIVE_ACTIONS,
    is_sensitive_query,
    record_operation,
    status_snapshot,
)
from app.schemas_logs import OperationLogItemOut, OperationLogPageOut

router = APIRouter(prefix="/api/v1/logs", tags=["logs"])

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_EXPORT_ROWS = 100000
SENSITIVE_DETAIL = "无权限访问敏感审计事件"

EXPORT_COLUMN_LABELS = {
    "id": "日志编号",
    "created_at": "操作时间",
    "operator_name": "经办人",
    "operator_account": "经办账号",
    "role": "角色",
    "action": "操作类型",
    "result": "结果",
    "target_type": "对象类型",
    "target_id": "对象编号",
    "target_label": "对象标识",
    "before_value": "变更前",
    "after_value": "变更后",
    "remark": "备注",
}
STAFF_EXPORT_COLUMNS = (
    "id",
    "created_at",
    "operator_name",
    "role",
    "action",
    "result",
    "target_type",
    "target_id",
    "target_label",
    "remark",
)
ADMIN_EXPORT_COLUMNS = (
    "id",
    "created_at",
    "operator_name",
    "operator_account",
    "role",
    "action",
    "result",
    "target_type",
    "target_id",
    "target_label",
    "before_value",
    "after_value",
    "remark",
)

StaffUser = Annotated[User, Depends(require_permission("log.view"))]


def _build_filters(
    current_user: User,
    action: str | None,
    operator_id: int | None,
    target_type: str | None,
    target_id: int | None,
    result: str | None,
    keyword: str | None,
    start_date: date | None,
    end_date: date | None,
) -> list:
    filters = []
    if effective_role(current_user) not in ("admin", "super_admin"):
        if action is not None and is_sensitive_query(action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=SENSITIVE_DETAIL,
            )
        filters.append(OperationLog.action.notin_(SENSITIVE_ACTIONS))
    if action is not None and action.strip():
        filters.append(OperationLog.action.contains(action.strip()))
    if operator_id is not None:
        filters.append(OperationLog.operator_id == operator_id)
    if target_type is not None and target_type.strip():
        filters.append(OperationLog.target_type == target_type.strip())
    if target_id is not None:
        filters.append(OperationLog.target_id == target_id)
    if result is not None and result.strip():
        filters.append(OperationLog.result == result.strip().lower())
    if keyword is not None and keyword.strip():
        pattern = f"%{keyword.strip()}%"
        filters.append(
            or_(
                OperationLog.target_label.ilike(pattern),
                OperationLog.remark.ilike(pattern),
                OperationLog.before_value.ilike(pattern),
                OperationLog.after_value.ilike(pattern),
            )
        )
    if start_date is not None:
        filters.append(OperationLog.created_at >= business_time.day_start(start_date))
    if end_date is not None:
        filters.append(OperationLog.created_at < business_time.next_day_start(end_date))
    return filters


def _snapshot_object(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _target_label(log: OperationLog) -> str | None:
    if log.target_label:
        return log.target_label
    for value in (log.after_value, log.before_value):
        parsed = _snapshot_object(value)
        if not parsed:
            continue
        for key in ("name", "account", "order_no"):
            candidate = parsed.get(key)
            if candidate:
                return str(candidate)
    return None


def _serialize(log: OperationLog, joined_name: str | None, is_admin: bool) -> OperationLogItemOut:
    return OperationLogItemOut(
        id=log.id,
        created_at=log.created_at,
        operator_name=log.operator_name or joined_name,
        operator_account=log.operator_account if is_admin else None,
        role=log.role,
        action=log.action,
        result=log.result,
        target_type=log.target_type,
        target_id=log.target_id,
        target_label=_target_label(log),
        before_value=log.before_value,
        after_value=log.after_value,
        before_status=status_snapshot(log.before_value),
        after_status=status_snapshot(log.after_value),
        remark=log.remark,
    )


def _csv_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    text = str(value)
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def _export_columns(is_admin: bool) -> tuple[str, ...]:
    return ADMIN_EXPORT_COLUMNS if is_admin else STAFF_EXPORT_COLUMNS


def _csv_text(columns: tuple[str, ...], rows: list[OperationLog]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([EXPORT_COLUMN_LABELS[column] for column in columns])
    for log in rows:
        record = {
            "id": log.id,
            "created_at": log.created_at,
            "operator_name": log.operator_name or log.operator_account,
            "operator_account": log.operator_account,
            "role": log.role,
            "action": log.action,
            "result": log.result,
            "target_type": log.target_type,
            "target_id": log.target_id,
            "target_label": _target_label(log),
            "before_value": log.before_value,
            "after_value": log.after_value,
            "remark": log.remark,
        }
        writer.writerow([_csv_cell(record.get(column)) for column in columns])
    return "\ufeff" + buffer.getvalue()


@router.get("")
def list_operation_logs(
    db: Annotated[Session, Depends(get_db)],
    current_user: StaffUser,
    action: Annotated[str | None, Query()] = None,
    operator_id: Annotated[int | None, Query()] = None,
    target_type: Annotated[str | None, Query()] = None,
    target_id: Annotated[int | None, Query()] = None,
    result: Annotated[str | None, Query()] = None,
    keyword: Annotated[str | None, Query()] = None,
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> OperationLogPageOut:
    if business_time.reversed_range(start_date, end_date):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=business_time.REVERSE_RANGE_DETAIL,
        )
    filters = _build_filters(
        current_user,
        action,
        operator_id,
        target_type,
        target_id,
        result,
        keyword,
        start_date,
        end_date,
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
    is_admin = effective_role(current_user) in ("admin", "super_admin")
    items = [_serialize(log, joined_name, is_admin) for log, joined_name in rows]
    return OperationLogPageOut(
        items=items, total=total, page=page, page_size=page_size
    )


@router.get("/export")
def export_operation_logs(
    db: Annotated[Session, Depends(get_db)],
    current_user: StaffUser,
    action: Annotated[str | None, Query()] = None,
    operator_id: Annotated[int | None, Query()] = None,
    target_type: Annotated[str | None, Query()] = None,
    target_id: Annotated[int | None, Query()] = None,
    result: Annotated[str | None, Query()] = None,
    keyword: Annotated[str | None, Query()] = None,
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
) -> Response:
    if business_time.reversed_range(start_date, end_date):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=business_time.REVERSE_RANGE_DETAIL,
        )
    filters = _build_filters(
        current_user,
        action,
        operator_id,
        target_type,
        target_id,
        result,
        keyword,
        start_date,
        end_date,
    )
    total = db.scalar(
        select(func.count()).select_from(OperationLog).where(*filters)
    ) or 0
    rows = db.scalars(
        select(OperationLog)
        .where(*filters)
        .order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
        .limit(MAX_EXPORT_ROWS)
    ).all()
    is_admin = effective_role(current_user) == "admin"
    columns = _export_columns(is_admin)
    content = _csv_text(columns, list(rows))
    record_operation(
        db,
        action="log_export",
        operator=current_user,
        target_type="operation_log",
        target_label=f"导出 {len(rows)} 条",
        after_value=json.dumps(
            {
                "exported": len(rows),
                "matched": total,
                "filters": {
                    "action": action,
                    "operator_id": operator_id,
                    "target_type": target_type,
                    "target_id": target_id,
                    "result": result,
                    "keyword": keyword,
                    "start_date": start_date.isoformat() if start_date else None,
                    "end_date": end_date.isoformat() if end_date else None,
                },
                "fields": list(columns),
            },
            ensure_ascii=False,
        ),
        remark=f"导出操作日志 {len(rows)} 条",
    )
    db.commit()
    trim_audit_logs(db)
    filename = datetime.now().strftime("operation_logs_%Y%m%d_%H%M%S.csv")
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
