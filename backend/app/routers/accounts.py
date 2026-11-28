import json
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.class_scope import class_display_name
from app.database import get_db
from app.deps import CurrentClassId, effective_role, require_permission
from app.models import Class, User, UserRole
from app.operation_log import record_operation, user_label
from app.roles import (
    ALL_ROLES,
    ROLE_ADMIN,
    ROLE_STUDENT,
    ROLE_SUPER_ADMIN,
    ROLE_TEACHER,
    ensure_grants,
    granted_roles,
)
from app.schemas_accounts import (
    AccountClassIn,
    AccountCreateIn,
    AccountItemOut,
    AccountPasswordResetOut,
    AccountRolesIn,
    AccountStatusIn,
    AccountUpdateIn,
)
from app.security import hash_password

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])

AccountManager = Annotated[User, Depends(require_permission("account.manage"))]
ALLOWED_STATUSES = ("enabled", "disabled")
TEACHER_MANAGEABLE_ROLES = (ROLE_ADMIN, ROLE_STUDENT)
ROLE_PRIORITY = (ROLE_SUPER_ADMIN, ROLE_TEACHER, ROLE_ADMIN, ROLE_STUDENT)


def _normalize_account(value: str) -> str:
    account = value.strip()
    if not account:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="账号不能为空")
    if len(account) > 64:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="账号不能超过 64 个字符"
        )
    if any(ch.isspace() for ch in account):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="账号不能包含空白字符"
        )
    return account


def _normalize_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="姓名不能为空")
    if len(name) > 64:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="姓名不能超过 64 个字符"
        )
    return name


def _primary_role(roles: set[str]) -> str:
    for role in ROLE_PRIORITY:
        if role in roles:
            return role
    return ROLE_STUDENT


def _account_item(db: Session, user: User) -> AccountItemOut:
    return AccountItemOut(
        id=user.id,
        account=user.account,
        name=user.name,
        role=user.role,
        status=user.status,
        class_id=user.class_id,
        class_name=class_display_name(db, user.class_id),
        roles=granted_roles(db, user),
        must_change_password=bool(user.must_change_password),
        created_at=user.created_at,
    )


def _snapshot(item: AccountItemOut) -> str:
    return json.dumps(item.model_dump(mode="json"), ensure_ascii=False)


def _get_manageable(
    db: Session, actor_role: str, actor_class_id: int, user_id: int
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在"
        )
    if actor_role == ROLE_SUPER_ADMIN:
        return user
    if actor_role == ROLE_TEACHER:
        if user.class_id != actor_class_id or user.role not in TEACHER_MANAGEABLE_ROLES:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在"
            )
        return user
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权限访问")


@router.get("")
def list_accounts(
    manager: AccountManager,
    class_id: CurrentClassId,
    db: Annotated[Session, Depends(get_db)],
    role: str | None = None,
    account_status: Annotated[str | None, Query(alias="status")] = None,
    keyword: str | None = None,
) -> list[AccountItemOut]:
    actor_role = effective_role(manager)
    statement = select(User).order_by(User.id)
    if actor_role != ROLE_SUPER_ADMIN:
        statement = statement.where(
            User.class_id == class_id, User.role.in_(TEACHER_MANAGEABLE_ROLES)
        )
    if role is not None:
        statement = statement.where(User.role == role)
    if account_status is not None:
        statement = statement.where(User.status == account_status)
    users = list(db.scalars(statement).all())
    if keyword:
        needle = keyword.strip().lower()
        if needle:
            users = [
                user
                for user in users
                if needle in user.account.lower() or needle in user.name.lower()
            ]
    return [_account_item(db, user) for user in users]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_account(
    payload: AccountCreateIn,
    manager: AccountManager,
    class_id: CurrentClassId,
    db: Annotated[Session, Depends(get_db)],
) -> AccountItemOut:
    actor_role = effective_role(manager)
    target_role = payload.role
    if target_role not in ALL_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的角色"
        )
    if actor_role == ROLE_TEACHER and target_role not in TEACHER_MANAGEABLE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="教师只能创建本班学生或班委账号",
        )
    if actor_role not in (ROLE_SUPER_ADMIN, ROLE_TEACHER):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权限访问")

    if actor_role == ROLE_TEACHER:
        target_class_id = class_id
    else:
        target_class_id = payload.class_id if payload.class_id is not None else class_id
        if db.get(Class, target_class_id) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="班级不存在"
            )

    account = _normalize_account(payload.account)
    name = _normalize_name(payload.name)
    existing = db.scalars(select(User).where(User.account == account)).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"账号 {account} 已存在"
        )

    user = User(
        account=account,
        password_hash=hash_password(payload.password),
        name=name,
        role=target_role,
        class_id=target_class_id,
        status="enabled",
        must_change_password=True,
    )
    db.add(user)
    db.flush()
    ensure_grants(db, user)
    item = _account_item(db, user)
    record_operation(
        db,
        action="account_create",
        operator=manager,
        class_id=target_class_id,
        target_type="user",
        target_id=user.id,
        target_label=user_label(user),
        after_value=_snapshot(item),
        remark=f"创建账号 {user_label(user)}（{target_role}）",
    )
    db.commit()
    db.refresh(user)
    return _account_item(db, user)


