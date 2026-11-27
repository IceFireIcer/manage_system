from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import OperationLog, User
from app.security import hash_password

BootstrapState = Literal["ready", "needs_initialization", "needs_recovery"]

BOOTSTRAP_MESSAGES: dict[str, str] = {
    "ready": "管理员账号已就绪",
    "needs_initialization": "系统需初始化：当前没有任何账号，请由校方授权人完成初始化",
    "needs_recovery": "系统需恢复：存在账号但没有可用管理员，请由校方授权人执行管理员恢复",
}

MIN_PASSWORD_LENGTH = 6
OPERATOR_ROLE = "operator"
DEFAULT_ACTOR = "校方授权人"
DEFAULT_ADMIN_NAME = "系统管理员"
DEFAULT_CLASS_ID = 1
ROLE_NAMES = {"teacher": "教师", "admin": "班委", "student": "学生"}


class ProvisioningError(Exception):
    pass


@dataclass(frozen=True)
class AccountResult:
    user_id: int
    account: str
    name: str
    role: str
    password: str
    generated: bool
    action: str


@dataclass(frozen=True)
class HandoverResult:
    previous_account: str
    previous_user_id: int
    new_admin: AccountResult


def bootstrap_state(db: Session) -> BootstrapState:
    enabled_admin = db.scalar(
        select(User.id).where(User.role == "admin", User.status == "enabled").limit(1)
    )
    if enabled_admin is not None:
        return "ready"
    if db.scalar(select(User.id).limit(1)) is None:
        return "needs_initialization"
    return "needs_recovery"


def enabled_admin_count(db: Session) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.status == "enabled")
        )
        or 0
    )


def resolve_actor(actor: str | None = None) -> str:
    candidate = (actor or os.getenv("OPS_ACTOR") or "").strip()
    return candidate or DEFAULT_ACTOR


def _validate_account(account: str) -> str:
    value = account.strip()
    if not value:
        raise ProvisioningError("账号不能为空")
    if len(value) > 64:
        raise ProvisioningError("账号长度不能超过 64 位")
    return value


def _validate_name(name: str) -> str:
    value = (name or "").strip()
    if not value:
        raise ProvisioningError("姓名不能为空")
    if len(value) > 64:
        raise ProvisioningError("姓名长度不能超过 64 位")
    return value


def _password_for(password: str | None) -> tuple[str, bool]:
    if password:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ProvisioningError(f"初始密码至少 {MIN_PASSWORD_LENGTH} 位")
        return password, False
    return secrets.token_urlsafe(12), True


def _snapshot(user: User, **extra: object) -> str:
    payload: dict[str, object] = {
        "id": user.id,
        "account": user.account,
        "name": user.name,
        "role": user.role,
        "status": user.status,
        "session_version": user.session_version,
    }
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _write_log(
    db: Session,
    *,
    action: str,
    target_id: int | None,
    before_value: str | None,
    after_value: str,
    remark: str,
) -> None:
    db.add(
        OperationLog(
            operator_id=None,
            role=OPERATOR_ROLE,
            action=action,
            target_type="user",
            target_id=target_id,
            before_value=before_value,
            after_value=after_value,
            remark=remark[:255],
        )
    )


def _reject(
    db: Session,
    *,
    action: str,
    actor: str,
    reason: str,
    target: User | None = None,
) -> None:
    _write_log(
        db,
        action=action,
        target_id=target.id if target is not None else None,
        before_value=_snapshot(target) if target is not None else None,
        after_value=json.dumps(
            {"result": "rejected", "reason": reason, "actor": actor},
            ensure_ascii=False,
        ),
        remark=f"{actor} 操作被拒绝：{reason}",
    )
    db.commit()
    raise ProvisioningError(reason)


def _create_account(
    db: Session,
    *,
    account: str,
    name: str,
    role: str,
    password: str | None,
    actor: str,
    action: str,
    reject_action: str,
    class_id: int = DEFAULT_CLASS_ID,
) -> AccountResult:
    if role not in ROLE_NAMES:
        raise ProvisioningError(f"不支持的账号角色：{role}")
    account_value = _validate_account(account)
    name_value = _validate_name(name)
    existing = db.scalars(select(User).where(User.account == account_value)).first()
    if existing is not None:
        _reject(
            db,
            action=reject_action,
            actor=actor,
            reason=(
                f"账号 {account_value} 已存在（角色 {existing.role}），"
                "拒绝覆盖或提升权限"
            ),
            target=existing,
        )
    secret, generated = _password_for(password)
    user = User(
        account=account_value,
        password_hash=hash_password(secret),
        name=name_value,
        role=role,
        status="enabled",
        class_id=class_id,
        must_change_password=True,
        session_version=0,
    )
    db.add(user)
    db.flush()
    _write_log(
        db,
        action=action,
        target_id=user.id,
        before_value=None,
        after_value=_snapshot(user, result="success", actor=actor),
        remark=(
            f"{actor} 开通{ROLE_NAMES[role]}账号 {account_value}（{name_value}），"
            "首次登录必须修改密码"
        ),
    )
    db.commit()
    db.refresh(user)
    return AccountResult(
        user_id=user.id,
        account=user.account,
        name=user.name,
        role=user.role,
        password=secret,
        generated=generated,
        action=action,
    )


def bootstrap_initial_admin(
    db: Session,
    *,
    account: str,
    name: str,
    password: str | None = None,
    actor: str | None = None,
    class_id: int = DEFAULT_CLASS_ID,
) -> AccountResult | None:
    state = bootstrap_state(db)
    if state == "ready":
        return None
    resolved_actor = resolve_actor(actor)
    if state == "needs_recovery":
        raise ProvisioningError(BOOTSTRAP_MESSAGES["needs_recovery"])
    return _create_account(
        db,
        account=account,
        name=name,
        role="admin",
        password=password,
        actor=resolved_actor,
        action="admin_bootstrap",
        reject_action="admin_bootstrap_rejected",
        class_id=class_id,
    )


