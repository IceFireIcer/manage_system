"""补货巡检建议缓存与生成流程。

缓存、失效代次与巡检合并均在单进程内存中维护；多实例部署时各实例独立失效，
跨实例一致需共享缓存或按失效代次重新查询后才能使用。
"""

import concurrent.futures
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import business_time, restock
from app.agent import llm
from app.agent.rules import (
    REPLENISHMENT_FORMULA,
    RULES_VERSION,
    SKIP_CONDITIONS,
    SORT_RULE,
    TIER_RULES,
    WINDOW_DAYS,
    WINDOW_RULE,
    Evaluation,
    evaluate,
)
from app.consumption import local_day_start, window_flow
from app.models import Consumable
from app.schemas_consumables import STATUS_ENABLED
from app.schemas_inventory import (
    RuleSpecOut,
    RuleTierOut,
    SuggestionListOut,
    SuggestionOut,
)

logger = logging.getLogger(__name__)

CACHE_TTL = timedelta(minutes=10)
TIER_ORDER = {"urgent": 0, "moderate": 1, "observe": 2}
LLM_BUDGET_SECONDS = 5.0
MAX_LLM_WORKERS = 8


@dataclass(frozen=True)
class _CacheEntry:
    epoch: int
    version: int
    stock: int
    safety_stock: int
    computed_at: datetime
    suggestion: SuggestionOut


_state_lock = threading.Lock()
_inspection_lock = threading.Lock()
_cache: dict[int, _CacheEntry] = {}
_versions: dict[int, int] = {}
_epoch = 0
_last_inspection_at: datetime | None = None


def clear_cache(consumable_id: int | None = None) -> None:
    global _epoch, _last_inspection_at
    with _state_lock:
        if consumable_id is None:
            _epoch += 1
            _versions.clear()
            _cache.clear()
            _last_inspection_at = None
            return
        _versions[consumable_id] = _versions.get(consumable_id, 0) + 1
        _cache.pop(consumable_id, None)


def window_bounds(now: datetime | None = None) -> tuple[datetime, datetime, datetime]:
    reference = business_time.normalize(now) if now is not None else business_time.now()
    now_local = business_time.normalize(reference)
    q7_start = local_day_start(WINDOW_DAYS - 1, reference)
    prev_start = local_day_start(2 * WINDOW_DAYS - 1, reference)
    return q7_start, prev_start, now_local


def _snapshot() -> tuple[int, dict[int, int]]:
    with _state_lock:
        return _epoch, dict(_versions)


def _version_matches(
    consumable_id: int, epoch: int, versions: dict[int, int]
) -> bool:
    with _state_lock:
        return (
            _epoch == epoch
            and _versions.get(consumable_id, 0) == versions.get(consumable_id, 0)
        )


def _cached(
    consumable: Consumable,
    now: datetime,
    epoch: int,
    versions: dict[int, int],
) -> SuggestionOut | None:
    with _state_lock:
        entry = _cache.get(consumable.id)
        if entry is None:
            return None
        if (
            entry.epoch != epoch
            or entry.version != versions.get(consumable.id, 0)
            or entry.stock != consumable.current_stock
            or entry.safety_stock != consumable.safety_stock
            or now - entry.computed_at >= CACHE_TTL
        ):
            _cache.pop(consumable.id, None)
            return None
        return entry.suggestion


def _store(
    consumable: Consumable,
    epoch: int,
    versions: dict[int, int],
    suggestion: SuggestionOut,
    computed_at: datetime,
) -> None:
    with _state_lock:
        if _epoch != epoch:
            return
        version = _versions.get(consumable.id, 0)
        if version != versions.get(consumable.id, 0):
            return
        _cache[consumable.id] = _CacheEntry(
            epoch=epoch,
            version=version,
            stock=consumable.current_stock,
            safety_stock=consumable.safety_stock,
            computed_at=computed_at,
            suggestion=suggestion,
        )


def _fallback(
    context: llm.RationaleContext, degraded: bool
) -> llm.RationaleResult:
    return llm.RationaleResult(
        llm.format_template(context), llm.RATIONALE_SOURCE_RULES, degraded
    )


