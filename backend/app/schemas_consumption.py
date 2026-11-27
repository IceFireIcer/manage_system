from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ConsumptionItemOut(BaseModel):
    consumable_id: int
    name: str
    spec: str
    unit: str
    issued_quantity: int
    reversed_quantity: int
    cross_period_reversed_quantity: int
    net_quantity: int
    quantity: int
    daily_rate: float


class ConsumptionUnitTotalOut(BaseModel):
    unit: str
    issued_quantity: int
    reversed_quantity: int
    cross_period_reversed_quantity: int
    net_quantity: int
    daily_rate: float
    consumables: int


class ConsumptionSummaryOut(BaseModel):
    window: int
    items: list[ConsumptionItemOut]
    unit_totals: list[ConsumptionUnitTotalOut]
    provisioning_scope: str


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
