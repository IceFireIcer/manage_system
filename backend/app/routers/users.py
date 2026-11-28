import json
import secrets
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import roster_import
from app.database import get_db
from app.deps import require_role
from app.models import User
from app.operation_log import record_operation, user_label
from app.schemas_users import (
    ACCOUNT_MAX_LENGTH,
    NAME_MAX_LENGTH,
    RosterImportPreviewOut,
    RosterImportReceiptOut,
    RosterImportRequest,
)
from app.security import hash_password

router = APIRouter(prefix="/api/v1/users", tags=["users"])

ALLOWED_STATUSES = ("enabled", "disabled")
ROSTER_TEMPLATE = "\ufeff学号,姓名\n20260001,张三\n20260002,李四\n"


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

    id: int
    name: str
    role: str

    model_config = {"from_attributes": True}


class UserDetailOption(UserOption):
    account: str


class StatusUpdate(BaseModel):
    status: str


class UserUpdate(BaseModel):
    account: str | None = Field(default=None, max_length=ACCOUNT_MAX_LENGTH)
    name: str | None = Field(default=None, max_length=NAME_MAX_LENGTH)

    @model_validator(mode="after")
    def require_change(self):
        if self.account is None and self.name is None:
            raise ValueError("请提供要更正的姓名或学号")
        return self


class HistoryUserOption(BaseModel):
    id: int
    account: str
    name: str
    role: str
    status: str

    model_config = {"from_attributes": True}


class PasswordResetOut(BaseModel):
    user: UserListItem
    temporary_password: str


def _normalize_account(value: str) -> str:
    account = value.strip()
    if not account:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="学号不能为空"
        )
    if len(account) > ACCOUNT_MAX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"学号不能超过 {ACCOUNT_MAX_LENGTH} 个字符",
        )
    if any(ch.isspace() for ch in account):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="学号不能包含空格等空白字符",
        )
    return account


def _normalize_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="姓名不能为空"
        )
    if len(name) > NAME_MAX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"姓名不能超过 {NAME_MAX_LENGTH} 个字符",
        )
    return name


def _log_operation(
    db: Session,
    operator: User,
    action: str,
    target: User,
    before_value: str | None,
    after_value: str | None,
) -> None:
    record_operation(
        db,
        action=action,
        operator=operator,
        target_type="user",
        target_id=target.id,
        target_label=user_label(target),
        before_value=before_value,
        after_value=after_value,
    )


def _user_snapshot(user: User) -> str:
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
    detail: bool = False,
):
    statement = select(User).where(User.status == "enabled").order_by(User.id)
    users = db.scalars(statement).all()
    if detail:
        return [UserDetailOption.model_validate(user) for user in users]
    return [UserOption.model_validate(user) for user in users]


@router.get("/history-options")
def list_history_user_options(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[User, Depends(require_role("teacher", "admin"))],
) -> list[HistoryUserOption]:
    statement = select(User).order_by(User.id)
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
        must_change_password=True,
    )
    db.add(user)
    db.flush()
    _log_operation(db, current_user, "user_create", user, None, _user_snapshot(user))
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_role("admin"))],
) -> UserListItem:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )
    if user.role != "student":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅允许更正学生账号",
        )
    field_kind = "学号" if payload.account is not None else "姓名"
    snapshot_before = _user_snapshot(user)
    changed = False
    try:
        if payload.account is not None:
            account = _normalize_account(payload.account)
            if account != user.account:
                conflict = db.scalars(
                    select(User).where(
                        User.account == account, User.id != user.id
                    )
                ).first()
                if conflict is not None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            f"学号 {account} 已被其他人员使用，"
                            "请核对同名人员后重试"
                        ),
                    )
                user.account = account
                changed = True
        if payload.name is not None:
            name = _normalize_name(payload.name)
            if name != user.name:
                user.name = name
                changed = True
        if not changed:
            return user
        db.flush()
        _log_operation(
            db,
            current_user,
            "user_update",
            user,
            snapshot_before,
            _user_snapshot(user),
        )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"更正{field_kind}时发生并发冲突，请刷新名单后重试",
        ) from error
    db.refresh(user)
    return user


@router.post("/{user_id}/reset-password")
def reset_student_password(
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_role("admin"))],
) -> PasswordResetOut:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能重置自己的密码",
        )
    if user.role != "student":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅允许重置学生密码",
        )
    temporary_password = secrets.token_urlsafe(9)
    user.password_hash = hash_password(temporary_password)
    user.must_change_password = True
    user.session_version += 1
    _log_operation(
        db,
        current_user,
        "user_password_reset",
        user,
        _user_snapshot(user),
        json.dumps(
            {
                "id": user.id,
                "account": user.account,
                "session_version": user.session_version,
                "must_change_password": True,
            },
            ensure_ascii=False,
        ),
    )
    db.commit()
    db.refresh(user)
    return PasswordResetOut(user=user, temporary_password=temporary_password)


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
        user.session_version += 1
        db.commit()
    db.refresh(user)
    return user


@router.get("/import/template")
def download_roster_template(
    _current_user: Annotated[User, Depends(require_role("admin"))],
) -> Response:
    return Response(
        content=ROSTER_TEMPLATE,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="student_roster_template.csv"'
        },
    )


@router.post("/import/preview")
def preview_roster_import(
    payload: RosterImportRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_role("admin"))],
) -> RosterImportPreviewOut:
    return roster_import.build_preview(
        db, class_id=admin.class_id, payload=payload
    )


@router.post("/import")
def import_roster(
    payload: RosterImportRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_role("admin"))],
) -> JSONResponse:
    receipt, created = roster_import.execute_import(
        db, operator=admin, payload=payload
    )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=jsonable_encoder(receipt),
    )


@router.get("/import/batches/{batch_id}")
def get_roster_import_batch(
    batch_id: int,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_role("admin"))],
) -> RosterImportReceiptOut:
    return roster_import.get_receipt(
        db, batch_id=batch_id, class_id=admin.class_id
    )
