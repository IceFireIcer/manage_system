from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUSES = (STATUS_PENDING, STATUS_CONFIRMED)

KIND_STOCK = "stock"
KIND_RESTOCK = "restock"
KIND_REQUISITION = "requisition"
KIND_ALERT = "alert"
KIND_INCIDENT = "incident"
KIND_SUGGESTION = "suggestion"
KIND_COUNT = "count"
KINDS = (
    KIND_STOCK,
    KIND_RESTOCK,
    KIND_REQUISITION,
    KIND_ALERT,
    KIND_INCIDENT,
    KIND_SUGGESTION,
    KIND_COUNT,
)

MAX_NAME_LENGTH = 64
MAX_ACCOUNT_LENGTH = 64


class HandoverChecklistCreate(BaseModel):
    incoming_id: int | None = Field(default=None, gt=0)
    incoming_account: str | None = Field(
        default=None, max_length=MAX_ACCOUNT_LENGTH
    )
    incoming_name: str | None = Field(default=None, max_length=MAX_NAME_LENGTH)

    @field_validator("incoming_account", "incoming_name", mode="after")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class HandoverItemOut(BaseModel):
    id: int
    seq: int
    kind: Literal[
        "stock",
        "restock",
        "requisition",
        "alert",
        "incident",
        "suggestion",
        "count",
    ]
    ref_type: str
    ref_id: int | None = None
    label: str
    payload: dict = Field(default_factory=dict)
    confirmed: bool
    confirmed_by_name: str | None = None
    confirmed_at: datetime | None = None


class HandoverChecklistSummaryOut(BaseModel):
    id: int
    class_id: int
    version: int
    status: Literal["pending", "confirmed"]
    outgoing_id: int
    outgoing_name: str
    outgoing_account: str
    incoming_id: int | None = None
    incoming_name: str | None = None
    incoming_account: str | None = None
    item_count: int
    confirmed_count: int
    snapshot_at: datetime
    confirmed_at: datetime | None = None
    confirmed_by_id: int | None = None
    confirmed_by_name: str | None = None
    confirmed_by_account: str | None = None
    created_at: datetime


class HandoverChecklistOut(HandoverChecklistSummaryOut):
    items: list[HandoverItemOut] = Field(default_factory=list)


class HandoverChecklistPageOut(BaseModel):
    items: list[HandoverChecklistSummaryOut]
    total: int
    page: int
    page_size: int
