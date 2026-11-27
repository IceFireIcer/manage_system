from datetime import datetime, timedelta

import pytest
from app import business_time
from app.agent import llm, rules, service
from app.consumption import window_flow
from app.models import Consumable, Requisition, User
from sqlalchemy import select


def _student_id(db_session) -> int:
    return db_session.scalars(select(User).where(User.account == "student01")).one().id


def _consumable(db_session, **overrides) -> Consumable:
    values = {
        "name": "焊丝",
        "spec": "φ1.2mm",
        "unit": "卷",
        "location": "一号柜",
        "safety_stock": 5,
        "current_stock": 10,
        "status": "enabled",
    }
    values.update(overrides)
    consumable = Consumable(**values)
    db_session.add(consumable)
    db_session.commit()
    return consumable


def _requisition(
    db_session,
    consumable_id: int,
    quantity: int,
    created_at: datetime,
    *,
    req_type: str = "normal",
    status: str = "active",
) -> None:
    db_session.add(
        Requisition(
            consumable_id=consumable_id,
            user_id=_student_id(db_session),
            quantity=quantity,
            stock_after=0,
            type=req_type,
            status=status,
            created_at=created_at,
        )
    )
    db_session.commit()


def test_window_boundary_midnight_record_counted_once(db_session) -> None:
    reference = datetime(2026, 5, 14, 12, 0, 0)
    q7_start, prev_start, window_end = service.window_bounds(reference)
    consumable = _consumable(db_session)

    _requisition(db_session, consumable.id, 5, prev_start)
    _requisition(db_session, consumable.id, 7, q7_start)
    _requisition(db_session, consumable.id, 3, q7_start + timedelta(hours=1))

    prev_flow = window_flow(
        db_session, consumable.id, prev_start, q7_start, end_inclusive=False
    )
    q7_flow = window_flow(db_session, consumable.id, q7_start, window_end)

    assert prev_flow.issued == 5
    assert prev_flow.records == 1
    assert q7_flow.issued == 10
    assert q7_flow.records == 2
    assert prev_flow.issued + q7_flow.issued == 15


def test_window_boundary_record_not_double_counted_with_inclusive_end(db_session) -> None:
    reference = datetime(2026, 5, 14, 12, 0, 0)
    q7_start, prev_start, window_end = service.window_bounds(reference)
    consumable = _consumable(db_session)
    _requisition(db_session, consumable.id, 7, q7_start)

    q7_flow = window_flow(db_session, consumable.id, q7_start, window_end)
    prev_flow = window_flow(
        db_session, consumable.id, prev_start, q7_start, end_inclusive=False
    )

    assert q7_flow.records == 1
    assert prev_flow.records == 0


@pytest.mark.parametrize(
    ("current_stock", "safety_stock", "q7", "tier", "expected"),
    [
        (10, 5, 29, "observe", 29),
        (0, 5, 29, "urgent", 34),
        (4, 10, 29, "urgent", 35),
        (10, 10, 0, None, 0),
    ],
)
def test_replenishment_quantity_uses_integer_net(
    current_stock: int, safety_stock: int, q7: int, tier: str | None, expected: int
) -> None:
    assert (
        rules.compute_replenishment_quantity(current_stock, safety_stock, q7, tier)
        == expected
    )


def test_net_consumption_29_does_not_round_up() -> None:
    evaluation = rules.evaluate(10, 5, 29, 1)
    assert evaluation is not None
    assert evaluation.recommended_quantity == 29
    assert evaluation.rate == 4.14


def test_trend_flat_but_one_day_supply_marks_supply_reason() -> None:
    evaluation = rules.evaluate(10, 5, 70, 70)
    assert evaluation is not None
    assert evaluation.tier == rules.TIER_URGENT
    assert evaluation.reasons == (rules.REASON_SUPPLY_DAYS_ONE,)
    assert "支撑天数触发" in llm.format_template(
        llm.RationaleContext(name="焊丝", unit="卷", evaluation=evaluation)
    )


def test_zero_consumption_states_are_distinguishable() -> None:
    none_state = rules.classify_consumption(0, 0, 0, 0)
    reversed_state = rules.classify_consumption(0, 12, 12, 2)
    partial_state = rules.classify_consumption(5, 15, 10, 2)

    assert none_state[0] == rules.CONSUMPTION_NONE
    assert reversed_state[0] == rules.CONSUMPTION_REVERSED_TO_ZERO
    assert partial_state[0] == rules.CONSUMPTION_PARTIALLY_REVERSED
    assert len({none_state[1], reversed_state[1], partial_state[1]}) == 3
    assert "无领用记录" in none_state[1]
    assert "已全部红冲" in reversed_state[1]
    assert "被红冲" in partial_state[1]


def test_rule_spec_uses_backend_version() -> None:
    spec = service.rule_spec()
    assert spec.version == rules.RULES_VERSION
    assert spec.window_days == rules.WINDOW_DAYS
    assert [tier.code for tier in spec.tiers] == [
        rules.TIER_URGENT,
        rules.TIER_MODERATE,
        rules.TIER_OBSERVE,
    ]
    assert spec.skip_conditions == list(rules.SKIP_CONDITIONS)


def test_suggestions_payload_includes_reasons_and_rule_spec(
    admin_client, db_session
) -> None:
    consumable = _consumable(
        db_session, name="砂纸", safety_stock=5, current_stock=10
    )
    _requisition(
        db_session,
        consumable.id,
        70,
        business_time.now().replace(microsecond=0),
    )

    body = admin_client.get("/api/v1/inventory/suggestions").json()
    assert body["rules"]["version"] == rules.RULES_VERSION
    item = next(entry for entry in body["items"] if entry["name"] == "砂纸")
    assert item["tier"] == rules.TIER_URGENT
    assert item["reasons"] == [rules.REASON_SUPPLY_DAYS_ONE]
    assert item["recommended_quantity"] == 70
    assert "支撑天数触发" in item["rationale"]
