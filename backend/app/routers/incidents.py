import uuid
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import business_time, inventory_count
from app.database import get_db
from app.deps import effective_role, require_permission
from app.models import Consumable, IncidentReport, InventoryCount, User
from app.schemas_incidents import (
    ALLOWED_PHOTO_TYPES,
    DESCRIPTION_MAX_LENGTH,
    LOCATION_MAX_LENGTH,
    MAX_PHOTO_BYTES,
    PHOTO_EXTENSIONS,
    PHOTO_MEDIA_TYPES,
    RATE_LIMIT_MAX_SUBMISSIONS,
    RATE_LIMIT_WINDOW_MINUTES,
    STATUS_CONFIRMED,
    STATUS_CONVERTED,
    STATUS_OPEN,
    STATUS_REJECTED,
    STATUSES,
    IncidentHandle,
    IncidentOut,
    IncidentPageOut,
)
from app.schemas_inventory import InventoryCountCreate, InventoryCountLineIn

router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])

PHOTO_DIR = Path(__file__).resolve().parents[2] / "var" / "incidents"
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
PHOTO_READ_LIMIT = MAX_PHOTO_BYTES + 1
RATE_LIMIT_WINDOW = timedelta(minutes=RATE_LIMIT_WINDOW_MINUTES)

IncidentViewer = Annotated[User, Depends(require_permission("incident.view"))]
IncidentReporter = Annotated[User, Depends(require_permission("incident.create"))]
IncidentManager = Annotated[User, Depends(require_permission("incident.manage"))]


def _photo_url(report: IncidentReport) -> str | None:
    if not report.photo_path:
        return None
    return f"/api/v1/incidents/{report.id}/photo"


def _serialize(db: Session, report: IncidentReport) -> IncidentOut:
    consumable = db.get(Consumable, report.consumable_id)
    count = (
        db.get(InventoryCount, report.linked_count_id)
        if report.linked_count_id is not None
        else None
    )
    count_status = (
        count.status
        if count is not None and count.class_id == report.class_id
        else None
    )
    return IncidentOut(
        id=report.id,
        class_id=report.class_id,
        consumable_id=report.consumable_id,
        consumable_name=consumable.name if consumable is not None else "",
        consumable_spec=consumable.spec if consumable is not None else "",
        unit=consumable.unit if consumable is not None else "",
        reporter_id=report.reporter_id,
        reporter_name=report.reporter_name,
        reporter_role=report.reporter_role,
        location=report.location,
        description=report.description,
        photo_url=_photo_url(report),
        status=report.status,
        handler_id=report.handler_id,
        handler_name=report.handler_name,
        handled_at=report.handled_at,
        reject_reason=report.reject_reason,
        linked_count_id=report.linked_count_id,
        count_order_no=report.count_order_no,
        count_status=count_status,
        created_at=report.created_at,
        updated_at=report.updated_at,
    )


def _get_visible_or_404(
    db: Session, incident_id: int, current_user: User
) -> IncidentReport:
    report = db.get(IncidentReport, incident_id)
    if report is None or report.class_id != current_user.active_class_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="异常单不存在"
        )
    if effective_role(current_user) == "student" and report.reporter_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="异常单不存在"
        )
    return report


def _matches_declared_type(content_type: str, data: bytes) -> bool:
    if content_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/webp":
        return (
            len(data) >= 12
            and data[:4] == b"RIFF"
            and data[8:12] == b"WEBP"
        )
    return False


def _read_photo(photo: UploadFile) -> tuple[bytes, str]:
    content_type = (photo.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_PHOTO_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="照片仅支持 JPEG、PNG 或 WebP 格式",
        )
    data = photo.file.read(PHOTO_READ_LIMIT)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="照片不能超过 5MB",
        )
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="照片内容为空",
        )
    if not _matches_declared_type(content_type, data):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="照片内容与声明格式不一致",
        )
    return data, content_type


def _store_photo(data: bytes, content_type: str) -> str:
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{PHOTO_EXTENSIONS[content_type]}"
    (PHOTO_DIR / filename).write_bytes(data)
    return filename


def _remove_photo(filename: str | None) -> None:
    if not filename:
        return
    try:
        (PHOTO_DIR / filename).unlink()
    except FileNotFoundError:
        pass


def _enforce_rate_limit(db: Session, user: User) -> None:
    window_start = business_time.now() - RATE_LIMIT_WINDOW
    recent = db.scalar(
        select(func.count())
        .select_from(IncidentReport)
        .where(
            IncidentReport.reporter_id == user.id,
            IncidentReport.created_at >= window_start,
        )
    ) or 0
    if recent >= RATE_LIMIT_MAX_SUBMISSIONS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="提交过于频繁，请稍后再试",
            headers={"Retry-After": str(RATE_LIMIT_WINDOW_MINUTES * 60)},
        )


def create_report(
    db: Session,
    *,
    user: User,
    class_id: int,
    consumable: Consumable,
    description: str,
    location: str | None = None,
    photo_path: str | None = None,
) -> IncidentReport:
    """校验并暂存异常单；事务提交由调用方负责。"""
    cleaned_description = description.strip()
    if not cleaned_description:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="请填写异常描述"
        )
    if len(cleaned_description) > DESCRIPTION_MAX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"异常描述不能超过 {DESCRIPTION_MAX_LENGTH} 个字符",
        )

    cleaned_location = (location or "").strip() or consumable.location
    if len(cleaned_location) > LOCATION_MAX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"位置不能超过 {LOCATION_MAX_LENGTH} 个字符",
        )

    _enforce_rate_limit(db, user)

    report = IncidentReport(
        class_id=class_id,
        consumable_id=consumable.id,
        reporter_id=user.id,
        reporter_name=user.name,
        reporter_role=user.role,
        location=cleaned_location,
        description=cleaned_description,
        photo_path=photo_path,
        status=STATUS_OPEN,
        created_at=business_time.now(),
        updated_at=business_time.now(),
    )
    db.add(report)
    db.flush()
    return report


