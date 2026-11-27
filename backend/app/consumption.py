"""消耗汇总与库存反推审计（T09）的核心逻辑。

净消耗口径与 T08 巡检 Agent 共用同一实现：窗口内领用数量减去有效红冲数量，
负值截断为 0；窗口按 Asia/Shanghai 自然日切割（含当天）。

审计部分不新增数据表：领用/红冲从 requisitions 推导，补货从 operation_logs
的 restock 前后值推导，再用 reconstruct_stock 逐条校验库存是否可反推。
"""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.models import OperationLog, Requisition

SHANGHAI_TZ = timezone(timedelta(hours=8))
VALID_WINDOWS = (7, 30)
DEFAULT_WINDOW = 7

REQUISITION_SOURCES = {"normal": "requisition", "reversal": "reversal"}


def local_day_start(days_ago: int = 0, now: datetime | None = None) -> datetime:
    """Asia/Shanghai 自然日零点，转为系统本地 naive 时间以与流水存储口径一致。"""
    reference = now or datetime.now(SHANGHAI_TZ)
    day_start = reference.astimezone(SHANGHAI_TZ).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (day_start - timedelta(days=days_ago)).astimezone().replace(tzinfo=None)


def window_start(window: int, now: datetime | None = None) -> datetime:
    """近 N 天窗口起点：含当天的自然日，故回退 N-1 天到零点。"""
    return local_day_start(window - 1, now)


def window_end(now: datetime | None = None) -> datetime:
    """窗口终点为当前本地时刻（领用不允许未来时间，与整天等价）。"""
    return (now or datetime.now(SHANGHAI_TZ)).astimezone().replace(tzinfo=None)


def daily_rate(quantity: int, window: int) -> float:
    """日均速率 = 净消耗 ÷ 窗口天数，保留 2 位小数。"""
    return round(quantity / window, 2)


def _type_totals(issued_condition, reversed_condition):
    return (
        func.coalesce(func.sum(case((issued_condition, Requisition.quantity), else_=0)), 0),
        func.coalesce(func.sum(case((reversed_condition, Requisition.quantity), else_=0)), 0),
    )


def window_net(
    db: Session, consumable_id: int, start: datetime, end: datetime
) -> int:
    """单耗材窗口净消耗 = max(领用 − 有效红冲, 0)。"""
    window_filters = (
        Requisition.consumable_id == consumable_id,
        Requisition.created_at >= start,
        Requisition.created_at <= end,
    )
    issued, reversed_quantity = db.execute(
        select(
            *_type_totals(
                Requisition.type == "normal",
                and_(
                    Requisition.type == "reversal",
                    Requisition.status == "active",
                ),
            )
        ).where(*window_filters)
    ).one()
    return max(issued - reversed_quantity, 0)


def net_by_consumable(
    db: Session, start: datetime, end: datetime
) -> list[tuple[int, int]]:
    """窗口内出现过流水的耗材及净消耗，按耗材 id 升序返回 (consumable_id, net)。"""
    issued, reversed_quantity = _type_totals(
        Requisition.type == "normal",
        and_(Requisition.type == "reversal", Requisition.status == "active"),
    )
    aggregated = (
        select(
            Requisition.consumable_id.label("consumable_id"),
            issued.label("issued"),
            reversed_quantity.label("reversed"),
        )
        .where(
            Requisition.created_at >= start,
            Requisition.created_at <= end,
        )
        .group_by(Requisition.consumable_id)
        .subquery()
    )
    rows = db.execute(
        select(
            aggregated.c.consumable_id,
            aggregated.c.issued,
            aggregated.c.reversed,
        ).order_by(aggregated.c.consumable_id)
    ).all()
    return [
        (consumable_id, max(issued_quantity - reversed_sum, 0))
        for consumable_id, issued_quantity, reversed_sum in rows
    ]


def _parse_snapshot(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def audit_events(
    db: Session, consumable_id: int
) -> list[dict[str, object]]:
    """按时间正序返回该耗材的库存变动序列（领用/红冲/补货）。"""
    events: list[dict[str, object]] = []

    records = db.scalars(
        select(Requisition).where(Requisition.consumable_id == consumable_id)
    ).all()
    for record in records:
        source = REQUISITION_SOURCES.get(record.type)
        if source is None:
            continue
        delta = -record.quantity if source == "requisition" else record.quantity
        events.append(
            {
                "time": record.created_at,
                "source": source,
                "delta": delta,
                "stock_after": record.stock_after,
                "_priority": 0,
                "_order": record.id,
            }
        )

    restock_logs = db.scalars(
        select(OperationLog).where(
            OperationLog.action == "restock",
            OperationLog.target_id == consumable_id,
        )
    ).all()
    for log in restock_logs:
        before_stock = _parse_snapshot(log.before_value).get("current_stock")
        after_stock = _parse_snapshot(log.after_value).get("current_stock")
        if not isinstance(before_stock, int) or not isinstance(after_stock, int):
            continue
        events.append(
            {
                "time": log.created_at,
                "source": "restock",
                "delta": after_stock - before_stock,
                "stock_after": after_stock,
                "_priority": 1,
                "_order": log.id,
            }
        )

    events.sort(key=lambda event: (event["time"], event["_priority"], event["_order"]))
    return events


@dataclass(frozen=True)
class Reconstruction:
    opening_stock: int | None
    stocks: list[int]
    consistent: bool


def reconstruct_stock(events: Iterable[Mapping[str, object]]) -> Reconstruction:
    """从首条事件反推期初库存，逐条累加 delta 并与 stock_after 比对。"""
    ordered = list(events)
    if not ordered:
        return Reconstruction(opening_stock=None, stocks=[], consistent=True)

    opening_stock = int(ordered[0]["stock_after"]) - int(ordered[0]["delta"])
    running = opening_stock
    stocks: list[int] = []
    for event in ordered:
        running += int(event["delta"])
        stocks.append(running)

    consistent = all(
        stock == int(event["stock_after"]) for stock, event in zip(stocks, ordered, strict=True)
    )
    return Reconstruction(opening_stock=opening_stock, stocks=stocks, consistent=consistent)
