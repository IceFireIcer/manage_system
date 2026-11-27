"""消耗汇总与库存反推审计的响应模型（T09）。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ConsumptionItemOut(BaseModel):
    consumable_id: int
    name: str
    spec: str
    unit: str
    quantity: int
    daily_rate: float


class ConsumptionTotalsOut(BaseModel):
    quantity: int
    daily_rate: float


class ConsumptionSummaryOut(BaseModel):
    window: int
    items: list[ConsumptionItemOut]
    totals: ConsumptionTotalsOut


class AuditEventOut(BaseModel):
    time: datetime
    source: Literal["requisition", "reversal", "restock"]
    delta: int
    stock_after: int


class ConsumptionAuditOut(BaseModel):
    consumable_id: int
    name: str
    spec: str
    unit: str
    current_stock: int
    opening_stock: int | None
    consistent: bool
    events: list[AuditEventOut]