@router.post("", status_code=status.HTTP_201_CREATED)
def submit_incident(
    db: Annotated[Session, Depends(get_db)],
    current_user: IncidentReporter,
    consumable_id: Annotated[int, Form()],
    description: Annotated[str, Form()],
    location: Annotated[str | None, Form()] = None,
    photo: Annotated[UploadFile | None, File()] = None,
) -> IncidentOut:
    consumable = db.get(Consumable, consumable_id)
    if consumable is None or consumable.class_id != current_user.active_class_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="耗材不存在"
        )
    if consumable.status != "enabled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="耗材已停用，无法上报异常",
        )

    photo_path: str | None = None
    if photo is not None:
        data, content_type = _read_photo(photo)
        photo_path = _store_photo(data, content_type)

    report = create_report(
        db,
        user=current_user,
        class_id=current_user.active_class_id,
        consumable=consumable,
        description=description,
        location=location,
        photo_path=photo_path,
    )
    try:
        db.commit()
    except Exception:
        _remove_photo(photo_path)
        raise
    db.refresh(report)
    return _serialize(db, report)


@router.get("")
def list_incidents(
    db: Annotated[Session, Depends(get_db)],
    current_user: IncidentViewer,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> IncidentPageOut:
    if status_filter is not None and status_filter not in STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="status 仅支持 open/confirmed/rejected/converted_to_count",
        )

    query = select(IncidentReport).where(
        IncidentReport.class_id == current_user.active_class_id
    )
    if effective_role(current_user) == "student":
        query = query.where(IncidentReport.reporter_id == current_user.id)
    if status_filter is not None:
        query = query.where(IncidentReport.status == status_filter)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(IncidentReport.created_at.desc(), IncidentReport.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return IncidentPageOut(
        items=[_serialize(db, report) for report in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{incident_id}")
def get_incident(
    incident_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: IncidentViewer,
) -> IncidentOut:
    report = _get_visible_or_404(db, incident_id, current_user)
    return _serialize(db, report)


@router.patch("/{incident_id}")
def handle_incident(
    incident_id: int,
    payload: IncidentHandle,
    db: Annotated[Session, Depends(get_db)],
    staff: IncidentManager,
) -> IncidentOut:
    report = _get_visible_or_404(db, incident_id, staff)
    if report.status != STATUS_OPEN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="异常单已处理"
        )

    reject_reason: str | None = None
    if payload.action == "reject":
        reject_reason = payload.reject_reason
        if not reject_reason:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="请填写驳回原因"
            )
        report.status = STATUS_REJECTED
        report.reject_reason = reject_reason
    else:
        report.status = STATUS_CONFIRMED
        report.reject_reason = None

    handled_at = business_time.now()
    report.handler_id = staff.id
    report.handler_name = staff.name
    report.handled_at = handled_at
    report.updated_at = handled_at
    db.commit()
    db.refresh(report)
    return _serialize(db, report)


def _conversion_request_id(report: IncidentReport) -> str:
    return f"incident-{report.id}"


@router.post("/{incident_id}/convert-to-count")
def convert_incident_to_count(
    incident_id: int,
    db: Annotated[Session, Depends(get_db)],
    staff: IncidentManager,
) -> JSONResponse:
    report = _get_visible_or_404(db, incident_id, staff)
    count = (
        db.get(InventoryCount, report.linked_count_id)
        if report.linked_count_id is not None
        else None
    )
    if count is not None and count.class_id == report.class_id:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=jsonable_encoder(_serialize(db, report)),
        )

    if report.status not in (STATUS_OPEN, STATUS_CONFIRMED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="当前状态不可转盘点"
        )

    payload = InventoryCountCreate(
        lines=[
            InventoryCountLineIn(
                consumable_id=report.consumable_id,
                counted_qty=None,
                reason="异常转盘点",
            )
        ],
        remark=f"异常单 #{report.id}",
        request_id=_conversion_request_id(report),
    )
    existing = db.scalars(
        select(InventoryCount).where(
            InventoryCount.request_id == payload.request_id,
            InventoryCount.class_id == staff.active_class_id,
        )
    ).first()
    if existing is not None:
        count, created = existing, False
    else:
        count, created = inventory_count.create_count(
            db, payload=payload, operator=staff
        )

    count.incident_id = report.id
    timestamp = business_time.now()
    count.updated_at = timestamp
    report.linked_count_id = count.id
    report.count_order_no = count.order_no
    report.status = STATUS_CONVERTED
    report.updated_at = timestamp
    db.add(count)
    db.add(report)
    db.commit()
    db.refresh(report)
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=jsonable_encoder(_serialize(db, report)),
    )


@router.get("/{incident_id}/photo")
def get_incident_photo(
    incident_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: IncidentViewer,
) -> FileResponse:
    report = _get_visible_or_404(db, incident_id, current_user)
    if not report.photo_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="照片不存在"
        )
    filename = Path(report.photo_path).name
    path = PHOTO_DIR / filename
    if filename != report.photo_path or not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="照片不存在"
        )
    media_type = PHOTO_MEDIA_TYPES.get(path.suffix.lstrip("."), "application/octet-stream")
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )
