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
    summary: InventorySummary | None = None
    items: list[InventoryItemOut]


class RuleTierOut(BaseModel):
    code: Literal["urgent", "moderate", "observe"]
    label: str
    condition: str


class RuleSpecOut(BaseModel):
    version: str
    window_days: int
    window_rule: str
    tiers: list[RuleTierOut]
    formula: str
    skip_conditions: list[str]


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
    reasons: list[str]
    consumption_state: Literal[
        "none", "reversed_to_zero", "partially_reversed", "normal"
    ]
    consumption_note: str
    rationale: str
    disposition_status: Literal["pending", "awaiting", "partial", "resolved"] = "pending"
    disposition_handler_name: str | None = None
    disposition_order_no: str | None = None
    disposition_updated_at: datetime | None = None


class SuggestionListOut(BaseModel):
    generated_at: datetime
    rules: RuleSpecOut
    items: list[SuggestionOut]


class RestockCreate(BaseModel):
    consumable_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    remark: str | None = Field(default=None, max_length=255)
    request_id: str | None = Field(default=None, max_length=64)
    received_at: datetime | None = None
    source: str | None = Field(default=None, max_length=64)

    @field_validator("remark", "source", mode="after")
    @classmethod
    def normalize_remark(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        return stripped or None

    @field_validator("request_id", mode="after")
    @classmethod
    def normalize_request_id(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        if not stripped:
            return None
        if any(ch not in "-_.:" and not ch.isalnum() for ch in stripped):
            raise ValueError("请求标识仅支持字母、数字与 - _ . : 字符")
        return stripped


class RestockOut(BaseModel):
    consumable_id: int
    consumable_name: str
    unit: str
    quantity: int
    stock_before: int
    stock_after: int
    order_no: str | None = None
    request_id: str | None = None
    source: str | None = None
    business_at: datetime | None = None
    posted_at: datetime | None = None

class InventoryEventOut(BaseModel):
    id: int
    event_type: str
    delta: int
    stock_before: int
    stock_after: int
    exec_seq: int
    business_at: datetime
    posted_at: datetime
    source_type: str
    source_id: int | None
    order_no: str | None
    requisition_id: int | None
    line_no: int | None
    operator_id: int | None
    operator_name: str | None
    operator_role: str | None
    unit_snapshot: str
    evidence_status: str


class InventoryLedgerOut(BaseModel):
    consumable_id: int
    name: str
    unit: str
    current_stock: int
    opening_stock: int | None
    consistent: bool
    events: list[InventoryEventOut]
