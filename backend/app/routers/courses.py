from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import business_time
from app.database import get_db
from app.deps import require_role
from app.models import (
    Consumable,
    Course,
    CourseRecipeItem,
    CourseRecipeSnapshot,
    CourseSession,
    User,
)
from app.schemas_courses import (
    CourseCreate,
    CourseOut,
    CoursePageOut,
    CourseSessionCreate,
    CourseSessionOut,
    CourseSessionPageOut,
    CourseSessionUpdate,
    CourseUpdate,
    RecipeHistoryOut,
    RecipeItemOut,
    RecipeOut,
    RecipeSaveRequest,
    RecipeSnapshotOut,
)

router = APIRouter(prefix="/api/v1/courses", tags=["courses"])

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
RECIPE_HISTORY_LIMIT = 20

StaffUser = Annotated[User, Depends(require_role("teacher", "admin"))]


def _get_course(db: Session, course_id: int, user: User) -> Course:
    course = db.get(Course, course_id)
    if course is None or course.class_id != user.class_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="课程不存在"
        )
    return course


def _latest_snapshot(db: Session, course_id: int) -> CourseRecipeSnapshot | None:
    return db.scalars(
        select(CourseRecipeSnapshot)
        .where(CourseRecipeSnapshot.course_id == course_id)
        .order_by(CourseRecipeSnapshot.version.desc())
    ).first()


def _session_counts(db: Session, course_ids: list[int]) -> dict[int, int]:
    if not course_ids:
        return {}
    rows = db.execute(
        select(CourseSession.course_id, func.count())
        .where(CourseSession.course_id.in_(course_ids))
        .group_by(CourseSession.course_id)
    ).all()
    return {course_id: count for course_id, count in rows}


def _recipe_versions(db: Session, course_ids: list[int]) -> dict[int, int]:
    if not course_ids:
        return {}
    rows = db.execute(
        select(CourseRecipeSnapshot.course_id, func.max(CourseRecipeSnapshot.version))
        .where(CourseRecipeSnapshot.course_id.in_(course_ids))
        .group_by(CourseRecipeSnapshot.course_id)
    ).all()
    return {course_id: version for course_id, version in rows if version is not None}


def _course_out(
    course: Course,
    session_count: int = 0,
    recipe_version: int | None = None,
) -> CourseOut:
    return CourseOut(
        id=course.id,
        class_id=course.class_id,
        name=course.name,
        class_label=course.class_label,
        semester=course.semester,
        status=course.status,
        created_by_name=course.created_by_name,
        version=course.version,
        session_count=session_count,
        recipe_version=recipe_version,
        created_at=course.created_at,
        updated_at=course.updated_at,
    )


def _snapshot_out(db: Session, snapshot: CourseRecipeSnapshot) -> RecipeSnapshotOut:
    rows = db.scalars(
        select(CourseRecipeItem)
        .where(CourseRecipeItem.snapshot_id == snapshot.id)
        .order_by(CourseRecipeItem.id)
    ).all()
    consumable_ids = [row.consumable_id for row in rows]
    specs: dict[int, str] = {}
    if consumable_ids:
        specs = {
            item.id: item.spec
            for item in db.scalars(
                select(Consumable).where(Consumable.id.in_(consumable_ids))
            ).all()
        }
    return RecipeSnapshotOut(
        id=snapshot.id,
        course_id=snapshot.course_id,
        version=snapshot.version,
        note=snapshot.note,
        created_by_name=snapshot.created_by_name,
        created_at=snapshot.created_at,
        items=[
            RecipeItemOut(
                consumable_id=row.consumable_id,
                name=row.consumable_name,
                spec=specs.get(row.consumable_id, ""),
                unit=row.unit,
                quantity_per_person=row.quantity_per_person,
                remark=row.remark,
            )
            for row in rows
        ],
    )


def _session_snapshot_versions(
    db: Session, sessions: list[CourseSession]
) -> dict[int, int]:
    snapshot_ids = [
        session.recipe_snapshot_id
        for session in sessions
        if session.recipe_snapshot_id is not None
    ]
    if not snapshot_ids:
        return {}
    rows = db.execute(
        select(CourseRecipeSnapshot.id, CourseRecipeSnapshot.version).where(
            CourseRecipeSnapshot.id.in_(snapshot_ids)
        )
    ).all()
    return {snapshot_id: version for snapshot_id, version in rows}


def _session_out(session: CourseSession, recipe_version: int | None) -> CourseSessionOut:
    return CourseSessionOut(
        id=session.id,
        course_id=session.course_id,
        seq=session.seq,
        session_date=session.session_date,
        headcount=session.headcount,
        class_label=session.class_label,
        status=session.status,
        recipe_snapshot_id=session.recipe_snapshot_id,
        recipe_version=recipe_version,
        created_by_name=session.created_by_name,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def _assert_course_version(
    course: Course, expected_version: int | None
) -> None:
    if expected_version is None or expected_version == course.version:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "message": "课程已被他人修改，请重新核对后再提交",
            "version": course.version,
        },
    )


