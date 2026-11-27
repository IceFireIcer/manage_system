"""补货决策规则引擎：纯函数，输入库存与窗口净消耗，输出分级与建议补货量。

分级严格按 SPEC 顺序判定，首个命中定档；整数比较避免浮点边界误差。
"""

import math
from dataclasses import dataclass

TIER_URGENT = "urgent"
TIER_MODERATE = "moderate"
TIER_OBSERVE = "observe"

WINDOW_DAYS = 7
URGENT_STOCK_RATIO = 0.3
TREND_RATIO = 1.5


@dataclass(frozen=True)
class Evaluation:
    """单次巡检的结构化指标，供 API 输出与润色上下文共用。"""

    current_stock: int
    safety_stock: int
    q7: int
    prev_q7: int
    gap: int
    rate: float
    days_of_supply: float | None
    tier: str
    recommended_quantity: int


def net_consumption(issued: int, reversed_quantity: int) -> int:
    """净消耗 = max(领用 − 红冲, 0)。"""
    return max(issued - reversed_quantity, 0)


def compute_rate(q7: int) -> float:
    return q7 / WINDOW_DAYS


def compute_gap(safety_stock: int, current_stock: int) -> int:
    return max(safety_stock - current_stock, 0)


def compute_days_of_supply(current_stock: int, rate: float) -> float | None:
    """可支撑天数；速率为零时返回 None 表示无穷大。"""
    if rate <= 0:
        return None
    return current_stock / rate


def _is_rising(q7: int, prev_q7: int) -> bool:
    if q7 <= 0:
        return False
    if prev_q7 <= 0:
        return True
    return 2 * q7 >= 3 * prev_q7


def compute_tier(
    current_stock: int, safety_stock: int, q7: int, prev_q7: int
) -> str | None:
    """返回 urgent/moderate/observe；None 表示正常或安全线为零跳过巡检。"""
    if safety_stock <= 0:
        return None

    if (
        current_stock == 0
        or current_stock * 10 < safety_stock * 3
        or (q7 > 0 and current_stock * WINDOW_DAYS <= q7)
    ):
        return TIER_URGENT

    if current_stock < safety_stock:
        if q7 > 0 and current_stock * WINDOW_DAYS <= 3 * q7:
            return TIER_MODERATE
        return TIER_OBSERVE

    if _is_rising(q7, prev_q7):
        return TIER_OBSERVE
    return None


def compute_replenishment_quantity(
    current_stock: int, safety_stock: int, q7: int, tier: str | None
) -> int:
    """建议补货量 R：覆盖缺口与一周用量，趋势触发时按一周用量。"""
    if tier is None:
        return 0
    rate = compute_rate(q7)
    if current_stock >= safety_stock:
        return max(math.ceil(rate * WINDOW_DAYS), 0)
    gap = compute_gap(safety_stock, current_stock)
    if rate <= 0:
        return gap
    return max(math.ceil(gap + rate * WINDOW_DAYS), 0)


def evaluate(
    current_stock: int, safety_stock: int, q7: int, prev_q7: int
) -> Evaluation | None:
    """计算完整指标；正常档与跳过档返回 None（不出建议、不调用大模型）。"""
    tier = compute_tier(current_stock, safety_stock, q7, prev_q7)
    if tier is None:
        return None
    rate = compute_rate(q7)
    days_of_supply = compute_days_of_supply(current_stock, rate)
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
    )
