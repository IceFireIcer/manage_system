import json
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import business_time
from app.database import get_db
from app.deps import require_permission
from app.models import (
    Consumable,
    Course,
    CourseRecipeItem,
    CourseRecipeSnapshot,
    CourseSession,
    Requisition,
    RestockLine,
    RestockOrder,
    TeachingReview,
    User,
)
from app.schemas_courses import (
    REVIEW_TAG_CANDIDATES,
    ArrivalCoverageItemOut,
    ArrivalCoverageOut,
    ArrivalCoverageSessionOut,
    CourseCreate,
    CourseOut,
    CoursePageOut,
    CourseRiskItemOut,
    CourseRiskListOut,
    CourseSessionCreate,
    CourseSessionOut,
    CourseSessionPageOut,
    CourseSessionRiskOut,
    CourseSessionUpdate,
    CourseUpdate,
    RecipeHistoryOut,
    RecipeItemOut,
    RecipeOut,
    RecipeSaveRequest,
    RecipeSnapshotOut,
    TeachingReviewCreate,
    TeachingReviewListOut,
    TeachingReviewOut,
    TeachingReviewRecipeItemOut,
    TeachingReviewUpdate,
    WeekRiskItemOut,
    WeekRiskOut,
    WeekRiskSessionOut,
    WeekRiskUnconfiguredOut,
)

router = APIRouter(prefix="/api/v1/courses", tags=["courses"])

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
RECIPE_HISTORY_LIMIT = 20
REVIEW_HISTORY_LIMIT = 50

CourseViewer = Annotated[User, Depends(require_permission("course.view"))]
CourseManager = Annotated[User, Depends(require_permission("course.manage"))]
RestockViewer = Annotated[User, Depends(require_permission("restock.view"))]


def _get_course(db: Session, course_id: int, user: User) -> Course:
    course = db.get(Course, course_id)
    if course is None or course.class_id != user.active_class_id:
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
    current_user: CourseViewer,
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

    filters = [Course.class_id == current_user.active_class_id]
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
    current_user: CourseManager,
) -> CourseOut:
    class_id = current_user.active_class_id
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


ITEM_RISK_RANK = {"sufficient": 0, "critical": 1, "insufficient": 2}

WEEK_RISK_DEFAULT_DAYS = 7
ARRIVAL_COVERAGE_DEFAULT_DAYS = 7
ARRIVAL_COVERAGE_SESSION_LIMIT = 20
ARRIVAL_COVERAGE_SESSION_MAX = 50


def _risk_level(available: int, safety_stock: int, required: int) -> str:
    if available < required:
        return "insufficient"
    if available < safety_stock:
        return "critical"
    return "sufficient"


def _load_recipe_context(
    db: Session, sessions: list[CourseSession]
) -> tuple[
    dict[int, CourseRecipeSnapshot],
    dict[int, list[CourseRecipeItem]],
    dict[int, Consumable],
]:
    snapshot_ids = {
        session.recipe_snapshot_id
        for session in sessions
        if session.recipe_snapshot_id is not None
    }
    snapshots: dict[int, CourseRecipeSnapshot] = {}
    items_by_snapshot: dict[int, list[CourseRecipeItem]] = {}
    consumables: dict[int, Consumable] = {}
    if not snapshot_ids:
        return snapshots, items_by_snapshot, consumables
    snapshots = {
        row.id: row
        for row in db.scalars(
            select(CourseRecipeSnapshot).where(
                CourseRecipeSnapshot.id.in_(snapshot_ids)
            )
        ).all()
    }
    item_rows = db.scalars(
        select(CourseRecipeItem)
        .where(CourseRecipeItem.snapshot_id.in_(snapshot_ids))
        .order_by(CourseRecipeItem.id)
    ).all()
    for row in item_rows:
        items_by_snapshot.setdefault(row.snapshot_id, []).append(row)
    consumable_ids = {row.consumable_id for row in item_rows}
    if consumable_ids:
        consumables = {
            row.id: row
            for row in db.scalars(
                select(Consumable).where(Consumable.id.in_(consumable_ids))
            ).all()
        }
    return snapshots, items_by_snapshot, consumables


def _upcoming_entries(
    db: Session, class_id: int, days: int
) -> list[tuple[Course, CourseSession]]:
    start = business_time.today()
    end = start + timedelta(days=days)
    rows = db.execute(
        select(Course, CourseSession)
        .join(CourseSession, CourseSession.course_id == Course.id)
        .where(
            Course.class_id == class_id,
            CourseSession.status == "planned",
            CourseSession.session_date >= start,
            CourseSession.session_date <= end,
        )
        .order_by(CourseSession.session_date, Course.id, CourseSession.seq)
    ).all()
    return [(course, session) for course, session in rows]


