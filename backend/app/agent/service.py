"""巡检服务：读取窗口净消耗、调用规则引擎与润色适配层，按耗材缓存 10 分钟。"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import llm
from app.agent.rules import WINDOW_DAYS, Evaluation, evaluate
from app.consumption import local_day_start, window_net
from app.models import Consumable
from app.schemas_consumables import STATUS_ENABLED
from app.schemas_inventory import SuggestionListOut, SuggestionOut

logger = logging.getLogger(__name__)

SHANGHAI_TZ = timezone(timedelta(hours=8))
CACHE_TTL = timedelta(minutes=10)
TIER_ORDER = {"urgent": 0, "moderate": 1, "observe": 2}

_cache: dict[int, tuple[datetime, SuggestionOut]] = {}
_last_inspection_at: datetime | None = None


def clear_cache(consumable_id: int | None = None) -> None:
    """清除巡检缓存；传入耗材 id 时仅清除该条目。"""
    global _last_inspection_at
    if consumable_id is None:
        _cache.clear()
        _last_inspection_at = None
        return
    _cache.pop(consumable_id, None)



def window_bounds(now: datetime | None = None) -> tuple[datetime, datetime, datetime]:
    """返回 (近 7 天窗口起点, 再前 7 天窗口起点, 当前本地时间)。"""
    reference = now or datetime.now(SHANGHAI_TZ)
    now_local = reference.astimezone().replace(tzinfo=None)
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


def _build_suggestion(consumable: Consumable, evaluation: Evaluation) -> SuggestionOut:
    rationale = llm.generate_rationale(
        llm.RationaleContext(
            name=consumable.name,
            unit=consumable.unit,
            evaluation=evaluation,
        )
    )
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
    """逐类巡检启用耗材：命中缓存则复用，否则重算并润色。"""
    global _last_inspection_at
    reference = now or datetime.now(SHANGHAI_TZ)
    now_local = reference.astimezone().replace(tzinfo=None)
    q7_start, prev_start, window_end = window_bounds(reference)

    consumables = db.scalars(
        select(Consumable)
        .where(Consumable.status == STATUS_ENABLED)
        .order_by(Consumable.id)
    ).all()

    items: list[SuggestionOut] = []
    computed = False
    for consumable in consumables:
        suggestion = _cached(consumable.id, now_local)
        if suggestion is None:
            evaluation = evaluate(
                current_stock=consumable.current_stock,
                safety_stock=consumable.safety_stock,
                q7=window_net(db, consumable.id, q7_start, window_end),
                prev_q7=window_net(db, consumable.id, prev_start, q7_start),
            )
            if evaluation is None:
                continue
            suggestion = _build_suggestion(consumable, evaluation)
            _cache[consumable.id] = (now_local, suggestion)
            computed = True
        items.append(suggestion)

    if computed or _last_inspection_at is None:
        _last_inspection_at = now_local

    items.sort(
        key=lambda item: (TIER_ORDER[item.tier], -item.recommended_quantity, item.name)
    )
    return SuggestionListOut(generated_at=_last_inspection_at, items=items)
