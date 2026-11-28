from collections.abc import Callable
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.class_scope import resolve_active_class_id
from app.database import get_db
from app.models import User
from app.permissions import effective_permission_set
from app.roles import granted_roles, resolve_active_role
from app.security import decode_access_token
from app.setup_service import SETUP_INCOMPLETE_DETAIL, setup_is_completed

AUTH_PATH_PREFIX = "/api/v1/auth/"
SETUP_PATH_PREFIX = "/api/v1/setup/"
BOOTSTRAP_PATH_PREFIX = "/api/v1/bootstrap/"
HEALTH_PATH = "/api/v1/health"
SUPER_ADMIN_ROLE = "super_admin"
CHANGE_PASSWORD_REQUIRED_DETAIL = "请先修改初始密码"
PERMISSION_DENIED_DETAIL = "无权限访问"
EFFECTIVE_ROLE_ATTR = "active_role"
EFFECTIVE_CLASS_ATTR = "active_class_id"
EFFECTIVE_PERMISSIONS_ATTR = "effective_permissions"


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
    class_id = resolve_active_class_id(db, current_user, requested)
    setattr(current_user, EFFECTIVE_CLASS_ATTR, class_id)
    return class_id


CurrentClassId = Annotated[int, Depends(get_active_class_id)]


def require_super_admin(current_user: CurrentUser) -> User:
    """超管专属动作：按当前会话 active_role 判定，避免切换视角后越权。"""
    if effective_role(current_user) != SUPER_ADMIN_ROLE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=PERMISSION_DENIED_DETAIL,
        )
    return current_user


def require_permission(permission_key: str) -> Callable:
    """按 active_role 的有效权限校验；super_admin 由权限层恒全量放行。"""

    def checker(
        request: Request,
        current_user: CurrentUser,
        db: Annotated[Session, Depends(get_db)],
        class_id: Annotated[int, Depends(get_active_class_id)],
    ) -> User:
        role = effective_role(current_user)
        if role == SUPER_ADMIN_ROLE:
            return current_user
        cache = getattr(request.state, EFFECTIVE_PERMISSIONS_ATTR, None)
        scope = (role, class_id)
        if cache is None or cache[0] != scope:
            cache = (scope, effective_permission_set(db, role, class_id))
            setattr(request.state, EFFECTIVE_PERMISSIONS_ATTR, cache)
        if permission_key not in cache[1]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=PERMISSION_DENIED_DETAIL,
            )
        return current_user

    return checker
