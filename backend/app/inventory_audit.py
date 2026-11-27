from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app import inventory_ledger

BASIS_POSTING = "posted_at"
BASIS_BUSINESS = "business_at"

BASIS_LABELS = {
    BASIS_POSTING: "按入账时间",
    BASIS_BUSINESS: "按业务时间重排",
}

EVIDENCE_LABELS = {
    inventory_ledger.EVIDENCE_TRUSTED: "可信",
    inventory_ledger.EVIDENCE_INSUFFICIENT: "证据不足",
    "unverifiable": "无法验证",
}


@dataclass(frozen=True)
class Reconciliation:
    opening_stock: int | None
    opening_event_id: int | None
    opening_source: str | None
    ledger_end: int | None
    diff: int | None
    evidence_status: str
    evidence_reasons: tuple[str, ...]
    first_anomaly_event_id: int | None
    first_anomaly_reason: str | None
    affected_start_event_id: int | None
    affected_end_event_id: int | None


@dataclass(frozen=True)
class ReorderedEvent:
    event: Any
    stock_before: int
    stock_after: int


@dataclass(frozen=True)
class PostingView:
    events: tuple[Any, ...]
    stock_at_cutoff: int | None


@dataclass(frozen=True)
class BusinessView:
    opening: Any | None
    events: tuple[ReorderedEvent, ...]
    stock_at_cutoff: int | None


def _ordered(events: Iterable[Any]) -> list[Any]:
    return sorted(events, key=lambda event: (event.exec_seq, event.id))


def _opening_of(ordered: list[Any]) -> Any | None:
    if ordered and ordered[0].event_type == inventory_ledger.EVENT_OPENING:
        return ordered[0]
    return None


