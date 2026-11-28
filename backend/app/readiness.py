from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.provisioning import (
    BOOTSTRAP_MESSAGES,
    bootstrap_state,
    enabled_admin_count,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
ALEMBIC_SCRIPTS = BACKEND_DIR / "alembic"

DATABASE_OK = "ok"
DATABASE_UNAVAILABLE = "unavailable"
MIGRATION_CURRENT = "current"
MIGRATION_OUTDATED = "outdated"
MIGRATION_MISSING = "missing"
MIGRATION_UNMANAGED = "unmanaged"
BOOTSTRAP_UNKNOWN = "unknown"
STATE_READY = "ready"
STATE_NOT_READY = "not_ready"
STATE_DATABASE_UNAVAILABLE = "database_unavailable"
DATABASE_UNAVAILABLE_MESSAGE = "数据库不可用，服务尚未就绪"
MIGRATION_MESSAGES = {
    MIGRATION_OUTDATED: "数据库迁移版本落后于代码，请完成迁移后重试",
    MIGRATION_MISSING: "数据库尚未完成迁移，请先执行 alembic upgrade head",
}


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    state: str
    message: str
    checks: dict[str, str]


def code_migration_head() -> str | None:
    try:
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_SCRIPTS))
        return ScriptDirectory.from_config(config).get_current_head()
    except Exception:
        return None


def _database_migration_version(db: Session) -> str | None:
    try:
        version = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
        db.rollback()
    except Exception:
        db.rollback()
        return None
    return str(version) if version else None


def _migration_status(db: Session) -> str:
    version = _database_migration_version(db)
    if version is not None:
        head = code_migration_head()
        if head is None or version == head:
            return MIGRATION_CURRENT
        return MIGRATION_OUTDATED
    try:
        has_users = inspect(db.get_bind()).has_table("users")
        db.rollback()
    except Exception:
        db.rollback()
        return MIGRATION_MISSING
    return MIGRATION_UNMANAGED if has_users else MIGRATION_MISSING


def _migration_ready(status: str, settings: Settings) -> bool:
    if status == MIGRATION_CURRENT:
        return True
    return not settings.is_production and status == MIGRATION_UNMANAGED


def _database_available(db: Session) -> bool:
    try:
        db.execute(text("SELECT 1"))
        db.rollback()
    except Exception:
        db.rollback()
        return False
    return True


def build_readiness(db: Session, settings: Settings | None = None) -> ReadinessReport:
    current = settings or get_settings()
    if not _database_available(db):
        return ReadinessReport(
            ready=False,
            state=STATE_DATABASE_UNAVAILABLE,
            message=DATABASE_UNAVAILABLE_MESSAGE,
            checks={
                "database": DATABASE_UNAVAILABLE,
                "migration": MIGRATION_MISSING,
                "bootstrap": BOOTSTRAP_UNKNOWN,
            },
        )

    migration = _migration_status(db)
    try:
        state = bootstrap_state(db)
        admins = enabled_admin_count(db)
    except Exception:
        db.rollback()
        return ReadinessReport(
            ready=False,
            state=STATE_DATABASE_UNAVAILABLE,
            message=DATABASE_UNAVAILABLE_MESSAGE,
            checks={
                "database": DATABASE_UNAVAILABLE,
                "migration": migration,
                "bootstrap": BOOTSTRAP_UNKNOWN,
            },
        )

    migration_ready = _migration_ready(migration, current)
    bootstrap_ready = state == STATE_READY
    ready = migration_ready and bootstrap_ready
    if not migration_ready:
        state_out = STATE_NOT_READY
        message = MIGRATION_MESSAGES.get(migration, "数据库迁移状态异常")
    elif bootstrap_ready:
        state_out = state
        message = BOOTSTRAP_MESSAGES[state]
    else:
        state_out = state
        message = f"{BOOTSTRAP_MESSAGES[state]}（可用管理员 {admins} 个）"

    return ReadinessReport(
        ready=ready,
        state=state_out,
        message=message,
        checks={
            "database": DATABASE_OK,
            "migration": migration,
            "bootstrap": state,
        },
    )