def _generate_rationales(
    contexts: list[llm.RationaleContext],
) -> list[llm.RationaleResult]:
    if not contexts:
        return []
    if len(contexts) == 1:
        return [llm.generate_rationale_result(contexts[0])]

    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=min(len(contexts), MAX_LLM_WORKERS)
    )
    try:
        futures = [
            executor.submit(llm.generate_rationale_result, context)
            for context in contexts
        ]
        done, _ = concurrent.futures.wait(futures, timeout=LLM_BUDGET_SECONDS)
        results: list[llm.RationaleResult] = []
        for future, context in zip(futures, contexts, strict=True):
            if future in done:
                try:
                    results.append(future.result())
                except Exception:
                    results.append(_fallback(context, True))
            else:
                results.append(_fallback(context, True))
        return results
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _build_suggestion(
    consumable: Consumable,
    evaluation: Evaluation,
    result: llm.RationaleResult,
    computed_at: datetime,
) -> SuggestionOut:
    return SuggestionOut(
        consumable_id=consumable.id,
        name=consumable.name,
        spec=consumable.spec,
        unit=consumable.unit,
        current_stock=consumable.current_stock,
        safety_stock=consumable.safety_stock,
        gap=evaluation.gap,
        q7=evaluation.q7,
        rate=evaluation.rate,
        days_of_supply=evaluation.days_of_supply,
        tier=evaluation.tier,
        recommended_quantity=evaluation.recommended_quantity,
        reasons=list(evaluation.reasons),
        consumption_state=evaluation.consumption_state,
        consumption_note=evaluation.consumption_note,
        rationale=result.text,
        rationale_source=result.source,
        rationale_degraded=result.degraded,
        computed_at=computed_at,
    )


def rule_spec() -> RuleSpecOut:
    return RuleSpecOut(
        version=RULES_VERSION,
        window_days=WINDOW_DAYS,
        window_rule=WINDOW_RULE,
        tiers=[RuleTierOut(**tier) for tier in TIER_RULES],
        formula=REPLENISHMENT_FORMULA,
        skip_conditions=list(SKIP_CONDITIONS),
        sort_rule=SORT_RULE,
    )


def _suggestion_sort_key(item: SuggestionOut) -> tuple:
    supply = item.days_of_supply
    return (
        TIER_ORDER[item.tier],
        (supply is None, supply if supply is not None else 0.0),
        -item.recommended_quantity,
        item.name,
    )


def inspect(db: Session, now: datetime | None = None) -> SuggestionListOut:
    with _inspection_lock:
        return _inspect(db, now)


def _inspect(db: Session, now: datetime | None) -> SuggestionListOut:
    global _last_inspection_at
    reference = business_time.normalize(now) if now is not None else business_time.now()
    now_local = business_time.normalize(reference)
    q7_start, prev_start, window_end = window_bounds(reference)
    epoch, versions = _snapshot()

    consumables = db.scalars(
        select(Consumable)
        .where(Consumable.status == STATUS_ENABLED)
        .order_by(Consumable.id)
    ).all()

    items: list[SuggestionOut] = []
    pending: list[tuple[Consumable, Evaluation]] = []
    for consumable in consumables:
        suggestion = _cached(consumable, now_local, epoch, versions)
        if suggestion is not None:
            items.append(suggestion)
            continue
        q7_flow = window_flow(db, consumable.id, q7_start, window_end)
        prev_flow = window_flow(
            db, consumable.id, prev_start, q7_start, end_inclusive=False
        )
        evaluation = evaluate(
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
        pending.append((consumable, evaluation))

    computed = bool(pending)
    if pending:
        contexts = [
            llm.RationaleContext(
                name=consumable.name,
                unit=consumable.unit,
                evaluation=evaluation,
            )
            for consumable, evaluation in pending
        ]
        results = _generate_rationales(contexts)
        for (consumable, evaluation), context, result in zip(
            pending, contexts, results, strict=True
        ):
            if not _version_matches(consumable.id, epoch, versions):
                result = _fallback(context, True)
            suggestion = _build_suggestion(
                consumable, evaluation, result, now_local
            )
            if _version_matches(consumable.id, epoch, versions):
                _store(consumable, epoch, versions, suggestion, now_local)
            items.append(suggestion)

    with _state_lock:
        if computed or _last_inspection_at is None:
            _last_inspection_at = now_local
        generated_at = _last_inspection_at

    items.sort(key=_suggestion_sort_key)
    return SuggestionListOut(
        generated_at=generated_at,
        generation=epoch,
        rules=rule_spec(),
        items=restock.attach_dispositions(db, items),
    )
