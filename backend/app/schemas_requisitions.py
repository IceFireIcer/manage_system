"""批量领用登记相关 Pydantic 模型（独立文件，避免与其他模块的 schemas 交叉冲突）。"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

MAX_ITEMS = 5
MAX_BACKFILL_DAYS = 30


class RequisitionItemIn(BaseModel):
    consumable_id: int = Field(gt=0)
    quantity: int = Field(gt=0)


class RequisitionCreate(BaseModel):
    items: list[RequisitionItemIn] = Field(min_length=1, max_length=MAX_ITEMS)
    user_id: int | None = Field(default=None, gt=0)
    created_at: datetime | None = None
    remark: str | None = Field(default=None, max_length=255)

    @field_validator("remark", mode="after")
    @classmethod
    def normalize_remark(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        return stripped or None


class RequisitionItemOut(BaseModel):
    consumable_name: str
    quantity: int
    stock_after: int


class RequisitionCreateOut(BaseModel):
    count: int
    items: list[RequisitionItemOut]

class ReversalCreate(BaseModel):
    reason: str = Field(min_length=1, max_length=255)
    remark: str | None = Field(default=None, max_length=255)

    @field_validator("reason", mode="after")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("撤销原因不能为空")
        return stripped

    @field_validator("remark", mode="after")
    @classmethod
    def normalize_remark(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        return stripped or None


class RequisitionRecordOut(BaseModel):
    id: int
    consumable_id: int
    consumable_name: str
    unit: str
    user_id: int
    user_name: str
    quantity: int
    stock_after: int
    type: str
    status: str
    reversal_of: int | None
    reversal_reason: str | None
    created_at: datetime
    remark: str | None


class RequisitionRelatedOut(BaseModel):
    id: int
    type: str
    status: str
    quantity: int
    stock_after: int
    created_at: datetime
    reversal_reason: str | None


class RequisitionListItemOut(BaseModel):
    id: int
    created_at: datetime
    consumable_name: str
    consumable_spec: str
    quantity: int
    user_id: int
    user_name: str
    stock_after: int
    type: str
    status: str
    reversal_of: int | None
    reversal_reason: str | None
    remark: str | None
    related: RequisitionRelatedOut | None = None


class RequisitionPageOut(BaseModel):
    items: list[RequisitionListItemOut]
    total: int
    page: int
    page_size: int


class RequisitionDetailOut(RequisitionRecordOut):
    origin: RequisitionRelatedOut | None = None
    reversal: RequisitionRelatedOut | None = None
