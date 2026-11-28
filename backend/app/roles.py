"""四角色与多角色绑定的公共定义与查询辅助。

角色键保持向后兼容：admin 仍代表「班委」，super_admin 由 #57 初始化流程补齐。
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User, UserRole

ROLE_SUPER_ADMIN = "super_admin"
ROLE_TEACHER = "teacher"
ROLE_ADMIN = "admin"
ROLE_STUDENT = "student"

ALL_ROLES: tuple[str, ...] = (
    ROLE_SUPER_ADMIN,
    ROLE_TEACHER,
    ROLE_ADMIN,
    ROLE_STUDENT,
)

ROLE_LABELS: dict[str, str] = {
    ROLE_SUPER_ADMIN: "超级管理员",
    ROLE_TEACHER: "教师",
    ROLE_ADMIN: "班委",
    ROLE_STUDENT: "学生",
}

SWITCHABLE_ROLES: tuple[str, ...] = (ROLE_ADMIN, ROLE_STUDENT)


def default_grants(role: str) -> list[str]:
    """单个旧角色对应的授予集合；班委默认同时具备学生角色。"""
    if role == ROLE_ADMIN:
        return [ROLE_ADMIN, ROLE_STUDENT]
    if role in ALL_ROLES:
        return [role]
    return [role] if role else []


def granted_roles(db: Session, user: User) -> list[str]:
    """读取已授予角色；历史数据缺失时按旧角色推导，保证向后兼容。"""
    rows = list(
        db.scalars(select(UserRole.role).where(UserRole.user_id == user.id)).all()
    )
    if rows:
        return sorted(set(rows))
    return sorted(set(default_grants(user.role)))


def ensure_grants(
    db: Session,
    user: User,
    roles: Iterable[str] | None = None,
) -> list[str]:
    """幂等补齐 user_roles；返回最终授予角色集合。"""
    desired = list(roles) if roles is not None else default_grants(user.role)
    existing = set(
        db.scalars(select(UserRole.role).where(UserRole.user_id == user.id)).all()
    )
    for role in desired:
        if role and role not in existing:
            db.add(UserRole(user_id=user.id, role=role))
            existing.add(role)
    return sorted(existing)


def resolve_active_role(user: User, roles: list[str], requested: str | None) -> str:
    """计算当前会话的 active_role；未授予的角色一律回落到账号默认角色。"""
    if requested and requested in roles:
        return requested
    if user.role in roles:
        return user.role
    return roles[0] if roles else ROLE_STUDENT


def role_passes(active_role: str, allowed_roles: tuple[str, ...]) -> bool:
    """super_admin 通过所有旧角色检查。"""
    return active_role == ROLE_SUPER_ADMIN or active_role in allowed_roles
