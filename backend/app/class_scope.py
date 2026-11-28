"""多班级访问范围与 active_class 解析工具。

超管可访问全部班级；教师只能访问被分配（class_memberships）的班级加主班；
班委/学生固定在主班。所有解析都基于会话中的 active_class，禁止前端传
class_id 越权。v1 未引入独立角色表前，兼容既有 admin=班委 命名。
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Class, ClassMembership, ClassTerm, User

ROLE_SUPER_ADMIN = "super_admin"
ROLE_TEACHER = "teacher"
ROLE_CLASS_COMMITTEE = "class_committee"
LEGACY_CLASS_COMMITTEE = "admin"
ROLE_STUDENT = "student"
CLASS_COMMITTEE_ROLES = (ROLE_CLASS_COMMITTEE, LEGACY_CLASS_COMMITTEE)
DEFAULT_CLASS_NAME = "默认班级"
ENABLED = "enabled"


def is_super_admin(user: User) -> bool:
    return user.role == ROLE_SUPER_ADMIN


def is_teacher(user: User) -> bool:
    return user.role == ROLE_TEACHER


def is_class_committee(user: User) -> bool:
    return user.role in CLASS_COMMITTEE_ROLES


def can_switch_class(user: User) -> bool:
    return is_super_admin(user) or is_teacher(user)


def allowed_class_ids(db: Session, user: User) -> list[int]:
    if is_super_admin(user):
        ids = list(
            db.scalars(
                select(Class.id).where(Class.status == ENABLED).order_by(Class.id)
            )
        )
        return ids or [user.class_id]
    if is_teacher(user):
        assigned = set(
            db.scalars(
                select(ClassMembership.class_id).where(
                    ClassMembership.user_id == user.id
                )
            )
        )
        assigned.add(user.class_id)
        return sorted(assigned)
    return [user.class_id]


def can_access_class(db: Session, user: User, class_id: int) -> bool:
    return class_id in allowed_class_ids(db, user)


def resolve_active_class_id(
    db: Session, user: User, requested: int | None = None
) -> int:
    allowed = allowed_class_ids(db, user)
    if requested is not None and requested in allowed:
        return requested
    if user.class_id in allowed:
        return user.class_id
    return allowed[0] if allowed else user.class_id


def class_display_name(db: Session, class_id: int) -> str:
    obj = db.get(Class, class_id)
    return obj.name if obj is not None else DEFAULT_CLASS_NAME


def active_term_name(db: Session, class_id: int) -> str | None:
    term = db.scalars(
        select(ClassTerm)
        .where(ClassTerm.class_id == class_id, ClassTerm.status == "active")
        .order_by(ClassTerm.id)
    ).first()
    return term.name if term is not None else None


def class_brief(db: Session, class_id: int) -> dict:
    return {
        "id": class_id,
        "name": class_display_name(db, class_id),
        "term_name": active_term_name(db, class_id),
    }


def available_class_briefs(db: Session, user: User) -> list[dict]:
    return [class_brief(db, class_id) for class_id in allowed_class_ids(db, user)]


def ensure_class_ids_exist(db: Session, class_ids: Sequence[int]) -> None:
    """占位辅助：确认给定班级存在，供后续接口扩展使用。"""
    if not class_ids:
        return