"""补货巡检建议缓存与生成流程。

缓存、失效代次与巡检合并均在单进程内存中维护；多实例部署时各实例独立失效，
跨实例一致需共享缓存或按失效代次重新查询后才能使用。
"""

import concurrent.futures
import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta

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
from app.models import (
    Consumable,
    Course,
    CourseRecipeItem,
    CourseSession,
)
from app.schemas_consumables import STATUS_ENABLED
from app.schemas_inventory import (
    RuleSpecOut,
    RuleTierOut,
    SuggestionListOut,
    SuggestionOut,
    TeachingContextOut,
    TeachingSessionOut,
)

logger = logging.getLogger(__name__)

CACHE_TTL = timedelta(minutes=10)
TIER_ORDER = {"urgent": 0, "moderate": 1, "observe": 2}
LLM_BUDGET_SECONDS = 5.0
MAX_LLM_WORKERS = 8
MAX_TEACHING_SESSIONS = 3


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
    context: llm.RationaleContext | llm.TeachingConsequenceContext,
    degraded: bool,
) -> llm.RationaleResult:
    if isinstance(context, llm.TeachingConsequenceContext):
        text = llm.format_teaching_template(context)
    else:
        text = llm.format_template(context)
    return llm.RationaleResult(text, llm.RATIONALE_SOURCE_RULES, degraded)


def _generate_outputs(
    pairs: list[tuple[llm.RationaleContext, llm.TeachingConsequenceContext]],
    rationale_generator: llm.RationaleGenerator | None = None,
    teaching_generator: llm.RationaleGenerator | None = None,
) -> list[tuple[llm.RationaleResult, llm.RationaleResult]]:
    if not pairs:
        return []
    if len(pairs) == 1:
        rationale_context, teaching_context = pairs[0]
        return [
            (
                llm.generate_rationale_result(rationale_context, rationale_generator),
                llm.generate_teaching_consequence_result(
                    teaching_context, teaching_generator
                ),
            )
        ]

    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=min(len(pairs) * 2, MAX_LLM_WORKERS)
    )
    try:
        jobs = []
        for index, (rationale_context, teaching_context) in enumerate(pairs):
            jobs.append(
                (
                    index,
                    0,
                    rationale_context,
                    executor.submit(
                        llm.generate_rationale_result,
                        rationale_context,
                        rationale_generator,
                    ),
                )
            )
            jobs.append(
                (
                    index,
                    1,
                    teaching_context,
                    executor.submit(
                        llm.generate_teaching_consequence_result,
                        teaching_context,
                        teaching_generator,
                    ),
                )
            )
        done, _ = concurrent.futures.wait(
            [job[3] for job in jobs], timeout=LLM_BUDGET_SECONDS
        )
        results: list[list[llm.RationaleResult | None]] = [
            [None, None] for _ in pairs
        ]
        for index, slot, context, future in jobs:
            if future in done:
                try:
                    results[index][slot] = future.result()
                except Exception:
                    results[index][slot] = _fallback(context, True)
            else:
                results[index][slot] = _fallback(context, True)
        return [
            (rationale, teaching)
            for rationale, teaching in results
            if rationale is not None and teaching is not None
        ]
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _teaching_contexts(
    db: Session,
    consumables: list[Consumable],
    today: date,
) -> dict[int, tuple[TeachingContextOut, llm.TeachingConsequenceContext]]:
    if not consumables:
        return {}
    consumable_ids = [consumable.id for consumable in consumables]
    rows = db.execute(
        select(CourseSession, Course, CourseRecipeItem)
        .join(Course, Course.id == CourseSession.course_id)
        .join(
            CourseRecipeItem,
            (CourseRecipeItem.snapshot_id == CourseSession.recipe_snapshot_id)
            & (CourseRecipeItem.consumable_id.in_(consumable_ids)),
        )
        .where(
            CourseSession.status == "planned",
            CourseSession.session_date >= today,
            CourseSession.class_id == Course.class_id,
            Course.status == "enabled",
        )
        .order_by(
            CourseSession.session_date,
            CourseSession.seq,
            CourseSession.id,
        )
    ).all()

    grouped: dict[int, list[tuple[CourseSession, Course, CourseRecipeItem]]] = {}
    for session, course, item in rows:
        grouped.setdefault(item.consumable_id, []).append((session, course, item))

    contexts: dict[int, tuple[TeachingContextOut, llm.TeachingConsequenceContext]] = {}
    for consumable in consumables:
        records = grouped.get(consumable.id, [])
        headcount_total = sum(max(session.headcount, 0) for session, _, _ in records)
        expected_demand = sum(
            item.quantity_per_person * max(session.headcount, 0)
            for session, _, item in records
        )
        expected_gap = max(expected_demand - consumable.current_stock, 0)
        llm_context = llm.TeachingConsequenceContext(
            name=consumable.name,
            unit=consumable.unit,
            unlinked=not records,
            session_count=len(records),
            headcount_total=headcount_total,
            expected_demand=expected_demand,
            expected_gap=expected_gap,
        )
        teaching = TeachingContextOut(
            unlinked=not records,
            sessions=[
                TeachingSessionOut(
                    course_id=course.id,
                    course_name=course.name,
                    session_id=session.id,
                    session_seq=session.seq,
                    session_date=session.session_date,
                    headcount=session.headcount,
                    required_quantity=item.quantity_per_person
                    * max(session.headcount, 0),
                )
                for session, course, item in records[:MAX_TEACHING_SESSIONS]
            ],
            session_count=len(records),
            headcount_total=headcount_total,
            expected_demand=expected_demand,
            expected_gap=expected_gap,
            consequence=llm.format_teaching_template(llm_context),
        )
        contexts[consumable.id] = (teaching, llm_context)
    return contexts


def _build_suggestion(
    consumable: Consumable,
    evaluation: Evaluation,
    result: llm.RationaleResult,
    teaching_context: TeachingContextOut,
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
        teaching_context=teaching_context,
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
        teaching_map = _teaching_contexts(
            db, [consumable for consumable, _ in pending], now_local.date()
        )
        pairs = [
            (
                llm.RationaleContext(
                    name=consumable.name,
                    unit=consumable.unit,
                    evaluation=evaluation,
                ),
                teaching_map[consumable.id][1],
            )
            for consumable, evaluation in pending
        ]
        results = _generate_outputs(
            pairs,
            llm.resolve_generator(db, llm.FEATURE_RESTOCK_RATIONALE),
            llm.resolve_generator(db, llm.FEATURE_TEACHING_CONSEQUENCE),
        )
        for index, (consumable, evaluation) in enumerate(pending):
            rationale, teaching = results[index]
            rationale_context = pairs[index][0]
            teaching_schema = teaching_map[consumable.id][0]
            if not _version_matches(consumable.id, epoch, versions):
                rationale = _fallback(rationale_context, True)
            suggestion = _build_suggestion(
                consumable,
                evaluation,
                rationale,
                teaching_schema.model_copy(
                    update={
                        "consequence": teaching.text,
                        "consequence_source": teaching.source,
                        "consequence_degraded": teaching.degraded,
                    }
                ),
                now_local,
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
