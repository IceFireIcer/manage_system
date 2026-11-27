from dataclasses import dataclass

TIER_URGENT = "urgent"
TIER_MODERATE = "moderate"
TIER_OBSERVE = "observe"

WINDOW_DAYS = 7
URGENT_STOCK_RATIO = 0.3
TREND_RATIO = 1.5

RULES_VERSION = "restock-r1"

REASON_OUT_OF_STOCK = "库存为零，已断供"
REASON_BELOW_RATIO = "库存低于安全线 30%"
REASON_SUPPLY_DAYS_ONE = "可支撑天数不足 1 天（支撑天数触发）"
REASON_BELOW_SAFETY = "库存低于安全线"
REASON_SUPPLY_DAYS_THREE = "可支撑天数不足 3 天"
REASON_RISING = "近 7 天消耗较前 7 天上升"
REASON_SLOW_CONSUMPTION = "消耗较慢，可支撑天数超过 3 天"
REASON_NO_CONSUMPTION = "近 7 天无净消耗，暂按库存缺口观察"

CONSUMPTION_NONE = "none"
CONSUMPTION_REVERSED_TO_ZERO = "reversed_to_zero"
CONSUMPTION_PARTIALLY_REVERSED = "partially_reversed"
CONSUMPTION_NORMAL = "normal"

TIER_RULES = (
    {
        "code": TIER_URGENT,
        "label": "紧急",
        "condition": "库存为零，或库存低于安全线 30%，或可支撑天数不超过 1 天",
    },
    {
        "code": TIER_MODERATE,
        "label": "一般",
        "condition": "库存低于安全线，且可支撑天数不超过 3 天",
    },
    {
        "code": TIER_OBSERVE,
        "label": "观察",
        "condition": (
            "库存低于安全线但可支撑天数超过 3 天；"
            "或库存达标但近 7 天消耗较前 7 天上升 50% 及以上"
        ),
    },
)

WINDOW_RULE = (
    "近 7 天窗口为分界日 00:00 至当前；前 7 天窗口为十四天前 00:00 至分界日 00:00（不含），"
    "分界点的领用记录只计入一次。"
)
REPLENISHMENT_FORMULA = (
    "建议补货量 = 库存缺口 + 近 7 天净消耗，按整数单位计；库存不低于安全线时缺口按 0 计。"
)
SKIP_CONDITIONS = (
    "停用耗材不参与巡检",
    "安全库存线为 0 的耗材不生成补货建议",
    "库存不低于安全线且近 7 天消耗未上升时不生成补货建议",
)


@dataclass(frozen=True)
class Evaluation:

    current_stock: int
    safety_stock: int
    q7: int
    prev_q7: int
    gap: int
    rate: float
    days_of_supply: float | None
    tier: str
    recommended_quantity: int
    reasons: tuple[str, ...]
    consumption_state: str
    consumption_note: str


def net_consumption(issued: int, reversed_quantity: int) -> int:
    return max(issued - reversed_quantity, 0)


def compute_rate(q7: int) -> float:
    return q7 / WINDOW_DAYS


def compute_gap(safety_stock: int, current_stock: int) -> int:
    return max(safety_stock - current_stock, 0)


def compute_days_of_supply(current_stock: int, rate: float) -> float | None:
    if rate <= 0:
        return None
    return current_stock / rate


def _is_rising(q7: int, prev_q7: int) -> bool:
    if q7 <= 0:
        return False
    if prev_q7 <= 0:
        return True
    return 2 * q7 >= 3 * prev_q7


def classify_tier(
    current_stock: int, safety_stock: int, q7: int, prev_q7: int
) -> tuple[str | None, tuple[str, ...]]:
    if safety_stock <= 0:
        return None, ()

    out_of_stock = current_stock == 0
    below_ratio = current_stock * 10 < safety_stock * 3
    supply_days_one = q7 > 0 and current_stock * WINDOW_DAYS <= q7

    if out_of_stock or below_ratio or supply_days_one:
        reasons: list[str] = []
        if out_of_stock:
            reasons.append(REASON_OUT_OF_STOCK)
        elif below_ratio:
            reasons.append(REASON_BELOW_RATIO)
        elif current_stock < safety_stock:
            reasons.append(REASON_BELOW_SAFETY)
        if supply_days_one and not out_of_stock:
            reasons.append(REASON_SUPPLY_DAYS_ONE)
        return TIER_URGENT, tuple(reasons)

    if current_stock < safety_stock:
        if q7 > 0 and current_stock * WINDOW_DAYS <= 3 * q7:
            return TIER_MODERATE, (REASON_BELOW_SAFETY, REASON_SUPPLY_DAYS_THREE)
        reason = REASON_NO_CONSUMPTION if q7 == 0 else REASON_SLOW_CONSUMPTION
        return TIER_OBSERVE, (REASON_BELOW_SAFETY, reason)

    if _is_rising(q7, prev_q7):
        return TIER_OBSERVE, (REASON_RISING,)
    return None, ()


def compute_tier(
    current_stock: int, safety_stock: int, q7: int, prev_q7: int
) -> str | None:
    return classify_tier(current_stock, safety_stock, q7, prev_q7)[0]


def compute_replenishment_quantity(
    current_stock: int, safety_stock: int, q7: int, tier: str | None
) -> int:
    if tier is None:
        return 0
    return compute_gap(safety_stock, current_stock) + max(q7, 0)


def classify_consumption(
    q7: int,
    issued: int | None,
    reversed_quantity: int | None,
    records: int | None,
) -> tuple[str, str]:
    issued_quantity = q7 if issued is None else issued
    reversed_sum = 0 if reversed_quantity is None else reversed_quantity
    record_count = (1 if q7 > 0 else 0) if records is None else records

    if record_count == 0 or (issued_quantity == 0 and reversed_sum == 0):
        return CONSUMPTION_NONE, "近 7 天无领用记录"
    if reversed_sum > 0 and reversed_sum >= issued_quantity:
        return (
            CONSUMPTION_REVERSED_TO_ZERO,
            f"近 7 天领用 {issued_quantity}，已全部红冲，净消耗为零",
        )
    if reversed_sum > 0:
        return (
            CONSUMPTION_PARTIALLY_REVERSED,
            f"近 7 天领用 {issued_quantity}，其中 {reversed_sum} 被红冲，"
            f"净消耗 {q7}",
        )
    return CONSUMPTION_NORMAL, f"近 7 天净消耗 {q7}"


def evaluate(
    current_stock: int,
    safety_stock: int,
    q7: int,
    prev_q7: int,
    *,
    issued: int | None = None,
    reversed_quantity: int | None = None,
    records: int | None = None,
) -> Evaluation | None:
    tier, reasons = classify_tier(current_stock, safety_stock, q7, prev_q7)
    if tier is None:
        return None
    rate = compute_rate(q7)
    days_of_supply = compute_days_of_supply(current_stock, rate)
    consumption_state, consumption_note = classify_consumption(
        q7, issued, reversed_quantity, records
    )
    return Evaluation(
        current_stock=current_stock,
        safety_stock=safety_stock,
        q7=q7,
        prev_q7=prev_q7,
        gap=compute_gap(safety_stock, current_stock),
        rate=round(rate, 2),
        days_of_supply=None if days_of_supply is None else round(days_of_supply, 1),
        tier=tier,
        recommended_quantity=compute_replenishment_quantity(
            current_stock, safety_stock, q7, tier
        ),
        reasons=reasons,
        consumption_state=consumption_state,
        consumption_note=consumption_note,
    )
