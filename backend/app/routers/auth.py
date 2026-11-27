import json
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import CurrentUser
from app.models import OperationLog, User
from app.schemas import ChangePasswordRequest, LoginRequest, SessionOut, UserOut
from app.security import (
    TOKEN_COOKIE_NAME,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _log_login(db: Session, user: User | None, account: str, remark: str | None) -> None:
    db.add(
        OperationLog(
            operator_id=user.id if user else None,
            role=user.role if user else None,
            action="login",
            target_type="user",
            target_id=user.id if user else None,
            after_value=user.account if user else None,
            remark=remark or f"账号 {account} 登录成功",
        )
    )


def _set_session_cookie(response: Response, token: str, expire_seconds: int) -> None:
    settings = get_settings()
    response.set_cookie(
        key=TOKEN_COOKIE_NAME,
        value=token,
        max_age=expire_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure_effective,
        path="/",
    )


def _session_expires_at(request: Request) -> datetime | None:
    token = request.cookies.get(TOKEN_COOKIE_NAME)
    payload = decode_access_token(token) if token else None
    if not payload:
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int | float):
        return None
    return datetime.fromtimestamp(exp, UTC)


@router.post("/login")
def login(
    payload: LoginRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> UserOut:
    error = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误")
    user = db.scalars(select(User).where(User.account == payload.account)).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        _log_login(db, user, payload.account, f"账号 {payload.account} 密码错误")
        db.commit()
        raise error
    if user.status != "enabled":
        _log_login(db, user, payload.account, f"账号 {payload.account} 已被停用")
        db.commit()
        raise error

    _log_login(db, user, payload.account, None)
    db.commit()

    token, expire_seconds = create_access_token(user.id, user.role, user.session_version)
    _set_session_cookie(response, token, expire_seconds)
    return user


@router.get("/session")
def session_info(
    request: Request,
    current_user: CurrentUser,
) -> SessionOut:
    return SessionOut(
        user=UserOut.model_validate(current_user),
        must_change_password=current_user.must_change_password,
        session_expires_at=_session_expires_at(request),
    )


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> SessionOut:
    if not verify_password(payload.old_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="原密码不正确",
        )
    if payload.new_password == payload.old_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="新密码不能与原密码相同",
        )

    current_user.password_hash = hash_password(payload.new_password)
    was_initial_password = current_user.must_change_password
    current_user.must_change_password = False
    current_user.session_version += 1
    db.add(
        OperationLog(
            operator_id=current_user.id,
            role=current_user.role,
            action="password_change",
            target_type="user",
            target_id=current_user.id,
            after_value=json.dumps(
                {"account": current_user.account, "initial": was_initial_password},
                ensure_ascii=False,
            ),
            remark=f"账号 {current_user.account} 修改密码",
        )
    )
    db.commit()
    db.refresh(current_user)

    token, expire_seconds = create_access_token(
        current_user.id, current_user.role, current_user.session_version
    )
    _set_session_cookie(response, token, expire_seconds)
    return SessionOut(
        user=UserOut.model_validate(current_user),
        must_change_password=current_user.must_change_password,
        session_expires_at=datetime.now(UTC) + timedelta(seconds=expire_seconds),
    )


@router.post("/logout")
def logout(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> dict[str, str]:
    db.add(
        OperationLog(
            operator_id=current_user.id,
            role=current_user.role,
            action="logout",
            target_type="user",
            target_id=current_user.id,
            after_value=json.dumps(
                {
                    "account": current_user.account,
                    "name": current_user.name,
                    "role": current_user.role,
                },
                ensure_ascii=False,
            ),
            remark=f"账号 {current_user.account} 退出登录",
        )
    )
    db.commit()
    settings = get_settings()
    response.delete_cookie(
        key=TOKEN_COOKIE_NAME,
        path="/",
        secure=settings.cookie_secure_effective,
        samesite="lax",
    )
    return {"status": "ok"}


@router.post("/logout-all")
def logout_all(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> dict[str, str]:
    current_user.session_version += 1
    db.add(
        OperationLog(
            operator_id=current_user.id,
            role=current_user.role,
            action="logout_all",
            target_type="user",
            target_id=current_user.id,
            after_value=json.dumps(
                {"account": current_user.account, "session_version": current_user.session_version},
                ensure_ascii=False,
            ),
            remark=f"账号 {current_user.account} 撤销全部会话",
        )
    )
    db.commit()
    response.delete_cookie(key=TOKEN_COOKIE_NAME)
    return {"status": "ok"}


@router.get("/me")
def me(current_user: CurrentUser) -> UserOut:
    return current_user
