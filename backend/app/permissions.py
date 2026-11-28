"""权限目录、角色矩阵与班级覆盖的有效权限计算。

有效权限 = 角色允许 &&（班级覆盖 ?? 角色默认开关）；super_admin 恒全量。
计算只使用当前会话的 active_role，不做多角色并集，避免越权叠加。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business_time import now as business_now
from app.models import ClassRolePermission, Permission, RolePermission
from app.roles import ROLE_ADMIN, ROLE_STUDENT, ROLE_SUPER_ADMIN, ROLE_TEACHER

SCOPE_SYSTEM: Final = "system"
SCOPE_CLASS: Final = "class"
SCOPE_PERSONAL: Final = "personal"

MATRIX_ROLES: Final[tuple[str, ...]] = (ROLE_TEACHER, ROLE_ADMIN, ROLE_STUDENT)
OVERRIDE_ROLES: Final[tuple[str, ...]] = (ROLE_ADMIN, ROLE_STUDENT)
ACTION_OVERRIDE_UPDATE: Final = "permission_override_update"

CATEGORY_LABELS: Final[dict[str, str]] = {
    "dashboard": "工作台",
    "inventory": "库存",
    "requisition": "领用",
    "record": "记录",
    "consumable": "耗材",
    "restock": "补货",
    "consumption": "消耗",
    "log": "日志",
    "audit": "审计",
    "course": "课程",
    "incident": "异常",
    "handover": "交接",
    "report": "报表",
    "profile": "个人",
    "assistant": "助手",
    "settings": "设置",
    "account": "账号",
    "permission": "权限",
    "llm": "LLM",
}


@dataclass(frozen=True)
class PermissionSpec:
    key: str
    label: str
    category: str
    scope: str
    allowed_roles: tuple[str, ...]
    default_roles: tuple[str, ...]
    toggleable_roles: tuple[str, ...] = ()
    locked: bool = False


_TEACHER_ADMIN = (ROLE_TEACHER, ROLE_ADMIN)
_ALL_ROLES = (ROLE_TEACHER, ROLE_ADMIN, ROLE_STUDENT)

CATALOG: Final[tuple[PermissionSpec, ...]] = (
    PermissionSpec(
        "dashboard.view", "工作台查看", "dashboard", SCOPE_CLASS,
        _ALL_ROLES, _ALL_ROLES, locked=True,
    ),
    PermissionSpec(
        "inventory.view", "库存查看", "inventory", SCOPE_CLASS,
        _ALL_ROLES, _ALL_ROLES, (ROLE_STUDENT,),
    ),
    PermissionSpec(
        "inventory.manage", "库存维护", "inventory", SCOPE_CLASS,
        _TEACHER_ADMIN, _TEACHER_ADMIN, (ROLE_ADMIN,),
    ),
    PermissionSpec(
        "requisition.view", "领用查看", "requisition", SCOPE_CLASS,
        _ALL_ROLES, _ALL_ROLES, (ROLE_STUDENT,),
    ),
    PermissionSpec(
        "requisition.create", "领用申请", "requisition", SCOPE_CLASS,
        _ALL_ROLES, _ALL_ROLES, locked=True,
    ),
    PermissionSpec(
        "requisition.approve", "领用审批", "requisition", SCOPE_CLASS,
        _TEACHER_ADMIN, _TEACHER_ADMIN, (ROLE_ADMIN,),
    ),
    PermissionSpec(
        "record.view", "领用记录", "record", SCOPE_CLASS,
        _ALL_ROLES, _ALL_ROLES, (ROLE_STUDENT,),
    ),
    PermissionSpec(
        "record.export", "记录导出", "record", SCOPE_CLASS,
        _TEACHER_ADMIN, _TEACHER_ADMIN, (ROLE_ADMIN,),
    ),
    PermissionSpec(
        "consumable.view", "耗材目录", "consumable", SCOPE_CLASS,
        _TEACHER_ADMIN, _TEACHER_ADMIN, (ROLE_ADMIN,),
    ),
    PermissionSpec(
        "consumable.manage", "耗材维护", "consumable", SCOPE_CLASS,
        _TEACHER_ADMIN, _TEACHER_ADMIN, (ROLE_ADMIN,),
    ),
    PermissionSpec(
        "restock.view", "补货查看", "restock", SCOPE_CLASS,
        _TEACHER_ADMIN, _TEACHER_ADMIN, (ROLE_ADMIN,),
    ),
    PermissionSpec(
        "restock.manage", "补货处理", "restock", SCOPE_CLASS,
        _TEACHER_ADMIN, _TEACHER_ADMIN, (ROLE_ADMIN,),
    ),
    PermissionSpec(
        "consumption.view", "消耗查看", "consumption", SCOPE_CLASS,
        _TEACHER_ADMIN, _TEACHER_ADMIN, (ROLE_ADMIN,),
    ),
    PermissionSpec(
        "consumption.manage", "消耗登记", "consumption", SCOPE_CLASS,
        _TEACHER_ADMIN, _TEACHER_ADMIN, (ROLE_ADMIN,),
    ),
    PermissionSpec(
        "log.view", "操作日志", "log", SCOPE_CLASS,
        _TEACHER_ADMIN, _TEACHER_ADMIN, (ROLE_ADMIN,),
    ),
    PermissionSpec(
        "audit.view", "审计日志", "audit", SCOPE_SYSTEM,
        (ROLE_TEACHER,), (ROLE_TEACHER,), locked=True,
    ),
    PermissionSpec(
        "course.view", "课程查看", "course", SCOPE_CLASS,
        _ALL_ROLES, _ALL_ROLES, (ROLE_STUDENT,),
    ),
    PermissionSpec(
        "course.manage", "课程维护", "course", SCOPE_CLASS,
        _TEACHER_ADMIN, _TEACHER_ADMIN, (ROLE_ADMIN,),
    ),
    PermissionSpec(
        "incident.view", "异常查看", "incident", SCOPE_CLASS,
        _ALL_ROLES, _ALL_ROLES, (ROLE_STUDENT,),
    ),
    PermissionSpec(
        "incident.create", "异常上报", "incident", SCOPE_CLASS,
        _ALL_ROLES, _ALL_ROLES, locked=True,
    ),
    PermissionSpec(
        "incident.manage", "异常处理", "incident", SCOPE_CLASS,
        _TEACHER_ADMIN, _TEACHER_ADMIN, (ROLE_ADMIN,),
    ),
    PermissionSpec(
        "handover.view", "交接查看", "handover", SCOPE_CLASS,
        _TEACHER_ADMIN, _TEACHER_ADMIN, (ROLE_ADMIN,),
    ),
    PermissionSpec(
        "handover.manage", "交接维护", "handover", SCOPE_CLASS,
        _TEACHER_ADMIN, _TEACHER_ADMIN, (ROLE_ADMIN,),
    ),
    PermissionSpec(
        "report.view", "报表查看", "report", SCOPE_CLASS,
        _TEACHER_ADMIN, _TEACHER_ADMIN, (ROLE_ADMIN,),
    ),
    PermissionSpec(
        "profile.view", "个人中心", "profile", SCOPE_PERSONAL,
        _ALL_ROLES, _ALL_ROLES, locked=True,
    ),
    PermissionSpec(
        "profile.edit", "个人信息", "profile", SCOPE_PERSONAL,
        _ALL_ROLES, _ALL_ROLES, locked=True,
    ),
    PermissionSpec(
        "assistant.view", "助手入口", "assistant", SCOPE_PERSONAL,
        _ALL_ROLES, _ALL_ROLES,
    ),
    PermissionSpec(
        "settings.manage", "系统设置", "settings", SCOPE_SYSTEM,
        (ROLE_TEACHER,), (ROLE_TEACHER,), locked=True,
    ),
    PermissionSpec(
        "account.view", "账号查看", "account", SCOPE_CLASS,
        _TEACHER_ADMIN, _TEACHER_ADMIN, (ROLE_ADMIN,),
    ),
    PermissionSpec(
        "account.manage", "账号管理", "account", SCOPE_CLASS,
        (ROLE_TEACHER,), (ROLE_TEACHER,),
    ),
    PermissionSpec(
        "permission.view", "权限查看", "permission", SCOPE_CLASS,
        (ROLE_TEACHER,), (ROLE_TEACHER,),
    ),
    PermissionSpec(
        "permission.manage", "权限管理", "permission", SCOPE_CLASS,
        (ROLE_TEACHER,), (ROLE_TEACHER,),
    ),
    PermissionSpec(
        "llm.manage", "LLM 配置", "llm", SCOPE_SYSTEM,
        (), (), locked=True,
    ),
)

PERMISSION_MAP: Final[dict[str, PermissionSpec]] = {s.key: s for s in CATALOG}


@dataclass(frozen=True)
class RolePermissionState:
    allowed: bool
    default_enabled: bool
    toggleable_by_teacher: bool
    locked: bool


class PermissionUpdateError(ValueError):
    """班级覆盖更新被拒绝；router 转为 4xx。"""

    def __init__(self, detail: str, *, forbidden: bool = False) -> None:
        super().__init__(detail)
        self.detail = detail
        self.forbidden = forbidden


def spec_allowed(spec: PermissionSpec, role: str) -> bool:
    return role in spec.allowed_roles


def spec_default(spec: PermissionSpec, role: str) -> bool:
    return spec_allowed(spec, role) and role in spec.default_roles


def spec_toggleable(spec: PermissionSpec, role: str) -> bool:
    return (
        not spec.locked
        and spec.scope == SCOPE_CLASS
        and role in spec.toggleable_roles
        and spec_allowed(spec, role)
    )


def _state_from_spec(spec: PermissionSpec, role: str) -> RolePermissionState:
    return RolePermissionState(
        allowed=spec_allowed(spec, role),
        default_enabled=spec_default(spec, role),
        toggleable_by_teacher=spec_toggleable(spec, role),
        locked=spec.locked,
    )


def _state_from_row(row: RolePermission) -> RolePermissionState:
    return RolePermissionState(
        allowed=bool(row.allowed),
        default_enabled=bool(row.default_enabled) and bool(row.allowed),
        toggleable_by_teacher=(
            bool(row.toggleable_by_teacher) and not bool(row.locked)
        ),
        locked=bool(row.locked),
    )


def role_states(db: Session, role: str) -> dict[str, RolePermissionState]:
    """角色矩阵；数据库缺行时回落到代码目录，保证幂等与向后兼容。"""
    rows = {
        row.permission_key: row
        for row in db.scalars(
            select(RolePermission).where(RolePermission.role == role)
        ).all()
    }
    states: dict[str, RolePermissionState] = {}
    for spec in CATALOG:
        row = rows.get(spec.key)
        states[spec.key] = (
            _state_from_row(row) if row is not None
            else _state_from_spec(spec, role)
        )
    return states


def role_state(db: Session, role: str, key: str) -> RolePermissionState | None:
    spec = PERMISSION_MAP.get(key)
    if spec is None:
        return None
    row = db.scalars(
        select(RolePermission).where(
            RolePermission.role == role,
            RolePermission.permission_key == key,
        )
    ).first()
    if row is not None:
        return _state_from_row(row)
    return _state_from_spec(spec, role)


def class_overrides(db: Session, class_id: int, role: str) -> dict[str, bool]:
    return {
        row.permission_key: bool(row.enabled)
        for row in db.scalars(
            select(ClassRolePermission).where(
                ClassRolePermission.class_id == class_id,
                ClassRolePermission.role == role,
            )
        ).all()
    }


def effective_permissions(db: Session, role: str, class_id: int) -> list[str]:
    """按 active_role 计算有效权限键列表；不做多角色并集。"""
    if role == ROLE_SUPER_ADMIN:
        return [spec.key for spec in CATALOG]
    states = role_states(db, role)
    overrides = class_overrides(db, class_id, role)
    result: list[str] = []
    for spec in CATALOG:
        state = states[spec.key]
        if not state.allowed:
            continue
        enabled = state.default_enabled
        if spec.scope == SCOPE_CLASS and not state.locked:
            enabled = overrides.get(spec.key, enabled)
        if enabled:
            result.append(spec.key)
    return result


def effective_permission_set(db: Session, role: str, class_id: int) -> set[str]:
    return set(effective_permissions(db, role, class_id))


def role_matrix(db: Session, class_id: int, role: str) -> list[dict]:
    states = role_states(db, role)
    overrides = class_overrides(db, class_id, role)
    entries: list[dict] = []
    for spec in CATALOG:
        state = states[spec.key]
        overridable = spec.scope == SCOPE_CLASS and state.toggleable_by_teacher
        overridden = overridable and spec.key in overrides
        enabled = state.default_enabled
        if spec.scope == SCOPE_CLASS and not state.locked:
            enabled = overrides.get(spec.key, enabled)
        entries.append(
            {
                "key": spec.key,
                "label": spec.label,
                "category": spec.category,
                "category_label": CATEGORY_LABELS.get(spec.category, spec.category),
                "scope": spec.scope,
                "allowed": state.allowed,
                "default_enabled": state.default_enabled,
                "effective_enabled": state.allowed and enabled,
                "toggleable_by_teacher": state.toggleable_by_teacher,
                "locked": state.locked,
                "overridden": overridden,
            }
        )
    return entries


def ensure_catalog_seeded(db: Session) -> None:
    """幂等补齐权限目录与角色矩阵，使代码目录与数据库同步。"""
    existing_permissions = set(db.scalars(select(Permission.key)).all())
    for index, spec in enumerate(CATALOG):
        if spec.key not in existing_permissions:
            db.add(
                Permission(
                    key=spec.key,
                    label=spec.label,
                    category=spec.category,
                    scope=spec.scope,
                    sort_order=index,
                )
            )
            existing_permissions.add(spec.key)
    existing_rows = {
        (row.role, row.permission_key)
        for row in db.scalars(select(RolePermission)).all()
    }
    for spec in CATALOG:
        for role in MATRIX_ROLES:
            if (role, spec.key) in existing_rows:
                continue
            db.add(
                RolePermission(
                    role=role,
                    permission_key=spec.key,
                    allowed=spec_allowed(spec, role),
                    default_enabled=spec_default(spec, role),
                    toggleable_by_teacher=spec_toggleable(spec, role),
                    locked=spec.locked,
                )
            )
    db.flush()


def _new_override(class_id: int, role: str, key: str) -> ClassRolePermission:
    return ClassRolePermission(
        class_id=class_id,
        role=role,
        permission_key=key,
        enabled=False,
        updated_by=None,
        updated_at=business_now(),
    )


def update_class_overrides(
    db: Session,
    class_id: int,
    role: str,
    updates: dict[str, bool],
    updated_by: int | None,
) -> list[str]:
    """教师更新本班低角色覆盖；越权、上限外、锁定项一律拒绝。"""
    if role not in OVERRIDE_ROLES:
        raise PermissionUpdateError("只能调整本班学生或班委的权限", forbidden=True)
    if not updates:
        raise PermissionUpdateError("没有需要更新的权限项")
    for key in updates:
        state = role_state(db, role, key)
        spec = PERMISSION_MAP.get(key)
        if state is None or spec is None:
            raise PermissionUpdateError(f"未知权限项：{key}")
        if not state.allowed:
            raise PermissionUpdateError(f"权限项 {key} 超出 {role} 的角色上限", forbidden=True)
        if state.locked or not state.toggleable_by_teacher:
            raise PermissionUpdateError(f"权限项 {key} 为锁定项，教师不可调整", forbidden=True)
        if spec.scope != SCOPE_CLASS:
            raise PermissionUpdateError(f"权限项 {key} 不支持班级覆盖", forbidden=True)

    existing = {
        row.permission_key: row
        for row in db.scalars(
            select(ClassRolePermission).where(
                ClassRolePermission.class_id == class_id,
                ClassRolePermission.role == role,
            )
        ).all()
    }
    changed: list[str] = []
    timestamp = business_now()
    for key, value in updates.items():
        row = existing.get(key)
        if row is None:
            row = _new_override(class_id, role, key)
            db.add(row)
        row.enabled = bool(value)
        row.updated_by = updated_by
        row.updated_at = timestamp
        changed.append(key)
    db.flush()
    return changed


def permission_rows_for_seed() -> list[dict]:
    return [
        {
            "key": spec.key,
            "label": spec.label,
            "category": spec.category,
            "scope": spec.scope,
            "sort_order": index,
        }
        for index, spec in enumerate(CATALOG)
    ]


def role_rows_for_seed() -> list[dict]:
    return [
        {
            "role": role,
            "permission_key": spec.key,
            "allowed": spec_allowed(spec, role),
            "default_enabled": spec_default(spec, role),
            "toggleable_by_teacher": spec_toggleable(spec, role),
            "locked": spec.locked,
        }
        for spec in CATALOG
        for role in MATRIX_ROLES
    ]
