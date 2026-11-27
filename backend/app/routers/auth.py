import json
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth_logs import trim_auth_logs
from app.config import get_settings
from app.database import get_db
from app.deps import CurrentUser
from app.login_throttle import login_throttle
from app.models import User
from app.operation_log import record_operation, user_label
from app.schemas import ChangePasswordRequest, LoginRequest, SessionOut, UserOut
from app.security import (
    TOKEN_COOKIE_NAME,
    create_access_token,
    decode_access_token,
    hash_password,
    resolve_client_source,
    verify_password,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
LOGIN_THROTTLED_DETAIL = "登录尝试过于频繁，请稍后重试"


def _log_login(
    db: Session,
    user: User | None,
    account: str,
    remark: str | None,
    result: str = "success",
) -> None:
    record_operation(
        db,
        action="login",
        operator=user,
        operator_name=None if user is not None else account,
        result=result,
        target_type="user",
        target_id=user.id if user else None,
        target_label=user_label(user) or account,
        after_value=user.account if user else None,
        remark=remark or f"账号 {account} 登录成功",
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
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> UserOut:
    settings = get_settings()
    account = payload.account.strip()
    account_key = account.lower()
    source = resolve_client_source(
        request.client.host if request.client else "",
        request.headers.get("x-forwarded-for"),
        settings.trusted_proxy_set,
    )
    retry_after, should_log_throttle = login_throttle.check(account_key, source)
    if retry_after > 0:
        if should_log_throttle:
            _log_login(db, None, account, f"账号 {account} 登录被节流")
            db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=LOGIN_THROTTLED_DETAIL,
            headers={"Retry-After": str(retry_after)},
        )

    error = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误")
    user = db.scalars(select(User).where(User.account == account)).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        newly_blocked = login_throttle.record_failure(account_key, source)
        _log_login(db, user, account, f"账号 {account} 密码错误")
        db.commit()
        if newly_blocked and trim_auth_logs(db, settings) > 0:
            db.commit()
        raise error
    if user.status != "enabled":
        newly_blocked = login_throttle.record_failure(account_key, source)
        _log_login(db, user, account, f"账号 {account} 已被停用")
        db.commit()
        if newly_blocked and trim_auth_logs(db, settings) > 0:
            db.commit()
        raise error

    login_throttle.record_success(account_key, source)
    _log_login(db, user, account, None)
    db.commit()
    if trim_auth_logs(db, settings) > 0:
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
    record_operation(
        db,
        action="password_change",
        operator=current_user,
        target_type="user",
        target_id=current_user.id,
        target_label=user_label(current_user),
        after_value=json.dumps(
            {"account": current_user.account, "initial": was_initial_password},
            ensure_ascii=False,
        ),
        remark=f"账号 {current_user.account} 修改密码",
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
    record_operation(
        db,
        action="logout",
        operator=current_user,
        target_type="user",
        target_id=current_user.id,
        target_label=user_label(current_user),
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
    record_operation(
        db,
        action="logout_all",
        operator=current_user,
        target_type="user",
        target_id=current_user.id,
        target_label=user_label(current_user),
        after_value=json.dumps(
            {"account": current_user.account, "session_version": current_user.session_version},
            ensure_ascii=False,
        ),
        remark=f"账号 {current_user.account} 撤销全部会话",
    )
    db.commit()
    response.delete_cookie(key=TOKEN_COOKIE_NAME)
    return {"status": "ok"}


@router.get("/me")
def me(current_user: CurrentUser) -> UserOut:
    return current_user