def provision_teacher(
    db: Session,
    *,
    account: str,
    name: str,
    password: str | None = None,
    actor: str | None = None,
    class_id: int = DEFAULT_CLASS_ID,
) -> AccountResult:
    return _create_account(
        db,
        account=account,
        name=name,
        role="teacher",
        password=password,
        actor=resolve_actor(actor),
        action="teacher_provision",
        reject_action="teacher_provision_rejected",
        class_id=class_id,
    )


def handover_admin(
    db: Session,
    *,
    previous_account: str,
    account: str,
    name: str,
    password: str | None = None,
    actor: str | None = None,
    class_id: int = DEFAULT_CLASS_ID,
) -> HandoverResult:
    resolved_actor = resolve_actor(actor)
    previous_value = previous_account.strip()
    previous = db.scalars(select(User).where(User.account == previous_value)).first()
    reject_action = "admin_handover_rejected"
    if previous is None or previous.role != "admin":
        _reject(
            db,
            action=reject_action,
            actor=resolved_actor,
            reason="仅允许交接班委管理员账号，教师或学生不能通过交接提升权限",
            target=previous,
        )
    assert previous is not None
    if previous.status != "enabled":
        _reject(
            db,
            action=reject_action,
            actor=resolved_actor,
            reason="原班委账号未启用，请改用管理员恢复",
            target=previous,
        )
    account_value = _validate_account(account)
    if account_value == previous.account:
        _reject(
            db,
            action=reject_action,
            actor=resolved_actor,
            reason="交接后的班委账号不能与原账号相同",
            target=previous,
        )
    existing = db.scalars(select(User).where(User.account == account_value)).first()
    if existing is not None:
        _reject(
            db,
            action=reject_action,
            actor=resolved_actor,
            reason="交接后的班委账号已存在，请更换新账号",
            target=existing,
        )

    name_value = _validate_name(name)
    secret, generated = _password_for(password)
    new_admin = User(
        account=account_value,
        password_hash=hash_password(secret),
        name=name_value,
        role="admin",
        status="enabled",
        class_id=class_id,
        must_change_password=True,
        session_version=0,
    )
    db.add(new_admin)
    db.flush()
    before = _snapshot(previous)
    previous.status = "disabled"
    previous.session_version += 1
    _write_log(
        db,
        action="admin_handover",
        target_id=previous.id,
        before_value=before,
        after_value=json.dumps(
            {
                "previous_account": previous.account,
                "previous_status": previous.status,
                "new_account": new_admin.account,
                "new_name": new_admin.name,
                "result": "success",
                "actor": resolved_actor,
            },
            ensure_ascii=False,
        ),
        remark=(
            f"{resolved_actor} 班委交接 {previous.account} → {account_value}，"
            "原账号停用、新账号首次登录必须修改密码"
        ),
    )
    _write_log(
        db,
        action="admin_handover_create",
        target_id=new_admin.id,
        before_value=None,
        after_value=_snapshot(new_admin, result="success", actor=resolved_actor),
        remark=f"{resolved_actor} 开通班委账号 {account_value}（{name_value}）",
    )
    db.commit()
    db.refresh(new_admin)
    db.refresh(previous)
    return HandoverResult(
        previous_account=previous.account,
        previous_user_id=previous.id,
        new_admin=AccountResult(
            user_id=new_admin.id,
            account=new_admin.account,
            name=new_admin.name,
            role=new_admin.role,
            password=secret,
            generated=generated,
            action="admin_handover_create",
        ),
    )


def recover_admin(
    db: Session,
    *,
    account: str,
    name: str | None = None,
    password: str | None = None,
    actor: str | None = None,
    class_id: int = DEFAULT_CLASS_ID,
) -> AccountResult:
    resolved_actor = resolve_actor(actor)
    account_value = _validate_account(account)
    existing = db.scalars(select(User).where(User.account == account_value)).first()
    reject_action = "admin_recover_rejected"

    if existing is None:
        if bootstrap_state(db) == "ready":
            _reject(
                db,
                action=reject_action,
                actor=resolved_actor,
                reason="系统已有可用管理员，新增管理员请使用班委交接",
            )
        return _create_account(
            db,
            account=account_value,
            name=name or DEFAULT_ADMIN_NAME,
            role="admin",
            password=password,
            actor=resolved_actor,
            action="admin_recover",
            reject_action=reject_action,
            class_id=class_id,
        )

    if existing.role != "admin":
        _reject(
            db,
            action=reject_action,
            actor=resolved_actor,
            reason="仅允许恢复管理员账号，不能把教师或学生提升为管理员",
            target=existing,
        )

    before = _snapshot(existing)
    secret, generated = _password_for(password)
    existing.password_hash = hash_password(secret)
    existing.name = _validate_name(name) if name else existing.name
    existing.status = "enabled"
    existing.must_change_password = True
    existing.session_version += 1
    _write_log(
        db,
        action="admin_recover",
        target_id=existing.id,
        before_value=before,
        after_value=_snapshot(existing, result="success", actor=resolved_actor),
        remark=(
            f"{resolved_actor} 恢复管理员账号 {existing.account}，"
            "旧会话失效且首次登录必须修改密码"
        ),
    )
    db.commit()
    db.refresh(existing)
    return AccountResult(
        user_id=existing.id,
        account=existing.account,
        name=existing.name,
        role=existing.role,
        password=secret,
        generated=generated,
        action="admin_recover",
    )
