from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

STATUS_OPEN = "open"
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"
STATUSES = (STATUS_OPEN, STATUS_CONFIRMED, STATUS_REJECTED)

DESCRIPTION_MAX_LENGTH = 500
LOCATION_MAX_LENGTH = 64
REJECT_REASON_MAX_LENGTH = 255
MAX_PHOTO_BYTES = 5 * 1024 * 1024
ALLOWED_PHOTO_TYPES = ("image/jpeg", "image/png", "image/webp")
PHOTO_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
PHOTO_MEDIA_TYPES = {value: key for key, value in PHOTO_EXTENSIONS.items()}
RATE_LIMIT_WINDOW_MINUTES = 10
RATE_LIMIT_MAX_SUBMISSIONS = 5


class IncidentOut(BaseModel):
    id: int
    class_id: int
    consumable_id: int
    consumable_name: str
    consumable_spec: str
    unit: str
    reporter_id: int
    reporter_name: str
    reporter_role: str
    location: str
    description: str
    photo_url: str | None = None
    status: Literal["open", "confirmed", "rejected"]
    handler_id: int | None = None
    handler_name: str | None = None
    handled_at: datetime | None = None
    reject_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class IncidentPageOut(BaseModel):
    items: list[IncidentOut]
    total: int
    page: int
    page_size: int


class IncidentHandle(BaseModel):
    action: Literal["confirm", "reject"]
    reject_reason: str | None = Field(
        default=None, max_length=REJECT_REASON_MAX_LENGTH
    )

    @field_validator("reject_reason", mode="after")
    @classmethod
    def normalize_reject_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None
