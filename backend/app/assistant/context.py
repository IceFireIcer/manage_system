"""助手上下文白名单清洗与聚合摘要。

前端只允许提交路由/页面/选中实体等有限字段；active_role 与 active_class
一律以服务端会话为准，禁止前端伪造。聚合摘要只含数量级信息，不含学生 PII。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import business_time
from app.class_scope import active_term_name, class_display_name
from app.models import Consumable, Requisition
from app.roles import ROLE_LABELS

ALLOWED_ENTITY_KEYS = ("type", "id", "name", "code", "label")
SENSITIVE_KEY_MARKERS = (
    "password",
    "passwd",
    "pwd",
    "token",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "phone",
    "mobile",
    "email",
    "student_no",
    "id_card",
)
MAX_TEXT = 200
MAX_ROUTE = 200


def _clean_text(value: Any, limit: int = MAX_TEXT) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _clean_route(value: Any) -> str | None:
    text = _clean_text(value, MAX_ROUTE)
    if text is None:
        return None
    path = text.split("?", 1)[0].split("#", 1)[0]
    return path or None


def _clean_page(value: Any) -> str | None:
    text = _clean_text(value, MAX_TEXT)
    if text is None:
        return None
    lowered = text.lower()
    if any(marker in lowered for marker in SENSITIVE_KEY_MARKERS):
        return None
    return text


def _sanitize_entity(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    entity: dict = {}
    for key in ALLOWED_ENTITY_KEYS:
        if key not in raw:
            continue
        value = raw.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            entity[key] = value
        else:
            cleaned = _clean_text(value)
            if cleaned is not None:
                entity[key] = cleaned
    return entity or None


def _aggregate_summary(db: Session, class_id: int) -> dict:
    consumables = db.scalars(
        select(Consumable).where(
            Consumable.class_id == class_id,
            Consumable.status == "enabled",
        )
    ).all()
    low_stock = sum(
        1
        for item in consumables
        if item.safety_stock > 0 and item.current_stock < item.safety_stock
    )
    out_of_stock = sum(1 for item in consumables if item.current_stock == 0)
    today_start = business_time.day_start(business_time.today())
    tomorrow_start = business_time.next_day_start(business_time.today())
    today_requisitions = (
        db.scalar(
            select(func.count())
            .select_from(Requisition)
            .where(
                Requisition.class_id == class_id,
                Requisition.type == "normal",
                Requisition.status == "active",
                Requisition.created_at >= today_start,
                Requisition.created_at < tomorrow_start,
            )
        )
        or 0
    )
    return {
        "consumable_kinds": len(consumables),
        "low_stock_count": low_stock,
        "out_of_stock_count": out_of_stock,
        "today_requisitions": int(today_requisitions),
    }


def build_context(
    db: Session,
    *,
    role: str,
    class_id: int,
    raw: dict | None,
) -> dict:
    """生成可信上下文；active_role/active_class 与聚合摘要由服务端补齐。"""
    raw = raw if isinstance(raw, dict) else {}
    return {
        "route": _clean_route(raw.get("route")),
        "page": _clean_page(raw.get("page")),
        "active_role": role,
        "role_label": ROLE_LABELS.get(role, role),
        "active_class": {
            "id": class_id,
            "name": class_display_name(db, class_id),
            "term_name": active_term_name(db, class_id),
        },
        "selected_entity": _sanitize_entity(raw.get("selected_entity")),
        "summary": _aggregate_summary(db, class_id),
    }
