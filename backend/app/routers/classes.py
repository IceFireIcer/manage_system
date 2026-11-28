import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.class_scope import (
    ROLE_TEACHER,
    active_term_name,
    allowed_class_ids,
    can_access_class,
    class_display_name,
    is_super_admin,
)
from app.database import get_db
from app.deps import CurrentClassId, CurrentUser, require_permission
from app.models import Class, ClassMembership, ClassTerm, User
from app.operation_log import record_operation, user_label
from app.schemas_classes import (
    ActiveClassOut,
    ClassCreate,
    ClassDetailOut,
    ClassMemberOut,
    ClassOut,
    ClassTeacherOut,
    ClassTermCreate,
    ClassTermOut,
    ClassTermUpdate,
    ClassUpdate,
    TeacherAssignRequest,
)

router = APIRouter(prefix="/api/v1/classes", tags=["classes"])

CLASS_NOT_FOUND = "班级不存在或无权访问"
TERM_NOT_FOUND = "学期不存在或无权访问"
ActiveClassUser = Annotated[User, Depends(require_permission("dashboard.view"))]


def _get_class(db: Session, class_id: int) -> Class:
    obj = db.get(Class, class_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=CLASS_NOT_FOUND)
    return obj


def _require_super_admin(current_user: User) -> None:
    if not is_super_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="仅超级管理员可执行此操作"
        )


def _require_access(db: Session, current_user: User, class_id: int) -> None:
    if not can_access_class(db, current_user, class_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=CLASS_NOT_FOUND)


def _class_snapshot(obj: Class) -> str:
    return json.dumps(
        {"id": obj.id, "name": obj.name, "code": obj.code, "status": obj.status},
        ensure_ascii=False,
    )


def _term_snapshot(obj: ClassTerm) -> str:
    return json.dumps(
        {
            "id": obj.id,
            "class_id": obj.class_id,
            "name": obj.name,
            "status": obj.status,
            "start_date": obj.start_date.isoformat() if obj.start_date else None,
            "end_date": obj.end_date.isoformat() if obj.end_date else None,
        },
        ensure_ascii=False,
    )


@router.get("")
def list_classes(
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> list[ClassOut]:
    ids = allowed_class_ids(db, current_user)
    if not ids:
        return []
    rows = db.scalars(select(Class).where(Class.id.in_(ids)).order_by(Class.id)).all()
    return rows


@router.post("", status_code=status.HTTP_201_CREATED)
def create_class(
    payload: ClassCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> ClassOut:
    _require_super_admin(current_user)
    conflict = db.scalars(select(Class).where(Class.name == payload.name)).first()
    if conflict is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"班级名称 {payload.name} 已存在",
        )
    obj = Class(name=payload.name, code=payload.code.strip(), status=payload.status)
    db.add(obj)
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="班级名称已存在"
        ) from error
    record_operation(
        db,
        action="class_create",
        operator=current_user,
        class_id=obj.id,
        target_type="class",
        target_id=obj.id,
        target_label=obj.name,
        after_value=_class_snapshot(obj),
        remark=f"创建班级 {obj.name}",
    )
    db.commit()
    db.refresh(obj)
    return obj


@router.get("/active")
def get_active_class(
    db: Annotated[Session, Depends(get_db)],
    current_user: ActiveClassUser,
    class_id: CurrentClassId,
) -> ActiveClassOut:
    return ActiveClassOut(
        id=class_id,
        name=class_display_name(db, class_id),
        term_name=active_term_name(db, class_id),
        available=allowed_class_ids(db, current_user),
        can_switch=len(allowed_class_ids(db, current_user)) > 1,
    )


