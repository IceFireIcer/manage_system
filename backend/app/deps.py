from collections.abc import Callable
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.security import decode_access_token
from app.setup_service import SETUP_INCOMPLETE_DETAIL, setup_is_completed

AUTH_PATH_PREFIX = "/api/v1/auth/"
SETUP_PATH_PREFIX = "/api/v1/setup/"
BOOTSTRAP_PATH_PREFIX = "/api/v1/bootstrap/"
HEALTH_PATH = "/api/v1/health"
SUPER_ADMIN_ROLE = "super_admin"
CHANGE_PASSWORD_REQUIRED_DETAIL = "请先修改初始密码"


def _allows_pending_password_change(path: str) -> bool:
    return path.startswith(AUTH_PATH_PREFIX) or path.startswith(SETUP_PATH_PREFIX)


def _allows_before_setup(path: str) -> bool:
    return (
        path.startswith(AUTH_PATH_PREFIX)
        or path.startswith(SETUP_PATH_PREFIX)
        or path.startswith(BOOTSTRAP_PATH_PREFIX)
        or path == HEALTH_PATH
    )


def get_current_user(
    request: Request,
    access_token: Annotated[str | None, Cookie()] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="未登录",
    )
    if not access_token:
        raise credentials_error
    payload = decode_access_token(access_token)
    if payload is None or "sub" not in payload:
        raise credentials_error
    user = db.get(User, int(payload["sub"]))
    if user is None or user.status != "enabled":
        raise credentials_error
    if payload.get("sv") != user.session_version:
        raise credentials_error
    if user.must_change_password and not _allows_pending_password_change(request.url.path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=CHANGE_PASSWORD_REQUIRED_DETAIL,
        )
    if not _allows_before_setup(request.url.path) and not setup_is_completed(db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=SETUP_INCOMPLETE_DETAIL,
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*allowed_roles: str) -> Callable:
    def checker(current_user: CurrentUser) -> User:
        if current_user.role == SUPER_ADMIN_ROLE or current_user.role in allowed_roles:
            return current_user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权限访问",
        )

    return checker
