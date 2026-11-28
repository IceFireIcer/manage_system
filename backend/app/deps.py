from collections.abc import Callable
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.class_scope import ROLE_SUPER_ADMIN, resolve_active_class_id
from app.database import get_db
from app.models import User
from app.roles import granted_roles, resolve_active_role, role_passes
from app.security import decode_access_token
from app.setup_service import SETUP_INCOMPLETE_DETAIL, setup_is_completed

AUTH_PATH_PREFIX = "/api/v1/auth/"
SETUP_PATH_PREFIX = "/api/v1/setup/"
BOOTSTRAP_PATH_PREFIX = "/api/v1/bootstrap/"
HEALTH_PATH = "/api/v1/health"
SUPER_ADMIN_ROLE = "super_admin"
CHANGE_PASSWORD_REQUIRED_DETAIL = "请先修改初始密码"
EFFECTIVE_ROLE_ATTR = "active_role"


def _allows_pending_password_change(path: str) -> bool:
    return path.startswith(AUTH_PATH_PREFIX) or path.startswith(SETUP_PATH_PREFIX)


def _allows_before_setup(path: str) -> bool:
    return (
        path.startswith(AUTH_PATH_PREFIX)
        or path.startswith(SETUP_PATH_PREFIX)
        or path.startswith(BOOTSTRAP_PATH_PREFIX)
        or path == HEALTH_PATH
    )


def effective_role(user: User) -> str:
    """当前会话的生效角色；未解析时回落到账号默认角色。"""
    return getattr(user, EFFECTIVE_ROLE_ATTR, None) or user.role


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
    roles = granted_roles(db, user)
    setattr(user, EFFECTIVE_ROLE_ATTR, resolve_active_role(user, roles, payload.get("ar")))
    request.state.token_payload = payload
    if not _allows_before_setup(request.url.path) and not setup_is_completed(db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=SETUP_INCOMPLETE_DETAIL,
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_active_class_id(
    request: Request,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> int:
    payload = getattr(request.state, "token_payload", None) or {}
    requested = payload.get("cid")
    if isinstance(requested, str) and requested.lstrip("-").isdigit():
        requested = int(requested)
    if not isinstance(requested, int):
        requested = None
    return resolve_active_class_id(db, current_user, requested)


CurrentClassId = Annotated[int, Depends(get_active_class_id)]


def require_role(*allowed_roles: str) -> Callable:
    def checker(current_user: CurrentUser) -> User:
        if not role_passes(effective_role(current_user), allowed_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="无权限访问",
            )
        return current_user

    return checker