@router.patch("/{user_id}")
def update_account(
    user_id: int,
    payload: AccountUpdateIn,
    manager: AccountManager,
    class_id: CurrentClassId,
    db: Annotated[Session, Depends(get_db)],
) -> AccountItemOut:
    actor_role = effective_role(manager)
    user = _get_manageable(db, actor_role, class_id, user_id)
    before = _account_item(db, user)
    changed = False
    if payload.account is not None:
        account = _normalize_account(payload.account)
        if account != user.account:
            conflict = db.scalars(
                select(User).where(User.account == account, User.id != user.id)
            ).first()
            if conflict is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"账号 {account} 已被使用",
                )
            user.account = account
            changed = True
    if payload.name is not None:
        name = _normalize_name(payload.name)
        if name != user.name:
            user.name = name
            changed = True
    if payload.class_id is not None and payload.class_id != user.class_id:
        if actor_role == ROLE_TEACHER and payload.class_id != class_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="只能将账号分配到本班"
            )
        if db.get(Class, payload.class_id) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="班级不存在"
            )
        user.class_id = payload.class_id
        user.session_version += 1
        changed = True
    if not changed:
        return before
    db.flush()
    after = _account_item(db, user)
    record_operation(
        db,
        action="account_update",
        operator=manager,
        class_id=user.class_id,
        target_type="user",
        target_id=user.id,
        target_label=user_label(user),
        before_value=_snapshot(before),
        after_value=_snapshot(after),
        remark=f"更新账号 {user_label(user)}",
    )
    db.commit()
    db.refresh(user)
    return _account_item(db, user)


@router.patch("/{user_id}/status")
def update_account_status(
    user_id: int,
    payload: AccountStatusIn,
    manager: AccountManager,
    class_id: CurrentClassId,
    db: Annotated[Session, Depends(get_db)],
) -> AccountItemOut:
    if payload.status not in ALLOWED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="状态仅允许 enabled 或 disabled",
        )
    actor_role = effective_role(manager)
    user = _get_manageable(db, actor_role, class_id, user_id)
    if user.id == manager.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="不能修改自己的账号状态"
        )
    if user.status != payload.status:
        before = _account_item(db, user)
        user.status = payload.status
        user.session_version += 1
        db.flush()
        record_operation(
            db,
            action="account_status",
            operator=manager,
            class_id=user.class_id,
            target_type="user",
            target_id=user.id,
            target_label=user_label(user),
            before_value=json.dumps({"status": before.status}, ensure_ascii=False),
            after_value=json.dumps({"status": user.status}, ensure_ascii=False),
            remark=f"{'启用' if user.status == 'enabled' else '停用'}账号 {user_label(user)}",
        )
        db.commit()
    db.refresh(user)
    return _account_item(db, user)


