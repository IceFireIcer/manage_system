import csv
import io
import json
import zipfile
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import business_time
from app.database import get_db
from app.deps import require_role
from app.models import (
    Consumable,
    Course,
    CourseSession,
    HandoverChecklist,
    HandoverItem,
    IncidentReport,
    InventoryCount,
    InventoryCountLine,
    InventoryEvent,
    TeachingReview,
    User,
)
from app.observability import collect_observability
from app.operation_log import record_operation
from app.readiness import build_readiness
from app.request_context import current_request_id

router = APIRouter(prefix="/api/v1/ops", tags=["ops"])

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
OPS_FORBIDDEN_DETAIL = "运维观测仅允许运维入口访问"

SEMESTER_CLOSE_ACTION = "semester_close_export"
SEMESTER_CLOSE_TARGET = "semester_close"
COUNT_POSTED_STATUS = "posted"
OPEN_INCIDENT_STATUSES = ("open", "confirmed")
CSV_LINE_TERMINATOR = "\r\n"
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
SUMMARY_FILENAME = "summary.json"

KEY_EVENT_FIELDS = (
    ("business_at", "业务时间"),
    ("event_type", "事件类型"),
    ("name_snapshot", "耗材名称"),
    ("spec_snapshot", "规格"),
    ("unit_snapshot", "单位"),
    ("delta", "变动数量"),
    ("stock_before", "变动前库存"),
    ("stock_after", "变动后库存"),
    ("source_type", "来源类型"),
    ("source_id", "来源编号"),
    ("exec_seq", "执行序号"),
    ("operator_name", "经办人"),
    ("operator_role", "经办角色"),
    ("evidence_status", "证据状态"),
    ("created_at", "记录时间"),
)
INCIDENT_FIELDS = (
    ("id", "异常编号"),
    ("created_at", "上报时间"),
    ("consumable_name", "耗材名称"),
    ("location", "存放位置"),
    ("status", "状态"),
    ("handler_name", "处理人"),
    ("handled_at", "处理时间"),

    ("count_order_no", "关联盘点单号"),
    ("updated_at", "更新时间"),
)
HANDOVER_FIELDS = (
    ("version", "交接版本"),
    ("checklist_status", "交接状态"),
    ("outgoing_name", "交班人"),
    ("incoming_name", "接班人"),
    ("snapshot_at", "快照时间"),
    ("confirmed_at", "确认时间"),
    ("confirmed_by_name", "确认人"),
    ("seq", "项目序号"),
    ("kind", "项目类型"),
    ("label", "项目标识"),
    ("confirmed", "是否确认"),
)
COUNT_DIFF_FIELDS = (
    ("order_no", "盘点单号"),
    ("count_at", "盘点时间"),
    ("posted_at", "入库时间"),
    ("consumable_name", "耗材名称"),
    ("unit", "单位"),
    ("baseline_stock", "盘点前库存"),
    ("period_delta", "本期变动"),
    ("counted_qty", "实盘数量"),
    ("diff", "差异"),
    ("reason", "差异原因"),
    ("counter_name", "盘点人"),
    ("reviewer_name", "复核人"),
    ("posted_by_name", "确认人"),
    ("event_id", "调整事件"),
)
REVIEW_FIELDS = (
    ("course_name", "课程名称"),
    ("seq", "课次序号"),
    ("session_date", "上课日期"),
    ("version", "复盘版本"),
    ("standard_quantity", "标准用量"),
    ("actual_net_quantity", "实际净用量"),
    ("deviation_ratio", "偏差比例"),
    ("reason_tags", "原因标签"),
    ("created_by_name", "记录人"),
)

StaffUser = Annotated[User, Depends(require_role("teacher", "admin"))]


def _is_ops_request(request: Request) -> bool:
    client_host = request.client.host if request.client else ""
    return client_host in LOOPBACK_HOSTS


def _iso(value: datetime | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return value.isoformat()


def _csv_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    text = str(value)
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def _csv_text(fields: tuple[tuple[str, str], ...], rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator=CSV_LINE_TERMINATOR)
    writer.writerow([label for _, label in fields])
    for row in rows:
        writer.writerow([_csv_cell(row.get(key)) for key, _ in fields])
    return "\ufeff" + buffer.getvalue()


def _write_entry(archive: zipfile.ZipFile, name: str, text: str) -> None:
    info = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, text.encode("utf-8"))


def _event_rows(
    db: Session, class_id: int, start_at: datetime, end_at: datetime
) -> list[dict]:
    events = db.scalars(
        select(InventoryEvent)
        .where(
            InventoryEvent.class_id == class_id,
            InventoryEvent.business_at >= start_at,
            InventoryEvent.business_at < end_at,
        )
        .order_by(InventoryEvent.business_at, InventoryEvent.id)
    ).all()
    return [
        {
            "business_at": _iso(event.business_at),
            "event_type": event.event_type,
            "name_snapshot": event.name_snapshot,
            "spec_snapshot": event.spec_snapshot,
            "unit_snapshot": event.unit_snapshot,
            "delta": event.delta,
            "stock_before": event.stock_before,
            "stock_after": event.stock_after,
            "source_type": event.source_type,
            "source_id": event.source_id,
            "exec_seq": event.exec_seq,
            "operator_name": event.operator_name,
            "operator_role": event.operator_role,
            "evidence_status": event.evidence_status,
            "created_at": _iso(event.created_at),
        }
        for event in events
    ]


