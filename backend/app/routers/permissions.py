import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.class_scope import class_display_name
from app.database import get_db
from app.deps import (
    CurrentClassId,
    CurrentUser,
    effective_role,
    require_permission,
    require_super_admin,
)
from app.models import User
from app.operation_log import record_operation, user_label
from app.permissions import (
    ACTION_OVERRIDE_UPDATE,
    CATALOG,
    CATEGORY_LABELS,
    MATRIX_ROLES,
    OVERRIDE_ROLES,
    PermissionUpdateError,
    class_overrides,
    effective_permissions,
    ensure_catalog_seeded,
    role_matrix,
    role_state,
    update_class_overrides,
    update_role_permission,
)
from app.roles import ROLE_LABELS
from app.schemas_permissions import (
    ClassOverrideUpdateIn,
    ClassOverrideUpdateOut,
    PermissionCatalogItemOut,
    PermissionCatalogOut,
    PermissionEntryOut,
    PermissionListOut,
    PermissionMatrixOut,
    RoleMatrixOut,
    RolePermissionUpdateIn,
)

router = APIRouter(prefix="/api/v1/permissions", tags=["permissions"])

TeacherUser = Annotated[User, Depends(require_permission("permission.view"))]
SuperAdminUser = Annotated[User, Depends(require_super_admin)]
PermissionManager = Annotated[User, Depends(require_permission("permission.manage"))]
UNKNOWN_ROLE_DETAIL = "只能查看班委或学生的班级覆盖"


def _state_snapshot(state) -> str | None:
    if state is None:
        return None
    return json.dumps(
        {
            "allowed": state.allowed,
            "default_enabled": state.default_enabled,
            "toggleable_by_teacher": state.toggleable_by_teacher,
            "locked": state.locked,
        },
        ensure_ascii=False,
    )


def _role_matrix_out(db: Session, class_id: int, role: str) -> RoleMatrixOut:
    return RoleMatrixOut(
        role=role,
        label=ROLE_LABELS.get(role, role),
        entries=[PermissionEntryOut(**entry) for entry in role_matrix(db, class_id, role)],
    )


def _matrix_out(db: Session, class_id: int, active_role: str) -> PermissionMatrixOut:
    return PermissionMatrixOut(
        class_id=class_id,
        active_role=active_role,
        roles=[_role_matrix_out(db, class_id, role) for role in MATRIX_ROLES],
    )


@router.get("/me")
def my_permissions(
    current_user: CurrentUser,
    class_id: CurrentClassId,
    db: Annotated[Session, Depends(get_db)],
) -> PermissionListOut:
    role = effective_role(current_user)
    return PermissionListOut(permissions=effective_permissions(db, role, class_id))


@router.get("/catalog")
def permission_catalog(
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> PermissionCatalogOut:
    return PermissionCatalogOut(
        items=[
            PermissionCatalogItemOut(
                key=spec.key,
                label=spec.label,
                category=spec.category,
                category_label=CATEGORY_LABELS.get(spec.category, spec.category),
                scope=spec.scope,
                sort_order=index,
            )
            for index, spec in enumerate(CATALOG)
        ]
    )


@router.get("/matrix")
def permission_matrix(
    teacher: TeacherUser,
    class_id: CurrentClassId,
    db: Annotated[Session, Depends(get_db)],
) -> PermissionMatrixOut:
    return _matrix_out(db, class_id, effective_role(teacher))


@router.get("/overrides")
def read_class_overrides(
    teacher: TeacherUser,
    class_id: CurrentClassId,
    db: Annotated[Session, Depends(get_db)],
    role: Annotated[str, Query(min_length=1, max_length=16)],
) -> RoleMatrixOut:
    if role not in OVERRIDE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=UNKNOWN_ROLE_DETAIL
        )
    return _role_matrix_out(db, class_id, role)


@router.put("/overrides")
def write_class_overrides(
    payload: ClassOverrideUpdateIn,
    manager: PermissionManager,
    class_id: CurrentClassId,
    db: Annotated[Session, Depends(get_db)],
) -> ClassOverrideUpdateOut:
    before = class_overrides(db, class_id, payload.role)
    ensure_catalog_seeded(db)
    try:
        updated = update_class_overrides(
            db,
            class_id,
            payload.role,
            payload.updates,
            manager.id,
        )
    except PermissionUpdateError as exc:
        db.rollback()
        raise HTTPException(
            status_code=(
                status.HTTP_403_FORBIDDEN if exc.forbidden else status.HTTP_400_BAD_REQUEST
            ),
            detail=exc.detail,
        ) from exc

    record_operation(
        db,
        action=ACTION_OVERRIDE_UPDATE,
        operator=manager,
        class_id=class_id,
        target_type="class",
        target_id=class_id,
        target_label=class_display_name(db, class_id),
        before_value=json.dumps(
            {key: before.get(key) for key in updated}, ensure_ascii=False
        ),
        after_value=json.dumps(
            {"role": payload.role, "updates": payload.updates}, ensure_ascii=False
        ),
        remark=(
            f"{user_label(manager)} 调整班级 {class_id} 的 {payload.role} 权限覆盖"
        ),
    )
    db.commit()
    return ClassOverrideUpdateOut(
        class_id=class_id,
        role=payload.role,
        updated=updated,
        matrix=_role_matrix_out(db, class_id, payload.role),
    )

@router.put("/roles")
def update_role_matrix(
    payload: RolePermissionUpdateIn,
    manager: SuperAdminUser,
    class_id: CurrentClassId,
    db: Annotated[Session, Depends(get_db)],
) -> RoleMatrixOut:
    before = role_state(db, payload.role, payload.permission_key)
    ensure_catalog_seeded(db)
    try:
        state = update_role_permission(
            db,
            payload.role,
            payload.permission_key,
            allowed=payload.allowed,
            default_enabled=payload.default_enabled,
            toggleable_by_teacher=payload.toggleable_by_teacher,
            locked=payload.locked,
        )
    except PermissionUpdateError as exc:
        db.rollback()
        raise HTTPException(
            status_code=(
                status.HTTP_403_FORBIDDEN if exc.forbidden else status.HTTP_400_BAD_REQUEST
            ),
            detail=exc.detail,
        ) from exc

    record_operation(
        db,
        action="permission_role_update",
        operator=manager,
        target_type="role",
        target_label=payload.role,
        before_value=_state_snapshot(before),
        after_value=_state_snapshot(state),
        remark=(
            f"{user_label(manager)} 调整 {payload.role} 的 {payload.permission_key} 角色权限"
        ),
    )
    db.commit()
    return _role_matrix_out(db, class_id, payload.role)
