from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import CurrentUser, require_super_admin
from app.models import User
from app.provisioning import ProvisioningError, provision_teacher
from app.routers.auth import change_password as auth_change_password
from app.schemas import ChangePasswordRequest
from app.setup_service import SetupError, complete_setup, setup_status

router = APIRouter(prefix="/api/v1/setup", tags=["setup"])


class SetupStatusOut(BaseModel):
    setup_completed: bool
    needs_setup: bool
    has_enabled_teacher: bool
    initialized_at: str | None = None
    initial_admin_account: str | None = None


class SetupTeacherRequest(BaseModel):
    account: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    password: str | None = Field(default=None, max_length=64)


class SetupTeacherOut(BaseModel):
    user_id: int
    account: str
    name: str
    temporary_password: str
    generated: bool


@router.get("/status")
def get_setup_status(
    db: Annotated[Session, Depends(get_db)],
) -> SetupStatusOut:
    return SetupStatusOut(**setup_status(db))


@router.post("/change-password")
def change_super_admin_password(
    payload: ChangePasswordRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
):
    return auth_change_password(payload, response, db, current_user)


@router.post("/teachers", status_code=status.HTTP_201_CREATED)
def create_setup_teacher(
    payload: SetupTeacherRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_super_admin)],
) -> SetupTeacherOut:
    try:
        result = provision_teacher(
            db,
            account=payload.account,
            name=payload.name,
            password=payload.password,
            actor=current_user.name,
        )
    except ProvisioningError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return SetupTeacherOut(
        user_id=result.user_id,
        account=result.account,
        name=result.name,
        temporary_password=result.password,
        generated=result.generated,
    )


@router.post("/complete")
def finish_setup(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_super_admin)],
) -> SetupStatusOut:
    try:
        complete_setup(db, operator_name=current_user.name, operator=current_user)
    except SetupError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return SetupStatusOut(**setup_status(db))