def _incident_rows(
    db: Session, class_id: int, start_at: datetime, end_at: datetime
) -> list[dict]:
    rows = db.execute(
        select(IncidentReport, Consumable.name)
        .outerjoin(Consumable, Consumable.id == IncidentReport.consumable_id)
        .where(
            IncidentReport.class_id == class_id,
            IncidentReport.created_at >= start_at,
            IncidentReport.created_at < end_at,
        )
        .order_by(IncidentReport.created_at, IncidentReport.id)
    ).all()
    return [
        {
            "id": incident.id,
            "created_at": _iso(incident.created_at),
            "consumable_name": consumable_name,
            "location": incident.location,
            "status": incident.status,
            "handler_name": incident.handler_name,
            "handled_at": _iso(incident.handled_at),

            "count_order_no": incident.count_order_no,
            "updated_at": _iso(incident.updated_at),
        }
        for incident, consumable_name in rows
    ]


def _handover_rows(
    db: Session, class_id: int, start_at: datetime, end_at: datetime
) -> tuple[list[dict], int, int]:
    rows = db.execute(
        select(HandoverChecklist, HandoverItem)
        .outerjoin(HandoverItem, HandoverItem.checklist_id == HandoverChecklist.id)
        .where(
            HandoverChecklist.class_id == class_id,
            HandoverChecklist.created_at >= start_at,
            HandoverChecklist.created_at < end_at,
        )
        .order_by(
            HandoverChecklist.created_at,
            HandoverChecklist.id,
            HandoverItem.seq,
        )
    ).all()
    payload = [
        {
            "version": checklist.version,
            "checklist_status": checklist.status,
            "outgoing_name": checklist.outgoing_name,
            "incoming_name": checklist.incoming_name,
            "snapshot_at": _iso(checklist.snapshot_at),
            "confirmed_at": _iso(checklist.confirmed_at),
            "confirmed_by_name": checklist.confirmed_by_name,
            "seq": item.seq if item is not None else None,
            "kind": item.kind if item is not None else None,
            "label": item.label if item is not None else None,
            "confirmed": item.confirmed if item is not None else None,
        }
        for checklist, item in rows
    ]
    checklists = len({checklist.id for checklist, _ in rows})
    items = sum(1 for _, item in rows if item is not None)
    return payload, checklists, items


def _count_difference_rows(
    db: Session, class_id: int, start_at: datetime, end_at: datetime
) -> list[dict]:
    rows = db.execute(
        select(InventoryCount, InventoryCountLine)
        .join(InventoryCountLine, InventoryCountLine.count_id == InventoryCount.id)
        .where(
            InventoryCount.class_id == class_id,
            InventoryCount.status == COUNT_POSTED_STATUS,
            InventoryCount.count_at >= start_at,
            InventoryCount.count_at < end_at,
            InventoryCountLine.diff.is_not(None),
            InventoryCountLine.diff != 0,
        )
        .order_by(
            InventoryCount.count_at,
            InventoryCount.id,
            InventoryCountLine.line_no,
        )
    ).all()
    return [
        {
            "order_no": count.order_no,
            "count_at": _iso(count.count_at),
            "posted_at": _iso(count.posted_at),
            "consumable_name": line.consumable_name,
            "unit": line.unit,
            "baseline_stock": line.baseline_stock,
            "period_delta": line.period_delta,
            "counted_qty": line.counted_qty,
            "diff": line.diff,
            "reason": line.reason,
            "counter_name": count.counter_name,
            "reviewer_name": count.reviewer_name,
            "posted_by_name": count.posted_by_name,
            "event_id": line.event_id,
        }
        for count, line in rows
    ]


def _review_rows(db: Session, class_id: int, start: date, end: date) -> list[dict]:
    rows = db.execute(
        select(TeachingReview, CourseSession, Course)
        .join(CourseSession, CourseSession.id == TeachingReview.session_id)
        .join(Course, Course.id == CourseSession.course_id)
        .where(
            TeachingReview.class_id == class_id,
            CourseSession.session_date >= start,
            CourseSession.session_date <= end,
        )
        .order_by(
            CourseSession.session_date,
            Course.id,
            CourseSession.seq,
            TeachingReview.version.desc(),
        )
    ).all()
    seen: set[int] = set()
    payload = []
    for review, session, course in rows:
        if review.session_id in seen:
            continue
        seen.add(review.session_id)
        payload.append(
            {
                "course_name": course.name,
                "seq": session.seq,
                "session_date": _iso(session.session_date),
                "version": review.version,
                "standard_quantity": review.standard_quantity,
                "actual_net_quantity": review.actual_net_quantity,
                "deviation_ratio": review.deviation_ratio,
                "reason_tags": review.reason_tags,
                "created_by_name": review.created_by_name,
            }
        )
    return payload


