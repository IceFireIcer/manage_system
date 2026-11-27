from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import business_time, stock_alerts
from app.database import get_db
from app.deps import require_role
from app.models import Consumable, User
from app.schemas_alerts import StockAlertAction, StockAlertListOut, StockAlertOut

router = APIRouter(prefix="/api/v1/inventory/alerts", tags=["inventory-alerts"])

StaffUser = Annotated[User, Depends(require_role("teacher", "admin"))]
AdminUser = Annotated[User, Depends(require_role("admin"))]


def _response(
    db: Session, class_id: int, include_completed: bool
) -> StockAlertListOut:
    items, active_count = stock_alerts.list_alerts(
        db, class_id, include_completed=include_completed
    )
    return StockAlertListOut(
        generated_at=business_time.now().replace(microsecond=0),
        active_count=active_count,
        items=items,
    )


@router.get("")
def list_stock_alerts(
    db: Annotated[Session, Depends(get_db)],
    staff: StaffUser,
    include_completed: Annotated[bool, Query()] = False,
) -> StockAlertListOut:
    return _response(db, staff.class_id, include_completed)


@router.post("/sync")
def sync_stock_alerts(
    db: Annotated[Session, Depends(get_db)],
    admin: AdminUser,
) -> StockAlertListOut:
    stock_alerts.sync_class(db, admin.class_id, source="manual")
    db.commit()
    return _response(db, admin.class_id, False)


@router.patch("/{consumable_id}")
def act_on_stock_alert(
    consumable_id: int,
    payload: StockAlertAction,
    db: Annotated[Session, Depends(get_db)],
    admin: AdminUser,
) -> StockAlertOut:
    alert = stock_alerts.get_alert(db, admin.class_id, consumable_id)
    if alert is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="待办不存在"
        )
    stock_alerts.handle_action(db, alert, admin, payload.action, payload.note)
    db.refresh(alert)
    consumable = db.get(Consumable, consumable_id)
    if consumable is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="耗材不存在"
        )
    return stock_alerts.build_out(alert, consumable)


@router.get("/{consumable_id}")
def get_stock_alert(
    consumable_id: int,
    db: Annotated[Session, Depends(get_db)],
    staff: StaffUser,
) -> StockAlertOut:
    alert = stock_alerts.get_alert(db, staff.class_id, consumable_id)
    if alert is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="待办不存在"
        )
    consumable = db.get(Consumable, consumable_id)
    if consumable is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="耗材不存在"
        )
    return stock_alerts.build_out(alert, consumable)
