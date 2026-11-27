from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

MAX_RESTOCK_ITEMS = 20
MAX_BACKFILL_DAYS = 30

DISPOSITION_STATUSES = ("pending", "awaiting", "partial", "resolved")


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return value
    stripped = value.strip()
    return stripped or None


def _normalize_request_id(value: str | None) -> str | None:
    if value is None:
        return value
    stripped = value.strip()
    if not stripped:
        return None
    if any(ch not in "-_.:" and not ch.isalnum() for ch in stripped):
        raise ValueError("请求标识仅支持字母、数字与 - _ . : 字符")
    return stripped


class RestockItemIn(BaseModel):
    consumable_id: int = Field(gt=0)
    quantity: int = Field(gt=0)


class RestockOrderCreate(BaseModel):
    items: list[RestockItemIn] = Field(min_length=1, max_length=MAX_RESTOCK_ITEMS)
    received_at: datetime | None = None
    source: str | None = Field(default=None, max_length=64)
    remark: str | None = Field(default=None, max_length=255)
    request_id: str | None = Field(default=None, max_length=64)

    @field_validator("source", "remark", mode="after")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("request_id", mode="after")
    @classmethod
    def normalize_request_id(cls, value: str | None) -> str | None:
        return _normalize_request_id(value)


class RestockCorrectionCreate(BaseModel):
    line_no: int = Field(gt=0)
    quantity: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=255)
    remark: str | None = Field(default=None, max_length=255)
    request_id: str | None = Field(default=None, max_length=64)
    received_at: datetime | None = None

    @field_validator("reason", mode="after")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("纠错原因不能为空")
        return stripped

    @field_validator("remark", mode="after")
    @classmethod
    def normalize_remark(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("request_id", mode="after")
    @classmethod
    def normalize_request_id(cls, value: str | None) -> str | None:
        return _normalize_request_id(value)


class RestockLineOut(BaseModel):
    line_no: int
    event_id: int
    consumable_id: int
    consumable_name: str
    unit: str
    quantity: int
    stock_before: int
    stock_after: int
    corrected_quantity: int
    business_at: datetime
    posted_at: datetime


class RestockCorrectionOut(BaseModel):
    correction_id: int
    request_id: str
    order_no: str
    line_no: int
    consumable_id: int
    consumable_name: str
    unit: str
    quantity: int
    stock_before: int
    stock_after: int
    reason: str
    business_at: datetime
    posted_at: datetime
    event_id: int | None


class RestockReceiptOut(BaseModel):
    order_no: str
    request_id: str
    status: str
    source: str | None
    operator_id: int
    operator_name: str
    operator_role: str
    business_at: datetime
    posted_at: datetime | None
    remark: str | None
    lines: list[RestockLineOut]
    corrections: list[RestockCorrectionOut] = Field(default_factory=list)


class RestockErrorOut(BaseModel):
    line_no: int | None = None
    field: str
    code: str
    reason: str


class RestockRequestStatusOut(BaseModel):
    request_id: str
    order_no: str
    status: str
    receipt: RestockReceiptOut | None = None
    errors: list[RestockErrorOut] = Field(default_factory=list)
    error_detail: str | None = None


class SuggestionDispositionUpdate(BaseModel):
    status: Literal["pending", "awaiting", "resolved"]
    note: str | None = Field(default=None, max_length=255)

    @field_validator("note", mode="after")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)


class SuggestionDispositionOut(BaseModel):
    consumable_id: int
    status: str
    handler_name: str | None
    order_no: str | None
    note: str | None
    updated_at: datetime | None
