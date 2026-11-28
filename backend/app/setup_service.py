from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business_time import now as business_now
from app.models import SystemSetting, User
from app.operation_log import record_operation

SETUP_COMPLETED_KEY = "setup_completed"
SETUP_INITIALIZED_AT_KEY = "setup_initialized_at"
SETUP_INITIAL_ADMIN_KEY = "setup_initial_admin_account"
SYSTEM_ACTOR = "system"
_TRUE_VALUES = {"1", "true", "yes", "on"}

SETUP_INCOMPLETE_DETAIL = "系统尚未完成初始化，请先完成初始化向导"
TEACHER_REQUIRED_DETAIL = "请先创建至少一名启用状态的教师"


class SetupError(Exception):
    pass


def get_setting(db: Session, key: str) -> str | None:
    row = db.get(SystemSetting, key)
    return row.value if row is not None else None


def set_setting(
    db: Session,
    *,
    key: str,
    value: str,
    updated_by: str | None = None,
) -> SystemSetting:
    row = db.get(SystemSetting, key)
    if row is None:
        row = SystemSetting(
            key=key,
            value=value,
            updated_by=updated_by,
            updated_at=business_now(),
        )
        db.add(row)
    else:
        row.value = value
        row.updated_by = updated_by
        row.updated_at = business_now()
    db.flush()
    return row


def has_enabled_teacher(db: Session) -> bool:
    return (
        db.scalar(
            select(User.id)
            .where(User.role == "teacher", User.status == "enabled")
            .limit(1)
        )
        is not None
    )


def setup_is_completed(db: Session) -> bool:
    value = get_setting(db, SETUP_COMPLETED_KEY)
    if value is None:
        # 未纳入初始化向导的既有部署：视为已完成，避免误阻断。
        return True
    return value.strip().lower() in _TRUE_VALUES


def needs_setup(db: Session) -> bool:
    return not setup_is_completed(db)


def record_initial_setup(db: Session, *, admin_account: str | None = None) -> bool:
    """补齐初始化记录，仅在缺失时写入，不覆盖已有状态。返回是否首次写入。"""
    created = False
    if get_setting(db, SETUP_COMPLETED_KEY) is None:
        set_setting(
            db, key=SETUP_COMPLETED_KEY, value="0", updated_by=SYSTEM_ACTOR
        )
        created = True
    if get_setting(db, SETUP_INITIALIZED_AT_KEY) is None:
        set_setting(
            db,
            key=SETUP_INITIALIZED_AT_KEY,
            value=business_now().isoformat(sep=" "),
            updated_by=SYSTEM_ACTOR,
        )
    if admin_account and get_setting(db, SETUP_INITIAL_ADMIN_KEY) is None:
        set_setting(
            db,
            key=SETUP_INITIAL_ADMIN_KEY,
            value=admin_account,
            updated_by=SYSTEM_ACTOR,
        )
    db.commit()
    return created


def setup_status(db: Session) -> dict[str, object]:
    completed = setup_is_completed(db)
    return {
        "setup_completed": completed,
        "needs_setup": not completed,
        "has_enabled_teacher": has_enabled_teacher(db),
        "initialized_at": get_setting(db, SETUP_INITIALIZED_AT_KEY),
        "initial_admin_account": get_setting(db, SETUP_INITIAL_ADMIN_KEY),
    }


def complete_setup(
    db: Session, *, operator_name: str | None = None, operator: User | None = None
) -> None:
    if not has_enabled_teacher(db):
        raise SetupError(TEACHER_REQUIRED_DETAIL)
    set_setting(
        db,
        key=SETUP_COMPLETED_KEY,
        value="1",
        updated_by=operator_name or SYSTEM_ACTOR,
    )
    record_operation(
        db,
        action="setup_complete",
        operator=operator,
        operator_name=operator_name,
        target_type="system",
        target_label="系统初始化",
        before_value=json.dumps({"setup_completed": False}, ensure_ascii=False),
        after_value=json.dumps({"setup_completed": True}, ensure_ascii=False),
        remark=f"{operator_name or SYSTEM_ACTOR} 完成系统初始化",
    )
    db.commit()