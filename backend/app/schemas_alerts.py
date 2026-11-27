from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class StockAlertOut(BaseModel):
    consumable_id: int
    consumable_name: str
    unit: str
    status: Literal["open", "acknowledged", "completed"]
    risk_level: Literal["low", "out"]
    current_stock: int
    safety_stock: int
    gap: int
    partial_arrival: bool
    occurrence: int
    detection_count: int
    first_detected_at: datetime
    last_detected_at: datetime
    acknowledged_at: datetime | None = None
    acknowledged_by_name: str | None = None
    completed_at: datetime | None = None
    completed_by_name: str | None = None
    completed_reason: str | None = None
    last_order_no: str | None = None
    last_source: str | None = None
    note: str | None = None
    updated_at: datetime


class StockAlertListOut(BaseModel):
    generated_at: datetime
    active_count: int
    items: list[StockAlertOut]


class StockAlertAction(BaseModel):
    action: Literal["acknowledge", "complete", "retrigger"]
    note: str | None = Field(default=None, max_length=255)

    @field_validator("note", mode="after")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        return stripped or None