def _unconfigured_risk(
    course: Course,
    session: CourseSession,
    lead_days: int,
    recipe_version: int | None = None,
) -> CourseSessionRiskOut:
    return CourseSessionRiskOut(
        configured=False,
        level="unconfigured",
        course_id=course.id,
        course_name=course.name,
        session_id=session.id,
        seq=session.seq,
        session_date=session.session_date,
        headcount=session.headcount,
        recipe_version=recipe_version,
        lead_days=lead_days,
        arrival_deadline=None,
        gap=0,
        gap_consumable_name=None,
        items=[],
    )


def _item_risk(
    item: CourseRecipeItem, consumable: Consumable, headcount: int
) -> CourseRiskItemOut:
    required = item.quantity_per_person * headcount
    available = consumable.current_stock
    level = _risk_level(available, consumable.safety_stock, required)
    return CourseRiskItemOut(
        consumable_id=consumable.id,
        consumable_name=consumable.name,
        unit=consumable.unit,
        quantity_per_person=item.quantity_per_person,
        required_quantity=required,
        available_quantity=available,
        safety_stock=consumable.safety_stock,
        gap=max(0, required - available),
        level=level,
    )


def _build_risks(
    db: Session,
    entries: list[tuple[Course, CourseSession]],
    lead_days: int,
) -> list[CourseSessionRiskOut]:
    snapshots, items_by_snapshot, consumables = _load_recipe_context(
        db, [session for _, session in entries]
    )

    results: list[CourseSessionRiskOut] = []
    for course, session in entries:
        if session.recipe_snapshot_id is None:
            results.append(_unconfigured_risk(course, session, lead_days))
            continue
        snapshot = snapshots.get(session.recipe_snapshot_id)
        recipe_version = snapshot.version if snapshot is not None else None
        rows = items_by_snapshot.get(session.recipe_snapshot_id, [])
        if snapshot is None or not rows or session.headcount <= 0:
            results.append(
                _unconfigured_risk(course, session, lead_days, recipe_version)
            )
            continue

        item_outs: list[CourseRiskItemOut] = []
        missing = False
        for row in rows:
            consumable = consumables.get(row.consumable_id)
            if consumable is None or consumable.class_id != course.class_id:
                missing = True
                break
            item_outs.append(_item_risk(row, consumable, session.headcount))
        if missing or not item_outs:
            results.append(
                _unconfigured_risk(course, session, lead_days, recipe_version)
            )
            continue

        worst = max(
            item_outs, key=lambda row: (ITEM_RISK_RANK[row.level], row.gap)
        )
        level = worst.level
        result = CourseSessionRiskOut(
            configured=True,
            level=level,
            course_id=course.id,
            course_name=course.name,
            session_id=session.id,
            seq=session.seq,
            session_date=session.session_date,
            headcount=session.headcount,
            recipe_version=recipe_version,
            lead_days=lead_days,
            arrival_deadline=(
                session.session_date - timedelta(days=lead_days)
                if level != "sufficient"
                else None
            ),
            gap=worst.gap,
            gap_consumable_name=worst.consumable_name,
            items=item_outs,
        )
        results.append(result)
    return results


@router.get("/risk")
def list_course_risks(
    db: Annotated[Session, Depends(get_db)],
    current_user: CourseViewer,
    days: Annotated[int, Query(ge=1, le=60)] = 14,
    lead_days: Annotated[int, Query(ge=0, le=30)] = 1,
) -> CourseRiskListOut:
    start = business_time.today()
    end = start + timedelta(days=days)
    rows = db.execute(
        select(Course, CourseSession)
        .join(CourseSession, CourseSession.course_id == Course.id)
        .where(
            Course.class_id == current_user.active_class_id,
            CourseSession.status == "planned",
            CourseSession.session_date >= start,
            CourseSession.session_date <= end,
        )
        .order_by(CourseSession.session_date, CourseSession.course_id, CourseSession.seq)
    ).all()
    entries = [(course, session) for course, session in rows]
    return CourseRiskListOut(
        days=days,
        lead_days=lead_days,
        items=_build_risks(db, entries, lead_days),
    )