def _ensure_course_unique(
    db: Session, class_id: int, name: str, semester: str, exclude_id: int | None = None
) -> None:
    query = select(Course).where(
        Course.class_id == class_id,
        Course.name == name,
        Course.semester == semester,
    )
    if exclude_id is not None:
        query = query.where(Course.id != exclude_id)
    if db.scalars(query).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"同学期已存在同名课程「{name}」，请修改名称或学期",
        )


def _ensure_seq_unique(
    db: Session,
    course_id: int,
    seq: int,
    exclude_id: int | None = None,
) -> None:
    query = select(CourseSession).where(
        CourseSession.course_id == course_id, CourseSession.seq == seq
    )
    if exclude_id is not None:
        query = query.where(CourseSession.id != exclude_id)
    if db.scalars(query).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"该课程已存在第 {seq} 次课",
        )


@router.get("")
def list_courses(
    db: Annotated[Session, Depends(get_db)],
    current_user: StaffUser,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    keyword: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> CoursePageOut:
    if status_filter in (None, "", "all"):
        status_filter = None
    elif status_filter not in ("enabled", "disabled"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="status 参数仅支持 enabled/disabled/all",
        )

    filters = [Course.class_id == current_user.class_id]
    if status_filter is not None:
        filters.append(Course.status == status_filter)
    keyword_text = (keyword or "").strip()
    if keyword_text:
        pattern = f"%{keyword_text}%"
        filters.append(
            or_(
                Course.name.like(pattern),
                Course.class_label.like(pattern),
                Course.semester.like(pattern),
            )
        )

    total = db.scalar(select(func.count()).select_from(Course).where(*filters)) or 0
    courses = db.scalars(
        select(Course)
        .where(*filters)
        .order_by(Course.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    course_ids = [course.id for course in courses]
    counts = _session_counts(db, course_ids)
    versions = _recipe_versions(db, course_ids)
    return CoursePageOut(
        items=[
            _course_out(course, counts.get(course.id, 0), versions.get(course.id))
            for course in courses
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_course(
    payload: CourseCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: StaffUser,
) -> CourseOut:
    class_id = current_user.class_id
    _ensure_course_unique(db, class_id, payload.name, payload.semester)
    course = Course(
        class_id=class_id,
        name=payload.name,
        class_label=payload.class_label,
        semester=payload.semester,
        status="enabled",
        created_by_id=current_user.id,
        created_by_name=current_user.name,
    )
    db.add(course)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="同学期已存在同名课程，请修改名称或学期",
        ) from error
    db.refresh(course)
    return _course_out(course)


@router.get("/{course_id}")
def get_course(
    course_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: StaffUser,
) -> CourseOut:
    course = _get_course(db, course_id, current_user)
    counts = _session_counts(db, [course.id])
    versions = _recipe_versions(db, [course.id])
    return _course_out(course, counts.get(course.id, 0), versions.get(course.id))


@router.put("/{course_id}")
def update_course(
    course_id: int,
    payload: CourseUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: StaffUser,
) -> CourseOut:
    course = _get_course(db, course_id, current_user)
    _assert_course_version(course, payload.expected_version)
    data = payload.model_dump(exclude_unset=True)
    data.pop("expected_version", None)
    name = data.get("name", course.name)
    semester = data.get("semester", course.semester)
    if "name" in data or "semester" in data:
        _ensure_course_unique(
            db, course.class_id, name, semester, exclude_id=course.id
        )
    if "name" in data:
        course.name = name
    if "class_label" in data:
        course.class_label = data["class_label"]
    if "semester" in data:
        course.semester = semester
    if "status" in data:
        course.status = data["status"]
    course.updated_at = business_time.now()
    course.version += 1
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="同学期已存在同名课程，请修改名称或学期",
        ) from error
    db.refresh(course)
    counts = _session_counts(db, [course.id])
    versions = _recipe_versions(db, [course.id])
    return _course_out(course, counts.get(course.id, 0), versions.get(course.id))


@router.get("/{course_id}/sessions")
def list_sessions(
    course_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: StaffUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> CourseSessionPageOut:
    course = _get_course(db, course_id, current_user)
    total = (
        db.scalar(
            select(func.count())
            .select_from(CourseSession)
            .where(CourseSession.course_id == course.id)
        )
        or 0
    )
    sessions = db.scalars(
        select(CourseSession)
        .where(CourseSession.course_id == course.id)
        .order_by(CourseSession.seq)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    versions = _session_snapshot_versions(db, sessions)
    return CourseSessionPageOut(
        items=[
            _session_out(session, versions.get(session.recipe_snapshot_id))
            for session in sessions
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/{course_id}/sessions", status_code=status.HTTP_201_CREATED)
def create_session(
    course_id: int,
    payload: CourseSessionCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: StaffUser,
) -> CourseSessionOut:
    course = _get_course(db, course_id, current_user)
    _ensure_seq_unique(db, course.id, payload.seq)
    latest = _latest_snapshot(db, course.id)
    session = CourseSession(
        course_id=course.id,
        class_id=course.class_id,
        seq=payload.seq,
        session_date=payload.session_date,
        headcount=payload.headcount,
        class_label=payload.class_label or course.class_label,
        status=payload.status,
        recipe_snapshot_id=latest.id if latest is not None else None,
        created_by_id=current_user.id,
        created_by_name=current_user.name,
    )
    db.add(session)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"该课程已存在第 {payload.seq} 次课",
        ) from error
    db.refresh(session)
    return _session_out(session, latest.version if latest is not None else None)


@router.put("/{course_id}/sessions/{session_id}")
def update_session(
    course_id: int,
    session_id: int,
    payload: CourseSessionUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: StaffUser,
) -> CourseSessionOut:
    course = _get_course(db, course_id, current_user)
    session = db.get(CourseSession, session_id)
    if session is None or session.course_id != course.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="课次不存在"
        )
    data = payload.model_dump(exclude_unset=True)
    if "seq" in data:
        _ensure_seq_unique(db, course.id, data["seq"], exclude_id=session.id)
        session.seq = data["seq"]
    if "session_date" in data:
        session.session_date = data["session_date"]
    if "headcount" in data:
        session.headcount = data["headcount"]
    if "class_label" in data:
        session.class_label = data["class_label"] or course.class_label
    if "status" in data:
        session.status = data["status"]
    session.updated_at = business_time.now()
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该课程已存在相同序号的课次",
        ) from error
    db.refresh(session)
    versions = _session_snapshot_versions(db, [session])
    return _session_out(session, versions.get(session.recipe_snapshot_id))


@router.get("/{course_id}/recipes/current")
def get_current_recipe(
    course_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: StaffUser,
) -> RecipeOut:
    course = _get_course(db, course_id, current_user)
    snapshot = _latest_snapshot(db, course.id)
    if snapshot is None:
        return RecipeOut(configured=False, snapshot=None)
    return RecipeOut(configured=True, snapshot=_snapshot_out(db, snapshot))


@router.get("/{course_id}/recipes")
def list_recipe_history(
    course_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: StaffUser,
) -> RecipeHistoryOut:
    course = _get_course(db, course_id, current_user)
    snapshots = db.scalars(
        select(CourseRecipeSnapshot)
        .where(CourseRecipeSnapshot.course_id == course.id)
        .order_by(CourseRecipeSnapshot.version.desc())
        .limit(RECIPE_HISTORY_LIMIT)
    ).all()
    return RecipeHistoryOut(
        items=[_snapshot_out(db, snapshot) for snapshot in snapshots]
    )


@router.post("/{course_id}/recipes", status_code=status.HTTP_201_CREATED)
def save_recipe(
    course_id: int,
    payload: RecipeSaveRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: StaffUser,
) -> RecipeSnapshotOut:
    course = _get_course(db, course_id, current_user)
    consumable_ids = [item.consumable_id for item in payload.items]
    consumables = {
        item.id: item
        for item in db.scalars(
            select(Consumable).where(
                Consumable.id.in_(consumable_ids),
                Consumable.class_id == course.class_id,
                Consumable.status == "enabled",
            )
        ).all()
    }
    missing = [item for item in payload.items if item.consumable_id not in consumables]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="配方中的耗材不存在、已停用或不属于本班",
        )
    for item in payload.items:
        consumable = consumables[item.consumable_id]
        if item.unit is not None and item.unit != consumable.unit:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"「{consumable.name}」的单位为 {consumable.unit}，"
                    "必须与耗材主数据一致"
                ),
            )

    latest_version = (
        db.scalar(
            select(func.max(CourseRecipeSnapshot.version)).where(
                CourseRecipeSnapshot.course_id == course.id
            )
        )
        or 0
    )
    snapshot = CourseRecipeSnapshot(
        course_id=course.id,
        class_id=course.class_id,
        version=latest_version + 1,
        created_by_id=current_user.id,
        created_by_name=current_user.name,
        note=payload.note,
    )
    db.add(snapshot)
    try:
        db.flush()
        for item in payload.items:
            consumable = consumables[item.consumable_id]
            db.add(
                CourseRecipeItem(
                    snapshot_id=snapshot.id,
                    consumable_id=consumable.id,
                    consumable_name=consumable.name,
                    unit=consumable.unit,
                    quantity_per_person=item.quantity_per_person,
                    remark=item.remark,
                )
            )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="配方版本冲突，请刷新后重试",
        ) from error
    db.refresh(snapshot)
    return _snapshot_out(db, snapshot)
