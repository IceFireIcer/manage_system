import concurrent.futures
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import business_time, restock
from app.agent import llm
from app.agent.rules import WINDOW_DAYS, Evaluation, evaluate
from app.consumption import local_day_start, window_net
from app.models import Consumable
from app.schemas_consumables import STATUS_ENABLED
from app.schemas_inventory import SuggestionListOut, SuggestionOut

logger = logging.getLogger(__name__)

CACHE_TTL = timedelta(minutes=10)
TIER_ORDER = {"urgent": 0, "moderate": 1, "observe": 2}
LLM_BUDGET_SECONDS = 5.0
MAX_LLM_WORKERS = 8

_cache: dict[int, tuple[datetime, SuggestionOut]] = {}
_last_inspection_at: datetime | None = None


def clear_cache(consumable_id: int | None = None) -> None:
    global _last_inspection_at
    if consumable_id is None:
        _cache.clear()
        _last_inspection_at = None
        return
    _cache.pop(consumable_id, None)


def window_bounds(now: datetime | None = None) -> tuple[datetime, datetime, datetime]:
    reference = business_time.normalize(now) if now is not None else business_time.now()
    now_local = business_time.normalize(reference)
    q7_start = local_day_start(WINDOW_DAYS - 1, reference)
    prev_start = local_day_start(2 * WINDOW_DAYS - 1, reference)
    return q7_start, prev_start, now_local


def _cached(consumable_id: int, now: datetime) -> SuggestionOut | None:
    entry = _cache.get(consumable_id)
    if entry is None:
        return None
    cached_at, suggestion = entry
    if now - cached_at >= CACHE_TTL:
        _cache.pop(consumable_id, None)
        return None
    return suggestion


def _generate_rationales(contexts: list[llm.RationaleContext]) -> list[str]:
    if not contexts:
        return []
    if len(contexts) == 1:
        return [llm.generate_rationale(contexts[0])]

    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=min(len(contexts), MAX_LLM_WORKERS)
    )
    try:
        futures = [executor.submit(llm.generate_rationale, context) for context in contexts]
        done, _ = concurrent.futures.wait(futures, timeout=LLM_BUDGET_SECONDS)
        rationales: list[str] = []
        for future, context in zip(futures, contexts, strict=True):
            if future in done:
                try:
                    rationales.append(future.result())
                except Exception:
                    rationales.append(llm.format_template(context))
            else:
                rationales.append(llm.format_template(context))
        return rationales
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _build_suggestion(
    consumable: Consumable, evaluation: Evaluation, rationale: str
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
        rationale=rationale,
    )


def inspect(db: Session, now: datetime | None = None) -> SuggestionListOut:
    global _last_inspection_at
    reference = business_time.normalize(now) if now is not None else business_time.now()
    now_local = business_time.normalize(reference)
    q7_start, prev_start, window_end = window_bounds(reference)

    consumables = db.scalars(
        select(Consumable)
        .where(Consumable.status == STATUS_ENABLED)
        .order_by(Consumable.id)
    ).all()

    items: list[SuggestionOut] = []
    pending: list[tuple[Consumable, Evaluation]] = []
    for consumable in consumables:
        suggestion = _cached(consumable.id, now_local)
        if suggestion is not None:
            items.append(suggestion)
            continue
        evaluation = evaluate(
            current_stock=consumable.current_stock,
            safety_stock=consumable.safety_stock,
            q7=window_net(db, consumable.id, q7_start, window_end),
            prev_q7=window_net(db, consumable.id, prev_start, q7_start),
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
        rationales = _generate_rationales(contexts)
        for (consumable, evaluation), rationale in zip(pending, rationales, strict=True):
            suggestion = _build_suggestion(consumable, evaluation, rationale)
            _cache[consumable.id] = (now_local, suggestion)
            items.append(suggestion)

    if computed or _last_inspection_at is None:
        _last_inspection_at = now_local

    items.sort(
        key=lambda item: (TIER_ORDER[item.tier], -item.recommended_quantity, item.name)
    )
    return SuggestionListOut(
        generated_at=_last_inspection_at,
        items=restock.attach_dispositions(db, items),
    )