def _build_week_risks(
    db: Session,
    entries: list[tuple[Course, CourseSession]],
    lead_days: int,
) -> tuple[list[WeekRiskItemOut], list[WeekRiskUnconfiguredOut]]:
    snapshots, items_by_snapshot, consumables = _load_recipe_context(
        db, [session for _, session in entries]
    )
    groups: dict[tuple, dict] = {}
    unconfigured: list[WeekRiskUnconfiguredOut] = []

    def skip(course: Course, session: CourseSession, reason: str, message: str) -> None:
        unconfigured.append(
            WeekRiskUnconfiguredOut(
                course_id=course.id,
                course_name=course.name,
                session_id=session.id,
                seq=session.seq,
                session_date=session.session_date,
                headcount=session.headcount,
                reason=reason,
                message=message,
            )
        )

    for course, session in entries:
        if session.recipe_snapshot_id is None:
            skip(course, session, "missing_recipe", "课次未绑定配方快照，未计入实训周聚合")
            continue
        snapshot = snapshots.get(session.recipe_snapshot_id)
        rows = items_by_snapshot.get(session.recipe_snapshot_id, [])
        if snapshot is None:
            skip(course, session, "missing_recipe", "配方快照不存在，未计入实训周聚合")
            continue
        if not rows:
            skip(course, session, "empty_recipe", "配方快照没有耗材，未计入实训周聚合")
            continue
        if session.headcount <= 0:
            skip(course, session, "missing_headcount", "课次人数为 0，未计入实训周聚合")
            continue
        missing = any(
            consumables.get(row.consumable_id) is None
            or consumables[row.consumable_id].class_id != course.class_id
            for row in rows
        )
        if missing:
            skip(
                course,
                session,
                "missing_consumable",
                "配方中的耗材不存在或不属于本班，未计入实训周聚合",
            )
            continue
        for row in rows:
            consumable = consumables[row.consumable_id]
            key = (session.session_date, row.consumable_id)
            group = groups.get(key)
            if group is None:
                group = {
                    "class_label": course.class_label or session.class_label,
                    "consumable": consumable,
                    "required": 0,
                    "sessions": [],
                }
                groups[key] = group
            elif not group["class_label"]:
                group["class_label"] = course.class_label or session.class_label
            required = row.quantity_per_person * session.headcount
            group["required"] += required
            group["sessions"].append(
                WeekRiskSessionOut(
                    course_id=course.id,
                    course_name=course.name,
                    session_id=session.id,
                    seq=session.seq,
                    headcount=session.headcount,
                    required_quantity=required,
                    recipe_version=snapshot.version,
                )
            )

    items: list[WeekRiskItemOut] = []
    for (session_date, _), group in groups.items():
        consumable = group["consumable"]
        required = group["required"]
        available = consumable.current_stock
        level = _risk_level(available, consumable.safety_stock, required)
        items.append(
            WeekRiskItemOut(
                class_id=consumable.class_id,
                class_label=group["class_label"],
                session_date=session_date,
                consumable_id=consumable.id,
                consumable_name=consumable.name,
                unit=consumable.unit,
                required_quantity=required,
                available_quantity=available,
                safety_stock=consumable.safety_stock,
                gap=max(0, required - available),
                level=level,
                arrival_deadline=(
                    session_date - timedelta(days=lead_days)
                    if level != "sufficient"
                    else None
                ),
                sessions=group["sessions"],
            )
        )
    items.sort(
        key=lambda item: (
            item.session_date,
            -ITEM_RISK_RANK[item.level],
            -item.gap,
            item.consumable_id,
        )
    )
    unconfigured.sort(
        key=lambda row: (row.session_date, row.course_id, row.seq)
    )
    return items, unconfigured


