from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ACCOUNT_MAX_LENGTH = 64
NAME_MAX_LENGTH = 64
ROSTER_MAX_ROWS = 500

ROSTER_STATUS_READY = "ready"
ROSTER_STATUS_EXISTING = "existing"
ROSTER_STATUS_UPDATE = "update"
ROSTER_STATUS_ERROR = "error"

ON_EXISTING_SKIP = "skip"
ON_EXISTING_UPDATE = "update"


class RosterRowErrorOut(BaseModel):
    field: str
    code: str
    reason: str


class RosterRowIn(BaseModel):
    line_no: int | None = Field(default=None, ge=1)
    account: str | None = None
    name: str | None = None


class RosterImportRequest(BaseModel):
    source: str = Field(min_length=1, max_length=128)
    rows: list[RosterRowIn] = Field(min_length=1, max_length=ROSTER_MAX_ROWS)
    request_id: str | None = Field(default=None, max_length=64)
    on_existing: Literal["skip", "update"] = ON_EXISTING_SKIP

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


class RosterRowOut(BaseModel):
    line_no: int
    account: str | None = None
    name: str | None = None
    status: str
    existing_user_id: int | None = None
    existing_name: str | None = None
    same_name_ids: list[int] = Field(default_factory=list)
    message: str | None = None
    errors: list[RosterRowErrorOut] = Field(default_factory=list)


class RosterImportSummaryOut(BaseModel):
    total_rows: int
    ready_rows: int
    existing_rows: int
    update_rows: int
    error_rows: int


class RosterImportPreviewOut(BaseModel):
    source: str
    on_existing: str
    valid: bool
    summary: RosterImportSummaryOut
    rows: list[RosterRowOut]


class RosterImportReceiptRowOut(BaseModel):
    line_no: int
    user_id: int | None = None
    account: str | None = None
    name: str | None = None
    action: str
    temporary_password: str | None = None


class RosterImportReceiptOut(BaseModel):
    batch_id: int
    request_id: str
    source: str
    on_existing: str
    operator_name: str
    created_at: datetime
    summary: RosterImportSummaryOut
    rows: list[RosterImportReceiptRowOut]