@router.post("/{user_id}/reset-password")
def reset_account_password(
    user_id: int,
    manager: AccountManager,
    class_id: CurrentClassId,
    db: Annotated[Session, Depends(get_db)],
) -> AccountPasswordResetOut:
    actor_role = effective_role(manager)
    user = _get_manageable(db, actor_role, class_id, user_id)
    if user.id == manager.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="不能重置自己的密码"
        )
    temporary_password = secrets.token_urlsafe(9)
    user.password_hash = hash_password(temporary_password)
    user.must_change_password = True
    user.session_version += 1
    db.flush()
    record_operation(
        db,
        action="account_password_reset",
        operator=manager,
        class_id=user.class_id,
        target_type="user",
        target_id=user.id,
        target_label=user_label(user),
        after_value=json.dumps(
            {"must_change_password": True, "session_version": user.session_version},
            ensure_ascii=False,
        ),
        remark=f"重置账号 {user_label(user)} 的密码",
    )
    db.commit()
    db.refresh(user)
    return AccountPasswordResetOut(
        user=_account_item(db, user), temporary_password=temporary_password
    )


@router.put("/{user_id}/roles")
def set_account_roles(
    user_id: int,
    payload: AccountRolesIn,
    manager: AccountManager,
    class_id: CurrentClassId,
    db: Annotated[Session, Depends(get_db)],
) -> AccountItemOut:
    actor_role = effective_role(manager)
    user = _get_manageable(db, actor_role, class_id, user_id)
    desired = {role for role in payload.roles if role}
    if not desired:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="至少保留一个角色"
        )
    unknown = desired - set(ALL_ROLES)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的角色：{', '.join(sorted(unknown))}",
        )
    if actor_role == ROLE_TEACHER and not desired.issubset(TEACHER_MANAGEABLE_ROLES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="教师不能授予教师或超级管理员角色",
        )
    if user.id == manager.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="不能修改自己的角色"
        )

    before = _account_item(db, user)
    existing = {
        row.role
        for row in db.scalars(select(UserRole).where(UserRole.user_id == user.id)).all()
    }
    for role in desired - existing:
        db.add(UserRole(user_id=user.id, role=role))
    for role in existing - desired:
        row = db.scalars(
            select(UserRole).where(UserRole.user_id == user.id, UserRole.role == role)
        ).first()
        if row is not None:
            db.delete(row)
    if user.role not in desired:
        user.role = _primary_role(desired)
    user.session_version += 1
    db.flush()
    after = _account_item(db, user)
    record_operation(
        db,
        action="account_role_update",
        operator=manager,
        class_id=user.class_id,
        target_type="user",
        target_id=user.id,
        target_label=user_label(user),
        before_value=json.dumps({"roles": before.roles}, ensure_ascii=False),
        after_value=json.dumps({"roles": after.roles}, ensure_ascii=False),
        remark=f"授予/撤销 {user_label(user)} 的角色",
    )
    db.commit()
    db.refresh(user)
    return _account_item(db, user)


@router.patch("/{user_id}/class")
def assign_account_class(
    user_id: int,
    payload: AccountClassIn,
    manager: AccountManager,
    class_id: CurrentClassId,
    db: Annotated[Session, Depends(get_db)],
) -> AccountItemOut:
    actor_role = effective_role(manager)
    user = _get_manageable(db, actor_role, class_id, user_id)
    if actor_role == ROLE_TEACHER and payload.class_id != class_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="只能分配到本班"
        )
    if db.get(Class, payload.class_id) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="班级不存在"
        )
    if payload.class_id == user.class_id:
        return _account_item(db, user)
    before = _account_item(db, user)
    user.class_id = payload.class_id
    user.session_version += 1
    db.flush()
    after = _account_item(db, user)
    record_operation(
        db,
        action="account_class_update",
        operator=manager,
        class_id=after.class_id,
        target_type="user",
        target_id=user.id,
        target_label=user_label(user),
        before_value=json.dumps({"class_id": before.class_id}, ensure_ascii=False),
        after_value=json.dumps({"class_id": after.class_id}, ensure_ascii=False),
        remark=f"调整 {user_label(user)} 的主班",
    )
    db.commit()
    db.refresh(user)
    return _account_item(db, user)