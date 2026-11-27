from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.provisioning import BOOTSTRAP_MESSAGES, bootstrap_state
from app.readiness import build_readiness
from app.request_context import current_request_id

router = APIRouter(prefix="/api/v1/bootstrap", tags=["bootstrap"])


NOT_READY_STATUS = status.HTTP_503_SERVICE_UNAVAILABLE


class BootstrapStatus(BaseModel):
    state: str
    message: str


class ReadinessStatus(BaseModel):
    state: str
    message: str
    ready: bool
    checks: dict[str, str]
    request_id: str = ""


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
) -> ReadinessStatus:
    report = build_readiness(db)
    if not report.ready:
        response.status_code = NOT_READY_STATUS
    return ReadinessStatus(
        state=report.state,
        message=report.message,
        ready=report.ready,
        checks=report.checks,
        request_id=current_request_id(),
    )
