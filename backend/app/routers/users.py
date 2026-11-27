import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_role
from app.models import OperationLog, User
from app.security import hash_password

router = APIRouter(prefix="/api/v1/users", tags=["users"])

ALLOWED_STATUSES = ("enabled", "disabled")


class UserCreate(BaseModel):
    account: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=64)
    role: str


class UserListItem(BaseModel):
    id: int
    account: str
    name: str
    role: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class UserOption(BaseModel):
    """领用登记代填下拉的精简用户信息（无敏感字段）。"""

    id: int
    name: str
    role: str


class StatusUpdate(BaseModel):
    status: str


def _log_operation(
    db: Session,
    operator: User,
    action: str,
    target: User,
    before_value: str | None,
    after_value: str | None,
) -> None:
    db.add(
        OperationLog(
            operator_id=operator.id,
            role=operator.role,
            action=action,
            target_type="user",
            target_id=target.id,
            before_value=before_value,
            after_value=after_value,
        )
    )


def _user_snapshot(user: User) -> str:
    """账号快照：只含身份与状态，不写入任何密码材料。"""
    return json.dumps(
        {
            "id": user.id,
            "account": user.account,
            "name": user.name,
            "role": user.role,
            "status": user.status,
        },
        ensure_ascii=False,
    )


@router.get("")
def list_users(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[User, Depends(require_role("admin"))],
    role: str | None = None,
    user_status: Annotated[str | None, Query(alias="status")] = None,
) -> list[UserListItem]:
    statement = select(User).order_by(User.id)
    if role is not None:
        statement = statement.where(User.role == role)
    if user_status is not None:
        statement = statement.where(User.status == user_status)
    return db.scalars(statement).all()


@router.get("/options")
def list_user_options(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[User, Depends(require_role("teacher", "admin"))],
) -> list[UserOption]:
    """领用登记代填下拉：返回启用用户精简列表（无敏感字段），教师与班委可用。"""
    statement = select(User).where(User.status == "enabled").order_by(User.id)
    return db.scalars(statement).all()


@router.post("", status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_role("admin"))],
) -> UserListItem:
    if payload.role != "student":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅允许创建学生账号",
        )
    existing = db.scalars(select(User).where(User.account == payload.account)).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"账号 {payload.account} 已存在",
        )

    user = User(
        account=payload.account,
        password_hash=hash_password(payload.password),
        name=payload.name,
        role="student",
        status="enabled",
    )
    db.add(user)
    db.flush()
    _log_operation(db, current_user, "user_create", user, None, _user_snapshot(user))
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}/status")
def update_user_status(
    user_id: int,
    payload: StatusUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_role("admin"))],
) -> UserListItem:
    if payload.status not in ALLOWED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="状态仅允许 enabled 或 disabled",
        )
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能修改自己的账号状态",
        )
    if user.role != "student":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅允许对学生账号进行启停",
        )

    if user.status != payload.status:
        action = "user_enable" if payload.status == "enabled" else "user_disable"
        _log_operation(db, current_user, action, user, user.status, payload.status)
        user.status = payload.status
        db.commit()
    db.refresh(user)
    return user
