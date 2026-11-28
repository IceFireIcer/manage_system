"""悬浮助手只读数据工具注册表。

所有工具按 active_role/active_class 与权限键在服务端二次校验；
返回值仅含聚合数量与耗材级信息，不返回学生姓名/学号/手机号/邮箱等个人明细。
工具只读，不写库存或业务事件；调用审计由路由层记录。
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