@router.get("/risk/week")
def get_week_risk(
    db: Annotated[Session, Depends(get_db)],
    current_user: CourseViewer,
    days: Annotated[int, Query(ge=1, le=60)] = WEEK_RISK_DEFAULT_DAYS,
    lead_days: Annotated[int, Query(ge=0, le=30)] = 1,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> WeekRiskOut:
    entries = _upcoming_entries(db, current_user.active_class_id, days)
    items, unconfigured = _build_week_risks(db, entries, lead_days)
    total = len(items)
    start = (page - 1) * page_size
    return WeekRiskOut(
        days=days,
        lead_days=lead_days,
        page=page,
        page_size=page_size,
        total=total,
        items=items[start : start + page_size],
        unconfigured=unconfigured,
    )


def _arrival_response(
    *,
    status: str,
    matched: bool,
    order_no: str,
    message: str,
    days: int,
    lead_days: int,
    items: list[ArrivalCoverageSessionOut],
    business_at: datetime | None = None,
    total: int = 0,
    truncated: bool = False,
) -> ArrivalCoverageOut:
    return ArrivalCoverageOut(
        matched=matched,
        status=status,
        order_no=order_no,
        message=message,
        days=days,
        lead_days=lead_days,
        business_at=business_at,
        total=total,
        items=items,
        truncated=truncated,
    )


@router.get("/risk/arrival-coverage")
def get_arrival_coverage(
    db: Annotated[Session, Depends(get_db)],
    current_user: RestockViewer,
    order_no: Annotated[str, Query(min_length=1, max_length=40)],
    days: Annotated[int, Query(ge=1, le=60)] = ARRIVAL_COVERAGE_DEFAULT_DAYS,
    lead_days: Annotated[int, Query(ge=0, le=30)] = 1,
    limit: Annotated[
        int, Query(ge=1, le=ARRIVAL_COVERAGE_SESSION_MAX)
    ] = ARRIVAL_COVERAGE_SESSION_LIMIT,
) -> ArrivalCoverageOut:
    normalized = order_no.strip()
    order = db.scalars(
        select(RestockOrder).where(
            RestockOrder.order_no == normalized,
            RestockOrder.class_id == current_user.active_class_id,
        )
    ).first()
    if order is None:
        return _arrival_response(
            status="unmatched",
            matched=False,
            order_no=normalized,
            message="未匹配到本班该入库单",
            days=days,
            lead_days=lead_days,
            items=[],
        )
    if order.status != "submitted":
        return _arrival_response(
            status="not_arrived",
            matched=False,
            order_no=normalized,
            message="该入库单尚未完成入库，暂无法判定覆盖",
            days=days,
            lead_days=lead_days,
            business_at=order.business_at,
            items=[],
        )
    lines = db.scalars(
        select(RestockLine)
        .where(RestockLine.order_id == order.id)
        .order_by(RestockLine.line_no)
    ).all()
    if not lines:
        return _arrival_response(
            status="unmatched",
            matched=False,
            order_no=normalized,
            message="该入库单没有入库明细",
            days=days,
            lead_days=lead_days,
            business_at=order.business_at,
            items=[],
        )

    arrived: dict[int, int] = {}
    for line in lines:
        arrived[line.consumable_id] = arrived.get(line.consumable_id, 0) + line.quantity

    entries = _upcoming_entries(db, current_user.active_class_id, days)
    snapshots, items_by_snapshot, consumables = _load_recipe_context(
        db, [session for _, session in entries]
    )
    session_items: dict[int, dict] = {}
    ordered_ids: list[int] = []

    for consumable_id, quantity in arrived.items():
        consumable = consumables.get(consumable_id)
        if consumable is None or consumable.class_id != current_user.active_class_id:
            continue
        demands: list[tuple[Course, CourseSession, CourseRecipeItem]] = []
        for course, session in entries:
            if session.recipe_snapshot_id is None or session.headcount <= 0:
                continue
            rows = items_by_snapshot.get(session.recipe_snapshot_id, [])
            for row in rows:
                if row.consumable_id == consumable_id:
                    demands.append((course, session, row))
                    break
        if not demands:
            continue
        pool = consumable.current_stock
        before_stock = max(0, pool - quantity)
        for course, session, row in demands:
            required = row.quantity_per_person * session.headcount
            allocated = min(pool, required)
            pool -= allocated
            info = session_items.get(session.id)
            if info is None:
                info = {"course": course, "session": session, "items": []}
                session_items[session.id] = info
                ordered_ids.append(session.id)
            info["items"].append(
                ArrivalCoverageItemOut(
                    consumable_id=consumable.id,
                    consumable_name=consumable.name,
                    unit=consumable.unit,
                    arrived_quantity=quantity,
                    required_quantity=required,
                    covered_quantity=allocated,
                    remaining_quantity=pool,
                    level_before=_risk_level(
                        before_stock, consumable.safety_stock, required
                    ),
                    level_after=_risk_level(
                        allocated, consumable.safety_stock, required
                    ),
                    covered=allocated >= required,
                )
            )

    sessions: list[ArrivalCoverageSessionOut] = []
    for session_id in ordered_ids:
        info = session_items[session_id]
        session = info["session"]
        covered = all(item.covered for item in info["items"])
        sessions.append(
            ArrivalCoverageSessionOut(
                course_id=info["course"].id,
                course_name=info["course"].name,
                session_id=session.id,
                seq=session.seq,
                session_date=session.session_date,
                headcount=session.headcount,
                covered=covered,
                items=info["items"],
                arrival_deadline=(
                    session.session_date - timedelta(days=lead_days)
                    if not covered
                    else None
                ),
            )
        )
    sessions.sort(key=lambda row: (row.session_date, row.course_id, row.seq))
    total = len(sessions)
    trimmed = sessions[:limit]
    covered_count = sum(1 for row in trimmed if row.covered)
    message = (
        f"本次到货匹配 {len(trimmed)} 个待上课次，其中 {covered_count} 个可覆盖"
        if trimmed
        else "未匹配到需要本次到货耗材的 upcoming 课次"
    )
    return _arrival_response(
        status="matched",
        matched=True,
        order_no=normalized,
        message=message,
        days=days,
        lead_days=lead_days,
        business_at=order.business_at,
        items=trimmed,
        total=total,
        truncated=total > limit,
    )


@router.get("/{course_id}/sessions/{session_id}/risk")
def get_session_risk(
    course_id: int,
    session_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: CourseViewer,
    lead_days: Annotated[int, Query(ge=0, le=30)] = 1,
) -> CourseSessionRiskOut:
    course = _get_course(db, course_id, current_user)
    session = db.get(CourseSession, session_id)
    if session is None or session.course_id != course.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="课次不存在"
        )
    return _build_risks(db, [(course, session)], lead_days)[0]


