"""演示数据脚本（T11）的幂等性与三级场景测试。"""

from app.agent import service
from app.consumption import audit_events, reconstruct_stock, window_net
from app.models import Consumable, Requisition
from scripts.seed_demo import DEMO_MARKER, DEMO_TARGETS, seed_demo
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _stocks(db: Session) -> dict[str, int]:
    rows = db.scalars(
        select(Consumable).where(Consumable.name.in_(tuple(DEMO_TARGETS)))
    ).all()
    return {row.name: row.current_stock for row in rows}


def _flow_count(db: Session) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Requisition)
            .where(Requisition.remark == DEMO_MARKER)
        )
        or 0
    )


def test_seed_creates_exactly_three_tiered_suggestions(db_session: Session) -> None:
    result = seed_demo(db_session)

    assert result.seeded is True
    assert result.requisitions == 20
    assert _flow_count(db_session) == 20
    assert _stocks(db_session) == DEMO_TARGETS

    service.clear_cache()
    suggestions = service.inspect(db_session)

    assert [item.name for item in suggestions.items] == ["焊丝", "记号笔", "电阻"]
    assert [item.tier for item in suggestions.items] == ["urgent", "moderate", "observe"]
    assert [item.recommended_quantity for item in suggestions.items] == [12, 12, 100]
    assert all(item.rationale for item in suggestions.items)
    assert {"砂纸", "实训导线"}.isdisjoint({item.name for item in suggestions.items})


def test_seed_is_idempotent(db_session: Session) -> None:
    first = seed_demo(db_session)
    stocks_after_first = _stocks(db_session)

    second = seed_demo(db_session)

    assert first.seeded is True
    assert second.seeded is False
    assert second.requisitions == 0
    assert _flow_count(db_session) == 20
    assert _stocks(db_session) == stocks_after_first


def test_seed_stock_after_matches_inventory(db_session: Session) -> None:
    seed_demo(db_session)

    consumables = db_session.scalars(
        select(Consumable).where(Consumable.name.in_(tuple(DEMO_TARGETS)))
    ).all()
    for consumable in consumables:
        events = audit_events(db_session, consumable.id)
        assert events, consumable.name
        assert reconstruct_stock(events).consistent is True

    q7_start, prev_start, window_end = service.window_bounds()
    nets = {
        consumable.name: (
            window_net(db_session, consumable.id, q7_start, window_end),
            window_net(db_session, consumable.id, prev_start, q7_start),
        )
        for consumable in consumables
    }
    assert nets["焊丝"] == (8, 4)
    assert nets["记号笔"] == (7, 2)
    assert nets["电阻"] == (100, 40)
    assert nets["砂纸"] == (0, 0)
    assert nets["实训导线"] == (0, 0)