@router.get("/{class_id}")
def get_class(
    class_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> ClassDetailOut:
    _require_access(db, current_user, class_id)
    obj = _get_class(db, class_id)
    terms = db.scalars(
        select(ClassTerm)
        .where(ClassTerm.class_id == class_id)
        .order_by(ClassTerm.id)
    ).all()
    detail = ClassDetailOut.model_validate(obj)
    detail.terms = [ClassTermOut.model_validate(term) for term in terms]
    return detail


@router.patch("/{class_id}")
def update_class(
    class_id: int,
    payload: ClassUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> ClassOut:
    _require_access(db, current_user, class_id)
    obj = _get_class(db, class_id)
    before = _class_snapshot(obj)
    changed = False
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="班级名称不能为空"
            )
        if name != obj.name:
            conflict = db.scalars(
                select(Class).where(Class.name == name, Class.id != class_id)
            ).first()
            if conflict is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"班级名称 {name} 已存在",
                )
            obj.name = name
            changed = True
    if payload.code is not None and payload.code.strip() != obj.code:
        obj.code = payload.code.strip()
        changed = True
    if payload.status is not None and payload.status != obj.status:
        obj.status = payload.status
        changed = True
    if not changed:
        return obj
    record_operation(
        db,
        action="class_update",
        operator=current_user,
        class_id=class_id,
        target_type="class",
        target_id=obj.id,
        target_label=obj.name,
        before_value=before,
        after_value=_class_snapshot(obj),
        remark=f"更新班级 {obj.name}",
    )
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/{class_id}")
def delete_class(
    class_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> dict[str, str]:
    _require_super_admin(current_user)
    obj = _get_class(db, class_id)
    before = _class_snapshot(obj)
    db.query(ClassTerm).filter(ClassTerm.class_id == class_id).delete(
        synchronize_session=False
    )
    db.query(ClassMembership).filter(ClassMembership.class_id == class_id).delete(
        synchronize_session=False
    )
    db.delete(obj)
    record_operation(
        db,
        action="class_delete",
        operator=current_user,
        class_id=class_id,
        target_type="class",
        target_id=class_id,
        target_label=obj.name,
        before_value=before,
        remark=f"删除班级 {obj.name}",
    )
    db.commit()
    return {"status": "ok"}


@router.get("/{class_id}/terms")
def list_terms(
    class_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> list[ClassTermOut]:
    _require_access(db, current_user, class_id)
    rows = db.scalars(
        select(ClassTerm)
        .where(ClassTerm.class_id == class_id)
        .order_by(ClassTerm.id)
    ).all()
    return rows


@router.post("/{class_id}/terms", status_code=status.HTTP_201_CREATED)
def create_term(
    class_id: int,
    payload: ClassTermCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> ClassTermOut:
    _require_access(db, current_user, class_id)
    _get_class(db, class_id)
    if payload.start_date and payload.end_date and payload.start_date > payload.end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="学期开始日期不能晚于结束日期"
        )
    obj = ClassTerm(
        class_id=class_id,
        name=payload.name.strip(),
        start_date=payload.start_date,
        end_date=payload.end_date,
        status=payload.status,
    )
    db.add(obj)
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="该班级下已存在相同学期"
        ) from error
    record_operation(
        db,
        action="class_term_create",
        operator=current_user,
        class_id=class_id,
        target_type="class_term",
        target_id=obj.id,
        target_label=f"{class_display_name(db, class_id)}/{obj.name}",
        after_value=_term_snapshot(obj),
        remark=f"创建学期 {obj.name}",
    )
    db.commit()
    db.refresh(obj)
    return obj


@router.patch("/{class_id}/terms/{term_id}")
def update_term(
    class_id: int,
    term_id: int,
    payload: ClassTermUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> ClassTermOut:
    _require_access(db, current_user, class_id)
    obj = db.get(ClassTerm, term_id)
    if obj is None or obj.class_id != class_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=TERM_NOT_FOUND)
    before = _term_snapshot(obj)
    if payload.name is not None and payload.name.strip() != obj.name:
        obj.name = payload.name.strip()
    if payload.start_date is not None:
        obj.start_date = payload.start_date
    if payload.end_date is not None:
        obj.end_date = payload.end_date
    if payload.status is not None and payload.status != obj.status:
        obj.status = payload.status
    if obj.start_date and obj.end_date and obj.start_date > obj.end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="学期开始日期不能晚于结束日期"
        )
    record_operation(
        db,
        action="class_term_update",
        operator=current_user,
        class_id=class_id,
        target_type="class_term",
        target_id=obj.id,
        target_label=f"{class_display_name(db, class_id)}/{obj.name}",
        before_value=before,
        after_value=_term_snapshot(obj),
        remark=f"更新学期 {obj.name}",
    )
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/{class_id}/terms/{term_id}")
def delete_term(
    class_id: int,
    term_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> dict[str, str]:
    _require_access(db, current_user, class_id)
    obj = db.get(ClassTerm, term_id)
    if obj is None or obj.class_id != class_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=TERM_NOT_FOUND)
    before = _term_snapshot(obj)
    db.delete(obj)
    record_operation(
        db,
        action="class_term_delete",
        operator=current_user,
        class_id=class_id,
        target_type="class_term",
        target_id=term_id,
        target_label=f"{class_display_name(db, class_id)}/{obj.name}",
        before_value=before,
        remark=f"删除学期 {obj.name}",
    )
    db.commit()
    return {"status": "ok"}