def _load_review_tags(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str)]


def _dump_review_tags(tags: list[str]) -> str:
    return json.dumps(tags, ensure_ascii=False)


def _get_session(db: Session, course: Course, session_id: int) -> CourseSession:
    session = db.get(CourseSession, session_id)
    if session is None or session.course_id != course.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="课次不存在"
        )
    return session


def _session_day_net(
    db: Session, class_id: int, consumable_id: int, session_date
) -> int:
    start = business_time.day_start(session_date)
    end = business_time.next_day_start(session_date)
    issued_expr = func.coalesce(
        func.sum(case((Requisition.type == "normal", Requisition.quantity), else_=0)),
        0,
    )
    reversed_expr = func.coalesce(
        func.sum(
            case(
                (
                    and_(
                        Requisition.type == "reversal",
                        Requisition.status == "active",
                    ),
                    Requisition.quantity,
                ),
                else_=0,
            )
        ),
        0,
    )
    issued, reversed_quantity = db.execute(
        select(issued_expr, reversed_expr).where(
            Requisition.class_id == class_id,
            Requisition.consumable_id == consumable_id,
            Requisition.created_at >= start,
            Requisition.created_at < end,
        )
    ).one()
    return max(int(issued) - int(reversed_quantity), 0)


def _review_recipe_items(db: Session, snapshot_id: int) -> list[CourseRecipeItem]:
    return list(
        db.scalars(
            select(CourseRecipeItem)
            .where(CourseRecipeItem.snapshot_id == snapshot_id)
            .order_by(CourseRecipeItem.id)
        ).all()
    )


def _review_recipe_items_out(
    items: list[CourseRecipeItem], headcount: int
) -> list[TeachingReviewRecipeItemOut]:
    return [
        TeachingReviewRecipeItemOut(
            consumable_id=item.consumable_id,
            consumable_name=item.consumable_name,
            unit=item.unit,
            quantity_per_person=item.quantity_per_person,
            standard_quantity=item.quantity_per_person * headcount,
        )
        for item in items
    ]


def _review_out(db: Session, review: TeachingReview) -> TeachingReviewOut:
    session = db.get(CourseSession, review.session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="课次不存在"
        )
    course = db.get(Course, session.course_id)
    snapshot = db.get(CourseRecipeSnapshot, review.snapshot_id)
    items = _review_recipe_items(db, review.snapshot_id)
    return TeachingReviewOut(
        id=review.id,
        class_id=review.class_id,
        course_id=session.course_id,
        course_name=course.name if course is not None else "",
        session_id=review.session_id,
        session_seq=session.seq,
        session_date=session.session_date,
        headcount=session.headcount,
        snapshot_id=review.snapshot_id,
        recipe_version=snapshot.version if snapshot is not None else None,
        actual_net_quantity=review.actual_net_quantity,
        standard_quantity=review.standard_quantity,
        deviation_ratio=review.deviation_ratio,
        deviation_available=review.deviation_ratio is not None,
        reason_tags=_load_review_tags(review.reason_tags),
        teacher_note=review.teacher_note,
        created_by_name=review.created_by_name,
        created_at=review.created_at,
        version=review.version,
        items=_review_recipe_items_out(items, session.headcount),
        reason_candidates=list(REVIEW_TAG_CANDIDATES),
    )


