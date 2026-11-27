import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

STATUS_ENABLED = "enabled"
STATUS_DISABLED = "disabled"

CODE_MAX_LENGTH = 32
IMPORT_MAX_ROWS = 500
IMPORT_STATUS_READY = "ready"
IMPORT_STATUS_EXISTING = "existing"
IMPORT_STATUS_ERROR = "error"


def _ensure_non_blank(value: str | None) -> str | None:
    if value is None:
        return value
    stripped = value.strip()
    if not stripped:
        raise ValueError("不能为空白内容")
    return stripped


def _ensure_code(value: str | None) -> str | None:
    if value is None:
        return value
    stripped = value.strip()
    if not stripped:
        return None
    if len(stripped) > CODE_MAX_LENGTH:
        raise ValueError(f"编码不能超过 {CODE_MAX_LENGTH} 个字符")
    if any(ch not in "-_.:" and not ch.isalnum() for ch in stripped):
        raise ValueError("编码仅支持字母、数字与 - _ . : 字符")
    return stripped


class ConsumableCreate(BaseModel):
    code: str | None = Field(default=None, max_length=CODE_MAX_LENGTH)
    name: str = Field(min_length=1, max_length=128)
    spec: str = Field(min_length=1, max_length=128)
    unit: str = Field(min_length=1, max_length=16)
    location: str = Field(min_length=1, max_length=64)
    safety_stock: int = Field(ge=0)
    current_stock: int = Field(ge=0)

    @field_validator("name", "spec", "unit", "location", mode="after")
    @classmethod
    def strip_blank(cls, value: str) -> str:
        return _ensure_non_blank(value)  # type: ignore[return-value]

    @field_validator("code", mode="after")
    @classmethod
    def normalize_code(cls, value: str | None) -> str | None:
        return _ensure_code(value)


class ConsumableUpdate(BaseModel):

    name: str | None = Field(default=None, min_length=1, max_length=128)
    spec: str | None = Field(default=None, min_length=1, max_length=128)
    unit: str | None = Field(default=None, min_length=1, max_length=16)
    location: str | None = Field(default=None, min_length=1, max_length=64)
    safety_stock: int | None = Field(default=None, ge=0)
    current_stock: int | None = None
    expected_version: int | None = Field(default=None, ge=1)

    @field_validator("name", "spec", "unit", "location", mode="after")
    @classmethod
    def strip_blank(cls, value: str | None) -> str | None:
        return _ensure_non_blank(value)

    @model_validator(mode="after")
    def reject_current_stock(self) -> "ConsumableUpdate":
        if "current_stock" in self.model_fields_set:
            raise ValueError(
                "当前库存不可通过修改接口变更，仅能经领用/撤销/补货等库存流水调整"
            )
        return self


class ConsumableStatusUpdate(BaseModel):
    status: Literal["enabled", "disabled"]
    reason: str | None = Field(default=None, max_length=255)
    expected_version: int | None = Field(default=None, ge=1)

    @field_validator("reason", mode="after")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        return stripped or None


class ConsumableOut(BaseModel):
    id: int
    code: str | None = None
    name: str
    spec: str
    unit: str
    location: str
    safety_stock: int
    current_stock: int
    class_id: int
    status: str
    low_stock: bool
    disabled_reason: str | None = None
    version: int


def snapshot(consumable) -> str:
    return json.dumps(
        {
            "id": consumable.id,
            "code": consumable.code,
            "name": consumable.name,
            "spec": consumable.spec,
            "unit": consumable.unit,
            "location": consumable.location,
            "safety_stock": consumable.safety_stock,
            "current_stock": consumable.current_stock,
            "status": consumable.status,
            "disabled_reason": consumable.disabled_reason,
            "version": consumable.version,
        },
        ensure_ascii=False,
    )


class PendingReceiptOut(BaseModel):
    order_no: str
    quantity: int
    created_at: datetime | None = None


class PendingActionOut(BaseModel):
    kind: str
    label: str
    count: int


class DeactivationPreviewOut(BaseModel):
    consumable: ConsumableOut
    disabled_stock: int | None = None
    disabled_at: datetime | None = None
    disabled_by_name: str | None = None
    pending_receipts: list[PendingReceiptOut] = Field(default_factory=list)
    pending_actions: list[PendingActionOut] = Field(default_factory=list)


class StatusAuditOut(BaseModel):
    previous_stock: int | None = None
    current_stock: int
    balance_changed: bool
    reason: str | None = None


class ConsumableStatusOut(ConsumableOut):
    audit: StatusAuditOut


class ImportRowErrorOut(BaseModel):
    field: str
    code: str
    reason: str


class LedgerImportRowIn(BaseModel):
    line_no: int | None = Field(default=None, ge=1)
    code: str | None = None
    name: str | None = None
    spec: str | None = None
    unit: str | None = None
    location: str | None = None
    safety_stock: int | str | None = None
    opening_stock: int | str | None = None


class LedgerImportRequest(BaseModel):
    source: str = Field(min_length=1, max_length=128)
    counted_at: datetime
    rows: list[LedgerImportRowIn] = Field(
        min_length=1, max_length=IMPORT_MAX_ROWS
    )
    request_id: str | None = Field(default=None, max_length=64)

    @field_validator("source", mode="after")
    @classmethod
    def normalize_source(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("来源不能为空白内容")
        return stripped

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


class LedgerImportRowOut(BaseModel):
    line_no: int
    code: str | None = None
    name: str | None = None
    spec: str | None = None
    unit: str | None = None
    location: str | None = None
    safety_stock: int = 0
    opening_stock: int = 0
    status: str
    message: str | None = None
    errors: list[ImportRowErrorOut] = Field(default_factory=list)


class ImportUnitSummaryOut(BaseModel):
    unit: str
    kinds: int
    opening_stock: int


class LedgerImportSummaryOut(BaseModel):
    total_rows: int
    ready_rows: int
    existing_rows: int
    error_rows: int
    total_kinds: int
    opening_total: int
    by_unit: list[ImportUnitSummaryOut]


class LedgerImportPreviewOut(BaseModel):
    source: str
    counted_at: datetime
    valid: bool
    summary: LedgerImportSummaryOut
    rows: list[LedgerImportRowOut]


class LedgerImportReceiptRowOut(BaseModel):
    line_no: int
    consumable_id: int
    code: str
    name: str
    spec: str
    unit: str
    opening_stock: int
    event_id: int | None = None
    event_stock_after: int
    reconciled: bool


class LedgerImportReceiptOut(BaseModel):
    batch_id: int
    request_id: str
    source: str
    counted_at: datetime
    operator_name: str
    created_at: datetime
    summary: LedgerImportSummaryOut
    rows: list[LedgerImportReceiptRowOut]
