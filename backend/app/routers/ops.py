from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.observability import collect_observability
from app.readiness import build_readiness
from app.request_context import current_request_id

router = APIRouter(prefix="/api/v1/ops", tags=["ops"])

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
OPS_FORBIDDEN_DETAIL = "运维观测仅允许运维入口访问"


def _is_ops_request(request: Request) -> bool:
    client_host = request.client.host if request.client else ""
    return client_host in LOOPBACK_HOSTS


@router.get("/observability")
def get_observability(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    if not _is_ops_request(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=OPS_FORBIDDEN_DETAIL,
        )
    readiness = build_readiness(db)
    payload = collect_observability()
    payload["request_id"] = current_request_id()
    payload["readiness"] = {
        "ready": readiness.ready,
        "state": readiness.state,
        "checks": readiness.checks,
    }
    return payload