def _review_snapshot(
    db: Session, course: Course, session: CourseSession
) -> tuple[CourseRecipeSnapshot, list[CourseRecipeItem]]:
    if session.recipe_snapshot_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="课次未绑定配方快照，无法生成复盘",
        )
    snapshot = db.get(CourseRecipeSnapshot, session.recipe_snapshot_id)
    if snapshot is None or snapshot.course_id != course.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="课次配方快照不存在，无法生成复盘",
        )
    items = _review_recipe_items(db, snapshot.id)
    if not items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="配方快照没有耗材，无法生成复盘",
        )
    return snapshot, items


def _review_numbers(
    db: Session, course: Course, session: CourseSession, items: list[CourseRecipeItem]
) -> tuple[int, int, float | None]:
    standard = sum(item.quantity_per_person * session.headcount for item in items)
    actual = sum(
        _session_day_net(
            db, course.class_id, item.consumable_id, session.session_date
        )
        for item in items
    )
    deviation = (
        None if standard == 0 else round((actual - standard) / standard, 4)
    )
    return actual, standard, deviation


@router.post(
    "/{course_id}/sessions/{session_id}/reviews",
    status_code=status.HTTP_201_CREATED,
)
def create_session_review(
    course_id: int,
    session_id: int,
    payload: TeachingReviewCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: CourseManager,
) -> TeachingReviewOut:
    course = _get_course(db, course_id, current_user)
    session = _get_session(db, course, session_id)
    snapshot, items = _review_snapshot(db, course, session)
    actual, standard, deviation = _review_numbers(db, course, session, items)
    latest_version = (
        db.scalar(
            select(func.max(TeachingReview.version)).where(
                TeachingReview.session_id == session.id
            )
        )
        or 0
    )
    review = TeachingReview(
        class_id=course.class_id,
        session_id=session.id,
        snapshot_id=snapshot.id,
        actual_net_quantity=actual,
        standard_quantity=standard,
        deviation_ratio=deviation,
        reason_tags=_dump_review_tags(payload.reason_tags),
        teacher_note=payload.teacher_note,
        created_by_id=current_user.id,
        created_by_name=current_user.name,
        version=latest_version + 1,
    )
    db.add(review)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="复盘版本冲突，请刷新后重试",
        ) from error
    db.refresh(review)
    return _review_out(db, review)


@router.get("/{course_id}/sessions/{session_id}/reviews")
def list_session_reviews(
    course_id: int,
    session_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: CourseViewer,
) -> TeachingReviewListOut:
    course = _get_course(db, course_id, current_user)
    session = _get_session(db, course, session_id)
    rows = db.scalars(
        select(TeachingReview)
        .where(
            TeachingReview.session_id == session.id,
            TeachingReview.class_id == course.class_id,
        )
        .order_by(TeachingReview.version.desc())
        .limit(REVIEW_HISTORY_LIMIT)
    ).all()
    return TeachingReviewListOut(
        items=[_review_out(db, row) for row in rows],
        reason_candidates=list(REVIEW_TAG_CANDIDATES),
    )


@router.get("/reviews/{review_id}")
def get_review(
    review_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: CourseViewer,
) -> TeachingReviewOut:
    review = db.get(TeachingReview, review_id)
    if review is None or review.class_id != current_user.active_class_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="复盘不存在"
        )
    return _review_out(db, review)


@router.patch("/reviews/{review_id}")
def update_review(
    review_id: int,
    payload: TeachingReviewUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: CourseManager,
) -> TeachingReviewOut:
    review = db.get(TeachingReview, review_id)
    if review is None or review.class_id != current_user.active_class_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="复盘不存在"
        )
    data = payload.model_dump(exclude_unset=True)
    if "reason_tags" in data:
        review.reason_tags = _dump_review_tags(data["reason_tags"])
    if "teacher_note" in data:
        review.teacher_note = data["teacher_note"]
    db.commit()
    db.refresh(review)
    return _review_out(db, review)


@router.get("/{course_id}")
def get_course(
    course_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: CourseViewer,
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
    current_user: CourseManager,
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
    current_user: CourseViewer,
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
    current_user: CourseManager,
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
    current_user: CourseManager,
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
    current_user: CourseViewer,
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
    current_user: CourseViewer,
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
    current_user: CourseManager,
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
