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
    sort_rule: str


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
    computed_at: datetime
    rationale_source: Literal["llm", "rules"] = "rules"
    rationale_degraded: bool = False
    disposition_status: Literal["pending", "awaiting", "partial", "resolved"] = "pending"
    disposition_handler_name: str | None = None
    disposition_order_no: str | None = None
    disposition_updated_at: datetime | None = None


class SuggestionListOut(BaseModel):
    generated_at: datetime
    generation: int = 0
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

class AuditEventOut(BaseModel):
    id: int
    event_type: str
    source_type: str
    source_id: int | None
    requisition_id: int | None
    order_no: str | None
    line_no: int | None
    business_at: datetime
    posted_at: datetime
    delta: int
    stock_before: int
    stock_after: int
    exec_seq: int
    operator_name: str | None
    unit_snapshot: str
    evidence_status: str
    can_open_source: bool


class AuditBasisOut(BaseModel):
    basis: Literal["posted_at", "business_at"]
    label: str
    cutoff: datetime | None
    stock_at_cutoff: int | None
    ledger_end: int | None
    events: list[AuditEventOut]


class AuditAnomalyOut(BaseModel):
    event_id: int
    exec_seq: int
    at: datetime
    reason: str


class AuditRangeOut(BaseModel):
    start_event_id: int
    end_event_id: int
    start_exec_seq: int
    end_exec_seq: int
    start_at: datetime
    end_at: datetime


class InventoryAuditOut(BaseModel):
    consumable_id: int
    name: str
    unit: str
    current_stock: int
    opening_stock: int | None
    opening_event_id: int | None
    opening_source: str | None
    ledger_end: int | None
    diff: int | None
    evidence_status: Literal["trusted", "insufficient", "unverifiable"]
    evidence_label: str
    evidence_reasons: list[str]
    first_anomaly: AuditAnomalyOut | None
    affected_range: AuditRangeOut | None
    posting: AuditBasisOut
    business: AuditBasisOut

MAX_COUNT_LINES = 100


class InventoryCountLineIn(BaseModel):
    consumable_id: int = Field(gt=0)
    counted_qty: int | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, max_length=64)

    @field_validator("reason", mode="after")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        return stripped or None


class InventoryCountCreate(BaseModel):
    lines: list[InventoryCountLineIn] = Field(
        min_length=1, max_length=MAX_COUNT_LINES
    )
    count_at: datetime | None = None
    counter_id: int | None = Field(default=None, gt=0)
    reviewer_id: int | None = Field(default=None, gt=0)
    remark: str | None = Field(default=None, max_length=255)
    request_id: str | None = Field(default=None, max_length=64)

    @field_validator("remark", mode="after")
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


class InventoryCountLineUpdate(BaseModel):
    consumable_id: int = Field(gt=0)
    counted_qty: int = Field(ge=0)
    reason: str | None = Field(default=None, max_length=64)

    @field_validator("reason", mode="after")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        return stripped or None


class InventoryCountLinesUpdate(BaseModel):
    lines: list[InventoryCountLineUpdate] = Field(
        min_length=1, max_length=MAX_COUNT_LINES
    )


class InventoryCountLineOut(BaseModel):
    line_no: int
    consumable_id: int
    consumable_name: str
    unit: str
    baseline_stock: int
    baseline_seq: int
    counted_qty: int | None
    period_delta: int
    expected_stock: int
    current_stock: int
    diff: int | None
    reason: str | None
    event_id: int | None
    recheck_required: bool


class InventoryCountOut(BaseModel):
    id: int
    order_no: str
    request_id: str
    status: Literal["open", "posted"]
    count_at: datetime
    counter_id: int
    counter_name: str
    counter_role: str
    reviewer_id: int
    reviewer_name: str
    reviewer_role: str
    posted_at: datetime | None
    posted_by_name: str | None
    remark: str | None
    warning: str | None
    recheck_required: bool
    lines: list[InventoryCountLineOut]