def _range_payload(start: date, end: date) -> dict:
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": (end - start).days + 1,
    }


def _collect_semester_close(
    db: Session, current_user: User, start: date, end: date
) -> dict:
    start_at = business_time.day_start(start)
    end_at = business_time.next_day_start(end)
    class_id = current_user.class_id

    key_events = _event_rows(db, class_id, start_at, end_at)
    incidents = _incident_rows(db, class_id, start_at, end_at)
    handover_rows, handover_checklists, handover_items = _handover_rows(
        db, class_id, start_at, end_at
    )
    count_differences = _count_difference_rows(db, class_id, start_at, end_at)
    reviews = _review_rows(db, class_id, start, end)

    files = (
        ("key-events.csv", KEY_EVENT_FIELDS, key_events),
        ("incidents.csv", INCIDENT_FIELDS, incidents),
        ("handover.csv", HANDOVER_FIELDS, handover_rows),
        ("count-differences.csv", COUNT_DIFF_FIELDS, count_differences),
        ("review-summary.csv", REVIEW_FIELDS, reviews),
    )
    totals = {
        "key_events": len(key_events),
        "key_event_delta_sum": sum(row["delta"] for row in key_events),
        "incidents": len(incidents),
        "open_incidents": sum(
            1 for row in incidents if row["status"] in OPEN_INCIDENT_STATUSES
        ),
        "handover_checklists": handover_checklists,
        "handover_items": handover_items,
        "handover_rows": len(handover_rows),
        "count_differences": len(count_differences),
        "count_difference_sum": sum(row["diff"] for row in count_differences),
        "reviews": len(reviews),
    }
    return {
        "range": _range_payload(start, end),
        "files": files,
        "totals": totals,
    }


def _files_payload(files: tuple) -> dict:
    payload = {
        SUMMARY_FILENAME: {"rows": 1, "columns": []},
    }
    for name, fields, rows in files:
        payload[name] = {
            "rows": len(rows),
            "columns": [key for key, _ in fields],
        }
    return payload


def _build_archive(payload: dict, current_user: User, generated_at: datetime) -> bytes:
    files = payload["files"]
    summary = {
        "range": payload["range"],
        "generated_at": _iso(generated_at),
        "generated_by": {
            "name": current_user.name,
            "role": current_user.role,
        },
        "class_id": current_user.class_id,
        "files": _files_payload(files),
        "totals": payload["totals"],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, fields, rows in files:
            _write_entry(archive, name, _csv_text(fields, rows))
        _write_entry(
            archive,
            SUMMARY_FILENAME,
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
    return buffer.getvalue()


@router.get("/observability")
def get_observability(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    if not _is_ops_request(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=OPS_FORBIDDEN_DETAIL,
        )
    readiness = build_readiness(db)
    payload = collect_observability()
    payload["request_id"] = current_request_id()
    payload["readiness"] = {
        "ready": readiness.ready,
        "state": readiness.state,
        "checks": readiness.checks,
    }
    return payload


def _validated_range(start: date, end: date) -> None:
    if business_time.reversed_range(start, end):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=business_time.REVERSE_RANGE_DETAIL,
        )


@router.get("/semester-close/preview")
def preview_semester_close(
    db: Annotated[Session, Depends(get_db)],
    current_user: StaffUser,
    start: Annotated[date, Query()],
    end: Annotated[date, Query()],
) -> dict[str, object]:
    _validated_range(start, end)
    payload = _collect_semester_close(db, current_user, start, end)
    return {
        "range": payload["range"],
        "files": _files_payload(payload["files"]),
        "totals": payload["totals"],
    }


@router.get("/semester-close")
def export_semester_close(
    db: Annotated[Session, Depends(get_db)],
    current_user: StaffUser,
    start: Annotated[date, Query()],
    end: Annotated[date, Query()],
) -> Response:
    _validated_range(start, end)
    payload = _collect_semester_close(db, current_user, start, end)
    generated_at = business_time.now().replace(microsecond=0)
    content = _build_archive(payload, current_user, generated_at)
    record_operation(
        db,
        action=SEMESTER_CLOSE_ACTION,
        operator=current_user,
        target_type=SEMESTER_CLOSE_TARGET,
        target_label=f"{start.isoformat()} ~ {end.isoformat()}",
        after_value=json.dumps(
            {
                "start": payload["range"]["start"],
                "end": payload["range"]["end"],
                "files": _files_payload(payload["files"]),
                "totals": payload["totals"],
                "generated_at": _iso(generated_at),
            },
            ensure_ascii=False,
        ),
        remark=f"导出学期结账包 {start.isoformat()} ~ {end.isoformat()}",
    )
    db.commit()
    filename = (
        f"semester_close_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.zip"
    )
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
