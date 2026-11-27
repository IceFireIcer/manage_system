import json
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

STATUS_ENABLED = "enabled"
STATUS_DISABLED = "disabled"


def _ensure_non_blank(value: str | None) -> str | None:
    if value is None:
        return value
    stripped = value.strip()
    if not stripped:
        raise ValueError("不能为空白内容")
    return stripped


class ConsumableCreate(BaseModel):
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


class ConsumableUpdate(BaseModel):

    name: str | None = Field(default=None, min_length=1, max_length=128)
    spec: str | None = Field(default=None, min_length=1, max_length=128)
    unit: str | None = Field(default=None, min_length=1, max_length=16)
    location: str | None = Field(default=None, min_length=1, max_length=64)
    safety_stock: int | None = Field(default=None, ge=0)
    current_stock: int | None = None

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


class ConsumableOut(BaseModel):
    id: int
    name: str
    spec: str
    unit: str
    location: str
    safety_stock: int
    current_stock: int
    class_id: int
    status: str
    low_stock: bool


def snapshot(consumable) -> str:
    return json.dumps(
        {
            "id": consumable.id,
            "name": consumable.name,
            "spec": consumable.spec,
            "unit": consumable.unit,
            "location": consumable.location,
            "safety_stock": consumable.safety_stock,
            "current_stock": consumable.current_stock,
            "status": consumable.status,
        },
        ensure_ascii=False,
    )
