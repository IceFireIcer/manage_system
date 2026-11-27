from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import business_time
from app.config import Settings, get_settings
from app.models import OperationLog

AUTH_LOG_ACTIONS = ("login", "logout")


def trim_auth_logs(db: Session, settings: Settings | None = None) -> int:
    current = settings or get_settings()
    deleted = 0
    actions_filter = OperationLog.action.in_(AUTH_LOG_ACTIONS)

    if current.auth_log_retention_days > 0:
        cutoff = business_time.now() - timedelta(days=current.auth_log_retention_days)
        result = db.execute(
            delete(OperationLog).where(
                actions_filter, OperationLog.created_at < cutoff
            )
        )
        deleted += result.rowcount or 0

    if current.auth_log_max_rows > 0:
        total = (
            db.scalar(select(func.count()).select_from(OperationLog).where(actions_filter))
            or 0
        )
        excess = total - current.auth_log_max_rows
        if excess > 0:
            stale_ids = db.scalars(
                select(OperationLog.id)
                .where(actions_filter)
                .order_by(OperationLog.created_at.asc(), OperationLog.id.asc())
                .limit(excess)
            ).all()
            if stale_ids:
                result = db.execute(
                    delete(OperationLog).where(OperationLog.id.in_(stale_ids))
                )
                deleted += result.rowcount or 0

    return deleted
