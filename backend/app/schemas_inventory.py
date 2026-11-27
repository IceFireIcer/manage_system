from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


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


class SuggestionOut(BaseModel):
    consumable_id: int
    name: str
    spec: str
    unit: str
    current_stock: int
    safety_stock: int
    gap: int
    q7: int
    rate: float
    days_of_supply: float | None
    tier: Literal["urgent", "moderate", "observe"]
    recommended_quantity: int
    rationale: str


class SuggestionListOut(BaseModel):
    generated_at: datetime
    items: list[SuggestionOut]


class RestockCreate(BaseModel):
    consumable_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    remark: str | None = Field(default=None, max_length=255)

    @field_validator("remark", mode="after")
    @classmethod
    def normalize_remark(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        return stripped or None


class RestockOut(BaseModel):
    consumable_id: int
    consumable_name: str
    unit: str
    quantity: int
    stock_before: int
    stock_after: int
