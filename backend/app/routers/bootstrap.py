from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.provisioning import (
    BOOTSTRAP_MESSAGES,
    bootstrap_state,
    enabled_admin_count,
)

router = APIRouter(prefix="/api/v1/bootstrap", tags=["bootstrap"])

READY = "ready"
NOT_READY_STATUS = status.HTTP_503_SERVICE_UNAVAILABLE


class BootstrapStatus(BaseModel):
    state: str
    message: str


@router.get("/status")
def get_bootstrap_status(
    db: Annotated[Session, Depends(get_db)],
) -> BootstrapStatus:
    state = bootstrap_state(db)
    return BootstrapStatus(state=state, message=BOOTSTRAP_MESSAGES[state])


@router.get("/readiness")
def get_readiness(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> BootstrapStatus:
    state = bootstrap_state(db)
    if state != READY:
        response.status_code = NOT_READY_STATUS
    return BootstrapStatus(
        state=state,
        message=(
            f"{BOOTSTRAP_MESSAGES[state]}（可用管理员 {enabled_admin_count(db)} 个）"
            if state != READY
            else BOOTSTRAP_MESSAGES[state]
        ),
    )
