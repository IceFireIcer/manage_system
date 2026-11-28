"""悬浮助手工具注册表。

只读工具返回聚合数据；写工具只在用户二次确认后执行；预填工具仅生成
目标路由与参数，不提交账本。所有工具按 active_role/active_class 与
权限键在服务端二次校验；工具参数禁止携带 class_id/role。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app import business_time, stock_alerts
from app.agent import rules
from app.consumption import local_day_start, window_flow
from app.models import Consumable, IncidentReport, Requisition, User
from app.permissions import effective_permission_set

PERMISSION_DENIED = "当前角色无权调用该数据工具"
UNKNOWN_TOOL = "未知的数据工具"
EXECUTE_FAILED = "工具执行失败，请稍后重试"
TIER_LABELS = {"urgent": "紧急", "moderate": "关注", "observe": "观察"}
RISK_LEVELS = ("insufficient", "critical", "unconfigured")

KIND_READ = "read"
KIND_WRITE = "write"
KIND_PREFILL = "prefill"
ACTION_PENDING = "pending"
ACTION_PREFILLED = "prefilled"
ACTION_CONFIRMED = "confirmed"
ACTION_CANCELLED = "cancelled"
ACTION_FAILED = "failed"

PROFILE_FIELDS = ("name", "phone", "email", "wechat", "qq", "github")
PROFILE_FIELD_LABELS = {
    "name": "姓名",
    "phone": "手机号",
    "email": "邮箱",
    "wechat": "微信",
    "qq": "QQ",
    "github": "GitHub",
}
ACTION_QUERY_MAX = 200
FORBIDDEN_ARG_KEYS = frozenset(
    {
        "class_id",
        "class",
        "role",
        "active_role",
        "active_class",
        "active_class_id",
        "user_id",
        "operator_id",
        "permission",
        "permissions",
        "settings",
        "llm",
        "ledger",
        "inventory_event",
        "event",
    }
)
FORBIDDEN_TOOL_MARKERS = (
    "role",
    "account",
    "permission",
    "llm",
    "setting",
    "config",
    "secret",
    "ledger",
    "inventory_event",
    "password",
)


class ToolActionError(Exception):
    """工具动作被拒绝或无法完成；消息直接展示给用户。"""


@dataclass(frozen=True)
class ToolContext:
    db: Session
    user: User
    role: str
    class_id: int


@dataclass(frozen=True)
class ToolSpec:
    name: str
    label: str
    description: str
    permission_key: str
    handler: Callable[[ToolContext], dict]


def _enabled_consumables(db: Session, class_id: int) -> list[Consumable]:
    return list(
        db.scalars(
            select(Consumable)
            .where(
                Consumable.class_id == class_id,
                Consumable.status == "enabled",
            )
            .order_by(Consumable.id)
        ).all()
    )


def _dashboard_summary(ctx: ToolContext) -> dict:
    db, class_id = ctx.db, ctx.class_id
    consumables = _enabled_consumables(db, class_id)
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
    _, active_alerts = stock_alerts.list_alerts(db, class_id)
    return {
        "consumable_kinds": len(consumables),
        "low_stock_count": low_stock,
        "out_of_stock_count": out_of_stock,
        "today_requisitions": int(today_requisitions),
        "active_alerts": int(active_alerts),
    }


def _inventory_overview(ctx: ToolContext) -> dict:
    consumables = _enabled_consumables(ctx.db, ctx.class_id)
    items = []
    low_stock = 0
    for item in consumables:
        is_low = item.safety_stock > 0 and item.current_stock < item.safety_stock
        if is_low:
            low_stock += 1
        items.append(
            {
                "name": item.name,
                "spec": item.spec,
                "unit": item.unit,
                "current_stock": item.current_stock,
                "safety_stock": item.safety_stock,
                "low_stock": is_low,
                "is_out": item.current_stock == 0,
            }
        )
    items.sort(key=lambda row: (not row["is_out"], not row["low_stock"], row["name"]))
    return {
        "total_kinds": len(consumables),
        "low_stock_count": low_stock,
        "items": items[:50],
        "truncated": len(items) > 50,
    }


def _consumable_names(db: Session, class_id: int) -> dict[int, tuple[str, str]]:
    rows = db.execute(
        select(Consumable.id, Consumable.name, Consumable.unit).where(
            Consumable.class_id == class_id
        )
    ).all()
    return {row[0]: (row[1], row[2]) for row in rows}


def _flow_totals(db: Session, class_id: int, start, end) -> list[tuple[int, int, int, int]]:
    issued = func.coalesce(
        func.sum(case((Requisition.type == "normal", Requisition.quantity), else_=0)), 0
    )
    reversed_quantity = func.coalesce(
        func.sum(case((Requisition.type == "reversal", Requisition.quantity), else_=0)), 0
    )
    rows = db.execute(
        select(
            Requisition.consumable_id,
            issued,
            reversed_quantity,
            func.count(),
        )
        .where(
            Requisition.class_id == class_id,
            Requisition.created_at >= start,
            Requisition.created_at <= end,
        )
        .group_by(Requisition.consumable_id)
        .order_by(Requisition.consumable_id)
    ).all()
    return [
        (row[0], int(row[1]), int(row[2]), int(row[3]))
        for row in rows
    ]


def _records_summary(ctx: ToolContext, days: int = 7) -> dict:
    db, class_id = ctx.db, ctx.class_id
    start = local_day_start(days - 1)
    end = business_time.now()
    names = _consumable_names(db, class_id)
    items = []
    total_records = 0
    for consumable_id, issued, reversed_quantity, records in _flow_totals(
        db, class_id, start, end
    ):
        name, unit = names.get(consumable_id, ("已删除耗材", ""))
        net = max(issued - reversed_quantity, 0)
        total_records += records
        items.append(
            {
                "name": name,
                "unit": unit,
                "issued_quantity": issued,
                "reversed_quantity": reversed_quantity,
                "net_quantity": net,
                "records": records,
            }
        )
    items.sort(key=lambda row: row["net_quantity"], reverse=True)
    return {
        "window_days": days,
        "total_records": total_records,
        "items": items[:30],
        "truncated": len(items) > 30,
    }


def _consumption_summary(ctx: ToolContext, days: int = 7) -> dict:
    db, class_id = ctx.db, ctx.class_id
    start = local_day_start(days - 1)
    end = business_time.now()
    names = _consumable_names(db, class_id)
    items = []
    for consumable_id, issued, reversed_quantity, _records in _flow_totals(
        db, class_id, start, end
    ):
        name, unit = names.get(consumable_id, ("已删除耗材", ""))
        net = max(issued - reversed_quantity, 0)
        items.append(
            {
                "name": name,
                "unit": unit,
                "net_quantity": net,
                "daily_rate": round(net / days, 2),
            }
        )
    items.sort(key=lambda row: row["net_quantity"], reverse=True)
    return {
        "window_days": days,
        "items": items[:30],
        "truncated": len(items) > 30,
    }


def _restock_suggestions(ctx: ToolContext) -> dict:
    db = ctx.db
    now = business_time.now()
    q7_start = local_day_start(rules.WINDOW_DAYS - 1, now)
    prev_start = local_day_start(2 * rules.WINDOW_DAYS - 1, now)
    items = []
    for consumable in _enabled_consumables(db, ctx.class_id):
        q7_flow = window_flow(db, consumable.id, q7_start, now)
        prev_flow = window_flow(
            db, consumable.id, prev_start, q7_start, end_inclusive=False
        )
        evaluation = rules.evaluate(
            current_stock=consumable.current_stock,
            safety_stock=consumable.safety_stock,
            q7=q7_flow.attributed_net,
            prev_q7=prev_flow.attributed_net,
            issued=q7_flow.issued,
            reversed_quantity=q7_flow.attributed_reversed,
            records=q7_flow.records,
        )
        if evaluation is None:
            continue
        items.append(
            {
                "name": consumable.name,
                "unit": consumable.unit,
                "current_stock": evaluation.current_stock,
                "safety_stock": evaluation.safety_stock,
                "gap": evaluation.gap,
                "q7": evaluation.q7,
                "days_of_supply": evaluation.days_of_supply,
                "tier": evaluation.tier,
                "tier_label": TIER_LABELS.get(evaluation.tier, evaluation.tier),
                "recommended_quantity": evaluation.recommended_quantity,
            }
        )
    items.sort(key=lambda row: (-row["recommended_quantity"], row["name"]))
    return {"items": items[:30], "truncated": len(items) > 30}


def _incident_summary(ctx: ToolContext) -> dict:
    db, class_id = ctx.db, ctx.class_id
    counts = {
        row[0]: int(row[1])
        for row in db.execute(
            select(IncidentReport.status, func.count())
            .where(IncidentReport.class_id == class_id)
            .group_by(IncidentReport.status)
        ).all()
    }
    recent_rows = db.execute(
        select(IncidentReport, Consumable.name)
        .join(Consumable, Consumable.id == IncidentReport.consumable_id)
        .where(IncidentReport.class_id == class_id)
        .order_by(IncidentReport.created_at.desc(), IncidentReport.id.desc())
        .limit(5)
    ).all()
    recent = [
        {
            "consumable_name": name,
            "status": report.status,
            "created_at": report.created_at.isoformat(sep=" ", timespec="minutes"),
        }
        for report, name in recent_rows
    ]
    return {
        "counts": counts,
        "total": sum(counts.values()),
        "open_count": counts.get("open", 0),
        "recent": recent,
    }


def _course_risk(ctx: ToolContext, days: int = 14) -> dict:
    from app.routers import courses as courses_router

    entries = courses_router._upcoming_entries(ctx.db, ctx.class_id, days)
    risks = courses_router._build_risks(ctx.db, entries, 1)
    risky = [risk for risk in risks if risk.level in RISK_LEVELS]
    items = [
        {
            "course_name": risk.course_name,
            "session_date": risk.session_date.isoformat(),
            "level": risk.level,
            "gap": risk.gap,
            "gap_consumable_name": risk.gap_consumable_name,
        }
        for risk in risky[:20]
    ]
    return {
        "days": days,
        "session_count": len(risks),
        "risk_count": len(risky),
        "items": items,
        "truncated": len(risky) > 20,
    }


def _handover_health(ctx: ToolContext) -> dict:
    from app.routers import handover as handover_router

    stock_diff_count, stock_diff_available = handover_router._stock_diff_health(
        ctx.db, ctx.class_id
    )
    return {
        "stock_diff_count": int(stock_diff_count),
        "stock_diff_available": bool(stock_diff_available),
        "open_incident_count": int(
            handover_router._open_incident_health(ctx.db, ctx.class_id)
        ),
        "course_risk_count": int(
            handover_router._course_risk_health(ctx.db, ctx.class_id)
        ),
    }


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "dashboard_summary",
        "工作台摘要",
        "本班耗材种类、低库存与缺货数量、今日领用单数、活跃库存告警数",
        "dashboard.view",
        _dashboard_summary,
    ),
    ToolSpec(
        "inventory_overview",
        "库存总览",
        "本班耗材的库存与安全库存明细（耗材级，不含个人明细）",
        "inventory.view",
        _inventory_overview,
    ),
    ToolSpec(
        "records_summary",
        "领用记录汇总",
        "近若干天按耗材聚合的领用数量、红冲数量与笔数（不含个人明细）",
        "record.view",
        _records_summary,
    ),
    ToolSpec(
        "restock_suggestions",
        "补货建议",
        "按补货规则计算的低库存耗材建议补货量与分级",
        "restock.view",
        _restock_suggestions,
    ),
    ToolSpec(
        "consumption_summary",
        "消耗汇总",
        "近若干天按耗材聚合的净消耗量与日均消耗",
        "consumption.view",
        _consumption_summary,
    ),
    ToolSpec(
        "incident_summary",
        "异常汇总",
        "本班异常上报的状态计数与最近条目（耗材级，不含上报人姓名）",
        "incident.view",
        _incident_summary,
    ),
    ToolSpec(
        "course_risk",
        "课程风险",
        "未来若干天已排课次的缺料风险与缺口（按耗材级）",
        "course.view",
        _course_risk,
    ),
    ToolSpec(
        "handover_health",
        "交接健康",
        "交接健康指标：最近盘点差异项、未处理异常数、课程风险数",
        "handover.view",
        _handover_health,
    ),
)

TOOL_MAP: dict[str, ToolSpec] = {spec.name: spec for spec in TOOL_SPECS}


def tool_catalog() -> list[dict]:
    return [
        {"name": spec.name, "label": spec.label, "description": spec.description}
        for spec in TOOL_SPECS
    ]


def tool_prompt_text() -> str:
    return "\n".join(
        f"- {spec.name}：{spec.label}，{spec.description}" for spec in TOOL_SPECS
    )


def has_permission(ctx: ToolContext, permission_key: str) -> bool:
    if ctx.role == "super_admin":
        return True
    return permission_key in effective_permission_set(
        ctx.db, ctx.role, ctx.class_id
    )


def run_tool(name: str, ctx: ToolContext) -> dict:
    spec = TOOL_MAP.get(name)
    if spec is None:
        return {"ok": False, "tool": name, "error": UNKNOWN_TOOL}
    if not has_permission(ctx, spec.permission_key):
        return {"ok": False, "tool": name, "label": spec.label, "error": PERMISSION_DENIED}
    try:
        data = spec.handler(ctx)
    except Exception:
        return {"ok": False, "tool": name, "label": spec.label, "error": EXECUTE_FAILED}
    return {"ok": True, "tool": name, "label": spec.label, "data": data}

@dataclass(frozen=True)
class ActionToolSpec:
    name: str
    label: str
    description: str
    permission_key: str
    kind: str
    params: tuple[str, ...]
    preview: Callable[[ToolContext, dict], dict]
    execute: Callable[[ToolContext, dict], dict] | None
    requires_confirmation: bool


def _int_arg(args: dict, key: str, label: str) -> int:
    raw = args.get(key)
    if raw is None or raw == "":
        raise ToolActionError(f"请提供{label}")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ToolActionError(f"{label}格式不正确")
    if value <= 0:
        raise ToolActionError(f"{label}必须为正整数")
    return value


def _optional_int_arg(args: dict, key: str) -> int | None:
    raw = args.get(key)
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _require_consumable(ctx: ToolContext, args: dict) -> Consumable:
    consumable_id = _int_arg(args, "consumable_id", "耗材")
    consumable = ctx.db.get(Consumable, consumable_id)
    if consumable is None or consumable.class_id != ctx.class_id:
        raise ToolActionError("耗材不存在或不属于当前班级")
    if consumable.status != "enabled":
        raise ToolActionError("耗材已停用，无法操作")
    return consumable


def _http_action(call: Callable) -> object:
    from fastapi import HTTPException

    try:
        return call()
    except HTTPException as exc:
        raise ToolActionError(str(exc.detail))


def _preview_update_my_profile(ctx: ToolContext, args: dict) -> dict:
    from app.routers import profile as profile_router

    fields = [key for key in PROFILE_FIELDS if key in args]
    if not fields:
        raise ToolActionError("请提供要修改的资料字段")
    if "name" in args:
        _http_action(lambda: profile_router._normalize_name(args["name"]))
    if "phone" in args:
        _http_action(lambda: profile_router._normalize_phone(args["phone"]))
    if "email" in args:
        _http_action(lambda: profile_router._normalize_email(args["email"]))
    labels = "、".join(PROFILE_FIELD_LABELS[key] for key in fields)
    return {"summary": f"修改本人资料：{labels}", "fields": fields}


def _execute_update_my_profile(ctx: ToolContext, args: dict) -> dict:
    from app.routers import profile as profile_router

    payload = profile_router.ProfileUpdate(
        **{key: args[key] for key in PROFILE_FIELDS if key in args}
    )
    profile, changed = profile_router.update_profile_fields(
        ctx.db, ctx.user, payload, commit=False
    )
    return {
        "changed": changed,
        "fields": [key for key in PROFILE_FIELDS if key in args],
        "name": ctx.user.name,
    }


def _validate_incident_args(ctx: ToolContext, args: dict) -> Consumable:
    from app.schemas_incidents import (
        DESCRIPTION_MAX_LENGTH,
        LOCATION_MAX_LENGTH,
    )

    consumable = _require_consumable(ctx, args)
    description = str(args.get("description") or "").strip()
    if not description:
        raise ToolActionError("请填写异常描述")
    if len(description) > DESCRIPTION_MAX_LENGTH:
        raise ToolActionError(f"异常描述不能超过 {DESCRIPTION_MAX_LENGTH} 个字符")
    location = str(args.get("location") or "").strip()
    if len(location) > LOCATION_MAX_LENGTH:
        raise ToolActionError(f"位置不能超过 {LOCATION_MAX_LENGTH} 个字符")
    return consumable


def _preview_create_incident(ctx: ToolContext, args: dict) -> dict:
    consumable = _validate_incident_args(ctx, args)
    description = str(args.get("description") or "").strip()
    preview = description[:60] + ("…" if len(description) > 60 else "")
    return {
        "summary": f"提交异常单：{consumable.name}，{preview}",
        "consumable_id": consumable.id,
        "consumable_name": consumable.name,
    }


def _execute_create_incident(ctx: ToolContext, args: dict) -> dict:
    from app.routers import incidents as incidents_router
    from app.schemas_incidents import STATUS_OPEN

    consumable = _validate_incident_args(ctx, args)
    report = _http_action(
        lambda: incidents_router.create_report(
            ctx.db,
            user=ctx.user,
            class_id=ctx.class_id,
            consumable=consumable,
            description=str(args.get("description") or ""),
            location=str(args.get("location") or ""),
        )
    )
    return {
        "incident_id": report.id,
        "consumable_id": consumable.id,
        "consumable_name": consumable.name,
        "status": STATUS_OPEN,
    }


def _prefill_requisition(ctx: ToolContext, args: dict) -> dict:
    consumable = _require_consumable(ctx, args)
    quantity = _optional_int_arg(args, "quantity")
    query = {"consumable_id": str(consumable.id)}
    if quantity is not None:
        query["quantity"] = str(quantity)
    amount = f" {quantity}{consumable.unit}" if quantity is not None else ""
    return {
        "summary": f"前往领用登记填写：{consumable.name}{amount}",
        "route": "/requisition",
        "query": query,
    }


def _prefill_inbound(ctx: ToolContext, args: dict) -> dict:
    consumable = _require_consumable(ctx, args)
    quantity = _optional_int_arg(args, "quantity")
    query = {"consumable_id": str(consumable.id)}
    if quantity is not None:
        query["quantity"] = str(quantity)
    amount = f" {quantity}{consumable.unit}" if quantity is not None else ""
    return {
        "summary": f"前往补货入库填写：{consumable.name}{amount}",
        "route": "/restock",
        "query": query,
    }


def _prefill_inventory_count(ctx: ToolContext, args: dict) -> dict:
    consumable = _require_consumable(ctx, args)
    return {
        "summary": f"前往库存盘点：{consumable.name}",
        "route": "/overview",
        "query": {
            "focus": "inventory_count",
            "consumable_id": str(consumable.id),
        },
    }


def _prefill_reversal(ctx: ToolContext, args: dict) -> dict:
    consumable_id = _optional_int_arg(args, "consumable_id")
    requisition_id = _optional_int_arg(args, "requisition_id")
    if consumable_id is None and requisition_id is None:
        raise ToolActionError("请提供耗材或领用记录")
    query: dict[str, str] = {"action": "reversal"}
    if consumable_id is not None:
        consumable = _require_consumable(ctx, args)
        query["consumable_id"] = str(consumable.id)
    if requisition_id is not None:
        query["requisition_id"] = str(requisition_id)
    return {
        "summary": "前往记录查询选择原始领用进行红冲/纠错",
        "route": "/records",
        "query": query,
    }


def _prefill_return(ctx: ToolContext, args: dict) -> dict:
    consumable_id = _optional_int_arg(args, "consumable_id")
    requisition_id = _optional_int_arg(args, "requisition_id")
    if consumable_id is None and requisition_id is None:
        raise ToolActionError("请提供耗材或领用记录")
    query: dict[str, str] = {"action": "return"}
    if consumable_id is not None:
        consumable = _require_consumable(ctx, args)
        query["consumable_id"] = str(consumable.id)
    if requisition_id is not None:
        query["requisition_id"] = str(requisition_id)
    return {
        "summary": "前往记录查询选择领用单办理退库",
        "route": "/records",
        "query": query,
    }


def _prefill_scrap(ctx: ToolContext, args: dict) -> dict:
    consumable_id = _optional_int_arg(args, "consumable_id")
    requisition_id = _optional_int_arg(args, "requisition_id")
    if consumable_id is None and requisition_id is None:
        raise ToolActionError("请提供耗材或领用记录")
    query: dict[str, str] = {"action": "scrap"}
    if consumable_id is not None:
        consumable = _require_consumable(ctx, args)
        query["consumable_id"] = str(consumable.id)
    if requisition_id is not None:
        query["requisition_id"] = str(requisition_id)
    return {
        "summary": "前往记录查询选择领用单办理报损",
        "route": "/records",
        "query": query,
    }


def _prefill_course_recipe(ctx: ToolContext, args: dict) -> dict:
    course_id = _optional_int_arg(args, "course_id")
    query: dict[str, str] = {}
    if course_id is not None:
        query["course_id"] = str(course_id)
    consumable_id = _optional_int_arg(args, "consumable_id")
    if consumable_id is not None:
        consumable = _require_consumable(ctx, args)
        query["consumable_id"] = str(consumable.id)
    if not query:
        raise ToolActionError("请提供课程或耗材")
    return {
        "summary": "前往课程配方维护预填课次耗材",
        "route": "/courses",
        "query": query,
    }


ACTION_TOOL_SPECS: tuple[ActionToolSpec, ...] = (
    ActionToolSpec(
        "update_my_profile",
        "修改本人资料",
        "在用户确认后修改本人的姓名、手机号、邮箱、微信、QQ 或 GitHub",
        "profile.edit",
        KIND_WRITE,
        PROFILE_FIELDS,
        _preview_update_my_profile,
        _execute_update_my_profile,
        True,
    ),
    ActionToolSpec(
        "create_incident",
        "提交异常单",
        "在用户确认后为指定耗材提交异常单（不支持照片）",
        "incident.create",
        KIND_WRITE,
        ("consumable_id", "description", "location"),
        _preview_create_incident,
        _execute_create_incident,
        True,
    ),
    ActionToolSpec(
        "prefill_requisition",
        "预填领用登记",
        "只跳转到领用登记并预选耗材与数量，不提交",
        "requisition.create",
        KIND_PREFILL,
        ("consumable_id", "quantity"),
        _prefill_requisition,
        None,
        False,
    ),
    ActionToolSpec(
        "prefill_inbound",
        "预填补货入库",
        "只跳转到补货页并预选耗材与数量，不提交",
        "restock.manage",
        KIND_PREFILL,
        ("consumable_id", "quantity"),
        _prefill_inbound,
        None,
        False,
    ),
    ActionToolSpec(
        "prefill_inventory_count",
        "预填库存盘点",
        "只跳转到库存总览并打开盘点入口，不提交",
        "inventory.manage",
        KIND_PREFILL,
        ("consumable_id",),
        _prefill_inventory_count,
        None,
        False,
    ),
    ActionToolSpec(
        "prefill_reversal",
        "预填红冲/纠错",
        "只跳转到记录查询并定位领用记录，不提交",
        "requisition.approve",
        KIND_PREFILL,
        ("consumable_id", "requisition_id"),
        _prefill_reversal,
        None,
        False,
    ),
    ActionToolSpec(
        "prefill_return",
        "预填退库",
        "只跳转到记录查询并定位领用记录，不提交",
        "requisition.approve",
        KIND_PREFILL,
        ("consumable_id", "requisition_id"),
        _prefill_return,
        None,
        False,
    ),
    ActionToolSpec(
        "prefill_scrap",
        "预填报损",
        "只跳转到记录查询并定位领用记录，不提交",
        "requisition.approve",
        KIND_PREFILL,
        ("consumable_id", "requisition_id"),
        _prefill_scrap,
        None,
        False,
    ),
    ActionToolSpec(
        "prefill_course_recipe",
        "预填课程配方",
        "只跳转到课程配方页并预选课程/耗材，不提交",
        "course.manage",
        KIND_PREFILL,
        ("course_id", "consumable_id"),
        _prefill_course_recipe,
        None,
        False,
    ),
)

ACTION_TOOL_MAP: dict[str, ActionToolSpec] = {
    spec.name: spec for spec in ACTION_TOOL_SPECS
}


def is_forbidden_tool(name: str) -> bool:
    lowered = name.strip().lower()
    return any(marker in lowered for marker in FORBIDDEN_TOOL_MARKERS)


def clean_action_args(spec: ActionToolSpec, raw: object) -> dict:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ToolActionError("工具参数格式不正确")
    for key in raw:
        if str(key).strip().lower() in FORBIDDEN_ARG_KEYS:
            raise ToolActionError("工具参数不能携带 class_id/role 等会话字段")
    cleaned: dict = {}
    for key in spec.params:
        value = raw.get(key)
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            cleaned[key] = int(value)
            continue
        text = str(value).strip()
        if text:
            cleaned[key] = text[:ACTION_QUERY_MAX]
    return cleaned


def prepare_action(
    name: str, ctx: ToolContext, raw_args: object
) -> tuple[ActionToolSpec, dict, dict]:
    spec = ACTION_TOOL_MAP.get(name)
    if spec is None:
        raise ToolActionError("未知或不受支持的操作工具")
    if not has_permission(ctx, spec.permission_key):
        raise ToolActionError(PERMISSION_DENIED)
    args = clean_action_args(spec, raw_args)
    preview = spec.preview(ctx, args)
    return spec, args, preview


def execute_action(spec: ActionToolSpec, ctx: ToolContext, args: dict) -> dict:
    if spec.execute is None:
        raise ToolActionError("该工具只支持预填，不执行提交")
    return spec.execute(ctx, args)


def action_tool_prompt_text() -> str:
    lines = []
    for spec in ACTION_TOOL_SPECS:
        params = "、".join(spec.params) or "无"
        mode = "需用户二次确认后执行" if spec.requires_confirmation else "只预填不提交"
        lines.append(
            f"- {spec.name}：{spec.label}（{mode}），参数：{params}。{spec.description}"
        )
    return "\n".join(lines)