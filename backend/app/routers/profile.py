import base64
import binascii
import json
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business_time import now as business_now
from app.class_scope import (
    CLASS_COMMITTEE_ROLES,
    ROLE_STUDENT,
    can_access_class,
    class_display_name,
)
from app.database import get_db
from app.deps import CurrentUser, require_permission
from app.models import User, UserProfile
from app.operation_log import record_operation, user_label

router = APIRouter(prefix="/api/v1/profile", tags=["profile"])

NAME_MAX_LENGTH = 64
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
AVATAR_DATA_URL_RE = re.compile(
    r"^data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/]+={0,2})$"
)
AVATAR_MAX_BYTES = 200 * 1024
PROFILE_EDITABLE_ROLES = (ROLE_STUDENT, *CLASS_COMMITTEE_ROLES)


class ProfileOut(BaseModel):
    id: int
    account: str
    name: str
    role: str
    class_id: int
    class_name: str
    employee_no: str | None = None
    student_no: str | None = None
    phone: str | None = None
    email: str | None = None
    wechat: str | None = None
    qq: str | None = None
    github: str | None = None
    avatar_base64: str | None = None


class ProfileUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = None
    phone: str | None = None
    email: str | None = None
    wechat: str | None = None
    qq: str | None = None
    github: str | None = None


class AdminProfileUpdate(ProfileUpdate):
    employee_no: str | None = None
    student_no: str | None = None


class AvatarUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    avatar_base64: str = ""


class AvatarOut(BaseModel):
    avatar_base64: str | None = None


def _clean(value: str | None) -> str:
    return value.strip() if isinstance(value, str) else ""


def _normalize_name(value: str) -> str:
    name = _clean(value)
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="姓名不能为空"
        )
    if len(name) > NAME_MAX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"姓名不能超过 {NAME_MAX_LENGTH} 个字符",
        )
    return name


def _normalize_phone(value: str | None) -> str:
    phone = _clean(value)
    if phone and not PHONE_RE.match(phone):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="手机号格式不正确"
        )
    return phone


def _normalize_email(value: str | None) -> str:
    email = _clean(value)
    if email and not EMAIL_RE.match(email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="邮箱格式不正确"
        )
    return email


def _validate_avatar(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    match = AVATAR_DATA_URL_RE.match(text)
    if match is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="头像需为 JPEG/PNG/WebP 图片",
        )
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="头像数据格式不正确"
        )
    if len(raw) > AVATAR_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="头像不能超过 200KB"
        )
    return text


def _get_or_create_profile(db: Session, user: User) -> UserProfile:
    profile = db.scalars(
        select(UserProfile).where(UserProfile.user_id == user.id)
    ).first()
    if profile is None:
        profile = UserProfile(user_id=user.id)
        db.add(profile)
        db.flush()
    return profile


def _to_out(db: Session, user: User, profile: UserProfile) -> ProfileOut:
    return ProfileOut(
        id=user.id,
        account=user.account,
        name=user.name,
        role=user.role,
        class_id=user.class_id,
        class_name=class_display_name(db, user.class_id),
        employee_no=profile.employee_no,
        student_no=profile.student_no,
        phone=profile.phone,
        email=profile.email,
        wechat=profile.wechat,
        qq=profile.qq,
        github=profile.github,
        avatar_base64=profile.avatar_base64,
    )


def _profile_snapshot(user: User, profile: UserProfile) -> str:
    return json.dumps(
        {
            "name": user.name,
            "employee_no": profile.employee_no,
            "student_no": profile.student_no,
            "phone": profile.phone,
            "email": profile.email,
            "wechat": profile.wechat,
            "qq": profile.qq,
            "github": profile.github,
            "has_avatar": bool(profile.avatar_base64),
        },
        ensure_ascii=False,
    )


def _ensure_unique_in_class(
    db: Session, user: User, field: str, value: str
) -> None:
    if not value:
        return
    column = UserProfile.student_no if field == "student_no" else UserProfile.employee_no
    stmt = (
        select(UserProfile.id)
        .join(User, User.id == UserProfile.user_id)
        .where(
            User.class_id == user.class_id,
            column == value,
            UserProfile.user_id != user.id,
        )
    )
    if db.scalars(stmt).first() is not None:
        label = "学号" if field == "student_no" else "工号"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{label} {value} 在本班已被使用",
        )


