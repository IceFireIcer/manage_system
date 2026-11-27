import pytest
from app.agent.rules import (
    compute_replenishment_quantity,
    compute_tier,
    evaluate,
    net_consumption,
)


@pytest.mark.parametrize(
    ("current_stock", "safety_stock", "q7", "qprev7"),
    [
        (0, 10, 0, 0),
        (2, 10, 0, 0),
        (7, 10, 49, 0),
        (1, 10, 70, 0),
    ],
)
def test_urgent_tier(
    current_stock: int, safety_stock: int, q7: int, qprev7: int
) -> None:
    assert compute_tier(current_stock, safety_stock, q7, qprev7) == "urgent"


@pytest.mark.parametrize(
    ("current_stock", "safety_stock", "q7", "qprev7"),
    [
        (6, 10, 14, 0),
        (5, 10, 21, 0),
        (4, 10, 12, 0),
    ],
)
def test_moderate_tier(
    current_stock: int, safety_stock: int, q7: int, qprev7: int
) -> None:
    assert compute_tier(current_stock, safety_stock, q7, qprev7) == "moderate"


@pytest.mark.parametrize(
    ("current_stock", "safety_stock", "q7", "qprev7"),
    [
        (31, 40, 70, 0),
        (4, 10, 1, 0),
        (5, 10, 0, 0),
        (10, 5, 15, 10),
        (10, 5, 1, 0),
    ],
)
def test_observe_tier(
    current_stock: int, safety_stock: int, q7: int, qprev7: int
) -> None:
    assert compute_tier(current_stock, safety_stock, q7, qprev7) == "observe"


@pytest.mark.parametrize(
    ("current_stock", "safety_stock", "q7", "qprev7"),
    [
        (10, 10, 0, 0),
        (10, 5, 14, 10),
        (10, 5, 0, 0),
        (10, 0, 0, 0),
    ],
)
def test_normal_or_skipped_tier(
    current_stock: int, safety_stock: int, q7: int, qprev7: int
) -> None:
    assert compute_tier(current_stock, safety_stock, q7, qprev7) is None


def test_safety_stock_zero_is_skipped() -> None:
    assert compute_tier(0, 0, 10, 5) is None
    assert evaluate(0, 0, 10, 5) is None


def test_thirty_percent_boundary_is_not_urgent() -> None:
    assert compute_tier(3, 10, 0, 0) == "observe"


def test_days_of_supply_exactly_one_is_urgent() -> None:
    assert compute_tier(7, 10, 49, 0) == "urgent"


def test_days_of_supply_exactly_three_is_moderate() -> None:
    assert compute_tier(6, 10, 14, 0) == "moderate"


def test_days_of_supply_above_three_is_observe() -> None:
    assert compute_tier(31, 40, 70, 0) == "observe"


def test_current_stock_equal_safety_stock_is_normal() -> None:
    assert compute_tier(10, 10, 0, 0) is None


def test_trend_exactly_fifty_percent_is_observe() -> None:
    assert compute_tier(10, 5, 15, 10) == "observe"
    assert compute_tier(10, 5, 14, 10) is None


def test_trend_from_zero_baseline() -> None:
    assert compute_tier(10, 5, 1, 0) == "observe"
    assert compute_tier(10, 5, 0, 0) is None


def test_rate_zero_with_low_stock_is_observe() -> None:
    assert compute_tier(5, 10, 0, 0) == "observe"


@pytest.mark.parametrize(
    ("current_stock", "safety_stock", "q7", "tier", "expected"),
    [
        (5, 10, 8, "observe", 13),
        (2, 10, 0, "observe", 8),
        (10, 5, 15, "observe", 15),
        (0, 5, 0, "urgent", 5),
        (4, 10, 3, "observe", 9),
        (1, 10, 9, "urgent", 18),
        (10, 10, 0, None, 0),
    ],
)
def test_replenishment_quantity(
    current_stock: int, safety_stock: int, q7: int, tier: str | None, expected: int
) -> None:
    assert (
        compute_replenishment_quantity(current_stock, safety_stock, q7, tier) == expected
    )


def test_net_consumption_truncates_negative() -> None:
    assert net_consumption(10, 4) == 6
    assert net_consumption(2, 5) == 0
    assert net_consumption(0, 0) == 0
    assert net_consumption(7, 7) == 0


def test_evaluate_outputs_metrics() -> None:
    result = evaluate(3, 10, 8, 0)
    assert result is not None
    assert result.tier == "moderate"
    assert result.gap == 7
    assert result.q7 == 8
    assert result.rate == 1.14
    assert result.days_of_supply == 2.6
    assert result.recommended_quantity == 15


def test_evaluate_infinite_days_of_supply() -> None:
    result = evaluate(5, 10, 0, 0)
    assert result is not None
    assert result.days_of_supply is None
    assert result.rate == 0
    assert result.tier == "observe"
    assert result.recommended_quantity == 5