def reconcile(events: Iterable[Any], current_stock: int) -> Reconciliation:
    ordered = _ordered(events)
    opening = _opening_of(ordered)
    opening_stock = opening.stock_after if opening is not None else None
    opening_event_id = opening.id if opening is not None else None
    opening_source = opening.source_type if opening is not None else None

    if not ordered:
        return Reconciliation(
            opening_stock=None,
            opening_event_id=None,
            opening_source=None,
            ledger_end=None,
            diff=None,
            evidence_status="unverifiable",
            evidence_reasons=("没有可读取的库存事件",),
            first_anomaly_event_id=None,
            first_anomaly_reason=None,
            affected_start_event_id=None,
            affected_end_event_id=None,
        )

    anomaly_event: Any | None = None
    anomaly_reason: str | None = None
    if opening is None:
        anomaly_event = ordered[0]
        anomaly_reason = "缺少可信期初事件，无法验证起点"
    elif opening.evidence_status != inventory_ledger.EVIDENCE_TRUSTED:
        anomaly_event = opening
        anomaly_reason = (
            "期初事件无法验证"
            if opening.evidence_status == "unverifiable"
            else "期初来源证据不足，需要人工核对"
        )

    checked = ordered[1:]
    previous = opening.stock_after if opening is not None else None
    for event in checked:
        if anomaly_event is None and event.evidence_status == "unverifiable":
            anomaly_event = event
            anomaly_reason = f"第 {event.exec_seq} 步事件无法读取"
        elif anomaly_event is None and event.evidence_status == (
            inventory_ledger.EVIDENCE_INSUFFICIENT
        ):
            anomaly_event = event
            anomaly_reason = f"第 {event.exec_seq} 步事件证据不足，需要人工核对"
        if event.stock_after != event.stock_before + event.delta:
            if anomaly_event is None:
                anomaly_event = event
                anomaly_reason = (
                    f"第 {event.exec_seq} 步变动量与前后的余额不符"
                )
        elif previous is not None and event.stock_before != previous:
            if anomaly_event is None:
                anomaly_event = event
                anomaly_reason = (
                    f"第 {event.exec_seq} 步变动前余额 {event.stock_before} "
                    f"与上一步变动后余额 {previous} 不符"
                )
        previous = event.stock_after

    ledger_end = ordered[-1].stock_after
    diff = current_stock - ledger_end

    reasons: list[str] = []
    if opening is None:
        evidence_status = "unverifiable"
        reasons.append("缺少可信期初事件")
    elif opening.evidence_status == "unverifiable":
        evidence_status = "unverifiable"
        reasons.append("期初事件无法验证")
    elif opening.evidence_status == inventory_ledger.EVIDENCE_INSUFFICIENT:
        evidence_status = inventory_ledger.EVIDENCE_INSUFFICIENT
        reasons.append("期初来源证据不足")
    else:
        evidence_status = inventory_ledger.EVIDENCE_TRUSTED

    if evidence_status != "unverifiable":
        unreadable = [
            event
            for event in ordered[1:]
            if event.evidence_status == "unverifiable"
        ]
        if unreadable:
            evidence_status = "unverifiable"
            reasons.append(f"第 {unreadable[0].exec_seq} 步事件无法读取")
        elif evidence_status == inventory_ledger.EVIDENCE_TRUSTED:
            insufficient = [
                event
                for event in ordered[1:]
                if event.evidence_status
                == inventory_ledger.EVIDENCE_INSUFFICIENT
            ]
            if insufficient:
                evidence_status = inventory_ledger.EVIDENCE_INSUFFICIENT
                reasons.append(
                    f"第 {insufficient[0].exec_seq} 步事件证据不足"
                )

    if diff != 0 and evidence_status == inventory_ledger.EVIDENCE_TRUSTED:
        evidence_status = "unverifiable"
        reasons.append(
            f"流水末值 {ledger_end} 与当前库存 {current_stock} 相差 {diff}，"
            "差量无法归属到具体事件"
        )

    affected_start_event_id: int | None = None
    affected_end_event_id: int | None = None
    if anomaly_event is not None:
        affected_start_event_id = anomaly_event.id
        affected_end_event_id = ordered[-1].id
    elif evidence_status != inventory_ledger.EVIDENCE_TRUSTED:
        affected_start_event_id = ordered[-1].id
        affected_end_event_id = ordered[-1].id

    if evidence_status == inventory_ledger.EVIDENCE_TRUSTED and not reasons:
        reasons.append("期初、每一步变动与当前库存相符")

    return Reconciliation(
        opening_stock=opening_stock,
        opening_event_id=opening_event_id,
        opening_source=opening_source,
        ledger_end=ledger_end,
        diff=diff,
        evidence_status=evidence_status,
        evidence_reasons=tuple(reasons),
        first_anomaly_event_id=(
            anomaly_event.id if anomaly_event is not None else None
        ),
        first_anomaly_reason=anomaly_reason,
        affected_start_event_id=affected_start_event_id,
        affected_end_event_id=affected_end_event_id,
    )


def posting_view(
    events: Iterable[Any], cutoff: datetime | None
) -> PostingView:
    ordered = _ordered(events)
    selected = (
        ordered
        if cutoff is None
        else [event for event in ordered if event.posted_at <= cutoff]
    )
    stock = selected[-1].stock_after if selected else None
    return PostingView(events=tuple(selected), stock_at_cutoff=stock)


def business_view(
    events: Iterable[Any], cutoff: datetime | None
) -> BusinessView:
    ordered = _ordered(events)
    opening = _opening_of(ordered)
    if opening is None:
        return BusinessView(opening=None, events=(), stock_at_cutoff=None)

    changes = sorted(
        ordered[1:],
        key=lambda event: (event.business_at, event.exec_seq, event.id),
    )
    running = opening.stock_after
    rows: list[ReorderedEvent] = []
    for event in changes:
        before = running
        running += event.delta
        rows.append(
            ReorderedEvent(event=event, stock_before=before, stock_after=running)
        )

    if cutoff is None:
        return BusinessView(
            opening=opening,
            events=tuple(rows),
            stock_at_cutoff=rows[-1].stock_after if rows else opening.stock_after,
        )

    rows = [row for row in rows if row.event.business_at <= cutoff]
    if not rows and cutoff < opening.business_at:
        return BusinessView(opening=opening, events=(), stock_at_cutoff=None)

    return BusinessView(
        opening=opening,
        events=tuple(rows),
        stock_at_cutoff=rows[-1].stock_after if rows else opening.stock_after,
    )
