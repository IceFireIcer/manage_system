"""库存总览（T07）相关 Pydantic 模型。"""

from pydantic import BaseModel


class InventorySummary(BaseModel):
    total_kinds: int
    low_stock_count: int
    today_requisitions: int


class InventoryItemOut(BaseModel):
    id: int
    name: str
    spec: str
    unit: str
    location: str
    current_stock: int
    safety_stock: int
    low_stock: bool
    is_out: bool


class InventoryOverviewOut(BaseModel):
    summary: InventorySummary
    items: list[InventoryItemOut]