def _apply_common_fields(user: User, profile: UserProfile, payload: ProfileUpdate) -> bool:
    changed = False
    if payload.name is not None:
        name = _normalize_name(payload.name)
        if name != user.name:
            user.name = name
            changed = True
    for field, normalizer in (
        ("phone", _normalize_phone),
        ("email", _normalize_email),
    ):
        if getattr(payload, field) is not None:
            value = normalizer(getattr(payload, field))
            if value != (getattr(profile, field) or ""):
                setattr(profile, field, value)
                changed = True
    for field in ("wechat", "qq", "github"):
        if getattr(payload, field) is not None:
            value = _clean(getattr(payload, field))
            if value != (getattr(profile, field) or ""):
                setattr(profile, field, value)
                changed = True
    return changed


@router.get("")
def get_profile(
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> ProfileOut:
    profile = _get_or_create_profile(db, current_user)
    return _to_out(db, current_user, profile)


def update_profile_fields(
    db: Session,
    user: User,
    payload: ProfileUpdate,
    *,
    action: str = "profile_update",
    commit: bool = True,
) -> tuple[UserProfile, bool]:
    """应用本人资料更新并由调用方决定事务边界。"""
    profile = _get_or_create_profile(db, user)
    before = _profile_snapshot(user, profile)
    changed = _apply_common_fields(user, profile, payload)
    if changed:
        profile.updated_at = business_now()
        record_operation(
            db,
            action=action,
            operator=user,
            class_id=user.class_id,
            target_type="user",
            target_id=user.id,
            target_label=user_label(user),
            before_value=before,
            after_value=_profile_snapshot(user, profile),
        )
        if commit:
            db.commit()
            db.refresh(user)
            db.refresh(profile)
    return profile, changed


@router.patch("")
def update_profile(
    payload: ProfileUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> ProfileOut:
    profile, _changed = update_profile_fields(db, current_user, payload)
    return _to_out(db, current_user, profile)


@router.put("/avatar")
def update_avatar(
    payload: AvatarUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> AvatarOut:
    profile = _get_or_create_profile(db, current_user)
    value = _validate_avatar(payload.avatar_base64) or None
    before = json.dumps({"has_avatar": bool(profile.avatar_base64)})
    if value != profile.avatar_base64:
        profile.avatar_base64 = value
        profile.updated_at = business_now()
        record_operation(
            db,
            action="profile_avatar_update",
            operator=current_user,
            class_id=current_user.class_id,
            target_type="user",
            target_id=current_user.id,
            target_label=user_label(current_user),
            before_value=before,
            after_value=json.dumps({"has_avatar": bool(value)}),
        )
        db.commit()
        db.refresh(profile)
    return AvatarOut(avatar_base64=profile.avatar_base64)


@router.patch("/users/{user_id}")
def admin_update_profile(
    user_id: int,
    payload: AdminProfileUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_permission("account.manage"))],
) -> ProfileOut:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在"
        )
    if target.role not in PROFILE_EDITABLE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅可维护学生或班委的资料",
        )
    if not can_access_class(db, current_user, target.class_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="无权修改该成员的资料"
        )
    profile = _get_or_create_profile(db, target)
    before = _profile_snapshot(target, profile)
    changed = _apply_common_fields(target, profile, payload)
    for field in ("student_no", "employee_no"):
        raw = getattr(payload, field)
        if raw is None:
            continue
        value = _clean(raw)
        if value == (getattr(profile, field) or ""):
            continue
        _ensure_unique_in_class(db, target, field, value)
        setattr(profile, field, value)
        changed = True
    if changed:
        profile.updated_at = business_now()
        record_operation(
            db,
            action="profile_admin_update",
            operator=current_user,
            class_id=target.class_id,
            target_type="user",
            target_id=target.id,
            target_label=user_label(target),
            before_value=before,
            after_value=_profile_snapshot(target, profile),
        )
        db.commit()
        db.refresh(target)
        db.refresh(profile)
    return _to_out(db, target, profile)