@router.get("/{class_id}/teachers")
def list_class_teachers(
    class_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> list[ClassTeacherOut]:
    _require_access(db, current_user, class_id)
    rows = db.execute(
        select(User)
        .join(ClassMembership, ClassMembership.user_id == User.id)
        .where(ClassMembership.class_id == class_id, ClassMembership.role == ROLE_TEACHER)
        .order_by(User.id)
    ).scalars().all()
    return [
        ClassTeacherOut(user_id=user.id, account=user.account, name=user.name, role=user.role)
        for user in rows
    ]


@router.post("/{class_id}/teachers", status_code=status.HTTP_201_CREATED)
def assign_teacher(
    class_id: int,
    payload: TeacherAssignRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> list[ClassTeacherOut]:
    _require_super_admin(current_user)
    _get_class(db, class_id)
    user = db.get(User, payload.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    if user.role != ROLE_TEACHER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="仅可将教师账号分配到班级"
        )
    existing = db.scalars(
        select(ClassMembership).where(
            ClassMembership.class_id == class_id,
            ClassMembership.user_id == user.id,
        )
    ).first()
    if existing is None:
        membership = ClassMembership(
            class_id=class_id, user_id=user.id, role=ROLE_TEACHER
        )
        db.add(membership)
        record_operation(
            db,
            action="class_teacher_assign",
            operator=current_user,
            class_id=class_id,
            target_type="class",
            target_id=class_id,
            target_label=class_display_name(db, class_id),
            after_value=json.dumps(
                {"user_id": user.id, "account": user.account}, ensure_ascii=False
            ),
            remark=f"分配教师 {user_label(user)} 到班级 {class_id}",
        )
        db.commit()
    return list_class_teachers(class_id, db, current_user)


@router.delete("/{class_id}/teachers/{user_id}")
def remove_teacher(
    class_id: int,
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> dict[str, str]:
    _require_super_admin(current_user)
    membership = db.scalars(
        select(ClassMembership).where(
            ClassMembership.class_id == class_id,
            ClassMembership.user_id == user_id,
        )
    ).first()
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="教师分配不存在")
    db.delete(membership)
    record_operation(
        db,
        action="class_teacher_remove",
        operator=current_user,
        class_id=class_id,
        target_type="class",
        target_id=class_id,
        target_label=class_display_name(db, class_id),
        before_value=json.dumps({"user_id": user_id}, ensure_ascii=False),
        remark=f"移除班级 {class_id} 的教师 {user_id}",
    )
    db.commit()
    return {"status": "ok"}


@router.get("/{class_id}/members")
def list_class_members(
    class_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> list[ClassMemberOut]:
    _require_access(db, current_user, class_id)
    rows = db.scalars(
        select(User).where(User.class_id == class_id).order_by(User.id)
    ).all()
    return [
        ClassMemberOut(
            user_id=user.id,
            account=user.account,
            name=user.name,
            role=user.role,
            status=user.status,
        )
        for user in rows
    ]
