import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session, aliased

from app import business_time
from app.models import OperationLog, Requisition

VALID_WINDOWS = (7, 30)
DEFAULT_WINDOW = 7

REQUISITION_SOURCES = {"normal": "requisition", "reversal": "reversal"}

PROVISIONING_SCOPE = (
    "备料估算按原领用归因：红冲通过关联原领用单抵扣原领用所在窗口的净消耗，"
    "原领用早于当前窗口的红冲只在本窗口计为跨期纠错，不改变本窗口的备料净消耗。"
)


def local_day_start(days_ago: int = 0, now: datetime | None = None) -> datetime:
    reference = business_time.normalize(now) if now is not None else business_time.now()
    return business_time.day_start(reference.date() - timedelta(days=days_ago))


def window_start(window: int, now: datetime | None = None) -> datetime:
    return local_day_start(window - 1, now)


def window_end(now: datetime | None = None) -> datetime:
    return business_time.normalize(now) if now is not None else business_time.now()


def daily_rate(quantity: int, window: int) -> float:
    return round(quantity / window, 2)


@dataclass(frozen=True)
class WindowFlow:
    issued: int
    reversed_quantity: int
    cross_period_reversed: int
    records: int

    @property
    def net(self) -> int:
        return max(self.issued - self.reversed_quantity, 0)

    @property
    def attributed_reversed(self) -> int:
        return self.reversed_quantity - self.cross_period_reversed

    @property
    def attributed_net(self) -> int:
        return max(self.issued - self.attributed_reversed, 0)


def _flow_columns(start: datetime):
    origin = aliased(Requisition)
    issued = func.coalesce(
        func.sum(case((Requisition.type == "normal", Requisition.quantity), else_=0)), 0
    )
    reversed_quantity = func.coalesce(
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
    cross_period_reversed = func.coalesce(
        func.sum(
            case(
                (
                    and_(
                        Requisition.type == "reversal",
                        Requisition.status == "active",
                        origin.id.is_not(None),
                        origin.created_at < start,
                    ),
                    Requisition.quantity,
                ),
                else_=0,
            )
        ),
        0,
    )
    return origin, issued, reversed_quantity, cross_period_reversed


def _window_filters(
    consumable_id: int,
    start: datetime,
    end: datetime,
    end_inclusive: bool,
) -> tuple:
    end_filter = (
        Requisition.created_at <= end
        if end_inclusive
        else Requisition.created_at < end
    )
    return (
        Requisition.consumable_id == consumable_id,
        Requisition.created_at >= start,
        end_filter,
    )


def window_flow(
    db: Session,
    consumable_id: int,
    start: datetime,
    end: datetime,
    *,
    end_inclusive: bool = True,
) -> WindowFlow:
    origin, issued, reversed_quantity, cross_period_reversed = _flow_columns(start)
    issued_total, reversed_sum, cross_sum, records = db.execute(
        select(issued, reversed_quantity, cross_period_reversed, func.count())
        .select_from(Requisition)
        .outerjoin(origin, origin.id == Requisition.reversal_of)
        .where(*_window_filters(consumable_id, start, end, end_inclusive))
    ).one()
    return WindowFlow(
        issued=int(issued_total),
        reversed_quantity=int(reversed_sum),
        cross_period_reversed=int(cross_sum),
        records=int(records),
    )


def window_net(
    db: Session,
    consumable_id: int,
    start: datetime,
    end: datetime,
    *,
    end_inclusive: bool = True,
) -> int:
    return window_flow(
        db, consumable_id, start, end, end_inclusive=end_inclusive
    ).net


@dataclass(frozen=True)
class FlowBreakdown:
    consumable_id: int
    issued: int
    reversed_quantity: int
    cross_period_reversed: int

    @property
    def net(self) -> int:
        return max(self.issued - self.reversed_quantity, 0)


def flow_by_consumable(
    db: Session, start: datetime, end: datetime
) -> list[FlowBreakdown]:
    origin, issued, reversed_quantity, cross_period_reversed = _flow_columns(start)
    rows = db.execute(
        select(
            Requisition.consumable_id,
            issued,
            reversed_quantity,
            cross_period_reversed,
        )
        .select_from(Requisition)
        .outerjoin(origin, origin.id == Requisition.reversal_of)
        .where(
            Requisition.created_at >= start,
            Requisition.created_at <= end,
        )
        .group_by(Requisition.consumable_id)
        .order_by(Requisition.consumable_id)
    ).all()
    return [
        FlowBreakdown(
            consumable_id=consumable_id,
            issued=int(issued_total),
            reversed_quantity=int(reversed_sum),
            cross_period_reversed=int(cross_sum),
        )
        for consumable_id, issued_total, reversed_sum, cross_sum in rows
    ]


def net_by_consumable(
    db: Session, start: datetime, end: datetime
) -> list[tuple[int, int]]:
    return [
        (flow.consumable_id, flow.net)
        for flow in flow_by_consumable(db, start, end)
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
                "_order": record.id,
            }
        )

    restock_logs = db.scalars(
        select(OperationLog).where(
            OperationLog.action.in_(("restock", "restock_correction")),
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
                "_order": log.id,
                "_before": before_stock,
            }
        )

    return _sort_by_execution_order(events)


def _sort_by_execution_order(
    events: list[dict[str, object]],
) -> list[dict[str, object]]:
    flow_events = [
        event for event in events if event["source"] in ("requisition", "reversal")
    ]
    restock_events = [event for event in events if event["source"] == "restock"]

    flow_events.sort(key=lambda event: event["_order"])
    ordered = list(flow_events)
    for restock in sorted(
        restock_events, key=lambda event: (event["time"], event["_order"])
    ):
        insert_at = len(ordered)
        for index, event in enumerate(ordered):
            if event["stock_after"] == restock["_before"]:
                insert_at = index + 1
        ordered.insert(insert_at, restock)

    for index, event in enumerate(ordered):
        event["seq"] = index
    return ordered


@dataclass(frozen=True)
class Reconstruction:
    opening_stock: int | None
    stocks: list[int]
    consistent: bool


def reconstruct_stock(events: Iterable[Mapping[str, object]]) -> Reconstruction:
    ordered = list(events)
    if ordered and "seq" in ordered[0]:
        ordered.sort(key=lambda event: event["seq"])
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
