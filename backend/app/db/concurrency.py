from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models import Consumable

LOCK_RETRY_DETAIL = "资源繁忙，请使用同一请求标识重试"
LOCK_RETRY_CODE = "retry_required"
LOCK_RETRY_AFTER_SECONDS = 1
MYSQL_LOCK_CONFLICT_CODES = frozenset({1205, 1213})
MYSQL_LOCK_CONFLICT_MARKERS = ("deadlock found", "lock wait timeout")


def lock_consumables(db: Session, consumable_ids: list[int]) -> dict[int, Consumable]:
    unique_ids = sorted(set(consumable_ids))
    if not unique_ids:
        return {}
    rows = db.scalars(
        select(Consumable)
        .where(Consumable.id.in_(unique_ids))
        .order_by(Consumable.id)
        .with_for_update()
    ).all()
    return {row.id: row for row in rows}


def is_lock_conflict(error: OperationalError) -> bool:
    original = getattr(error, "orig", None)
    args = getattr(original, "args", ()) if original is not None else ()
    if args and isinstance(args[0], int) and args[0] in MYSQL_LOCK_CONFLICT_CODES:
        return True
    message = str(error).lower()
    return any(marker in message for marker in MYSQL_LOCK_CONFLICT_MARKERS)


def retry_required_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=LOCK_RETRY_DETAIL,
        headers={
            "Retry-After": str(LOCK_RETRY_AFTER_SECONDS),
            "X-Error-Code": LOCK_RETRY_CODE,
        },
    )
