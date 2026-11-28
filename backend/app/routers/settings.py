"""系统参数：设置页读取，超级管理员修改并写审计。"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import require_permission, require_super_admin
from app.models import User
from app.operation_log import record_operation
from app.setup_service import get_setting, set_setting

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

ASSISTANT_RETENTION_KEY = "assistant_retention_days"
RETENTION_MIN = 0
RETENTION_MAX = 3650


class SystemSettingsOut(BaseModel):
    assistant_retention_days: int


class SystemSettingsIn(BaseModel):
    assistant_retention_days: int = Field(ge=RETENTION_MIN, le=RETENTION_MAX)


def _current_retention(db: Session) -> int:
    raw = get_setting(db, ASSISTANT_RETENTION_KEY)
    if raw is not None:
        try:
            return max(RETENTION_MIN, int(raw))
        except (TypeError, ValueError):
            pass
    return max(RETENTION_MIN, get_settings().assistant_retention_days)


def _snapshot(days: int) -> str:
    return json.dumps({"assistant_retention_days": days}, ensure_ascii=False)


@router.get("/system")
def read_system_settings(
    db: Annotated[Session, Depends(get_db)],
    _viewer: Annotated[User, Depends(require_permission("settings.manage"))],
) -> SystemSettingsOut:
    return SystemSettingsOut(assistant_retention_days=_current_retention(db))


@router.put("/system")
def update_system_settings(
    payload: SystemSettingsIn,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_super_admin)],
) -> SystemSettingsOut:
    before = _current_retention(db)
    days = payload.assistant_retention_days
    set_setting(
        db,
        key=ASSISTANT_RETENTION_KEY,
        value=str(days),
        updated_by=current_user.account,
    )
    record_operation(
        db,
        action="system_setting_update",
        operator=current_user,
        target_type="system",
        target_label=ASSISTANT_RETENTION_KEY,
        before_value=_snapshot(before),
        after_value=_snapshot(days),
        remark="更新聊天留存天数",
    )
    db.commit()
    return SystemSettingsOut(assistant_retention_days=days)
