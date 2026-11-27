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


class RequisitionItemOut(BaseModel):
    consumable_name: str
    quantity: int
    stock_after: int


class RequisitionReceiptLineOut(BaseModel):
    line_no: int
    event_id: int
    requisition_id: int
    consumable_id: int
    consumable_name: str
    unit: str
    quantity: int
    stock_before: int
    stock_after: int
    business_at: datetime
    posted_at: datetime


class RequisitionReceiptOut(BaseModel):
    order_no: str
    request_id: str
    status: str
    owner_id: int
    owner_name: str
    operator_id: int
    operator_name: str
    operator_role: str
    business_at: datetime
    posted_at: datetime | None
    remark: str | None
    lines: list[RequisitionReceiptLineOut]


class RequisitionErrorOut(BaseModel):
    line_no: int | None = None
    field: str
    code: str
    reason: str


class RequisitionRequestStatusOut(BaseModel):
    request_id: str
    order_no: str
    status: str
    receipt: RequisitionReceiptOut | None = None
    errors: list[RequisitionErrorOut] = []
    error_detail: str | None = None


class RequisitionCreateOut(BaseModel):
    count: int
    items: list[RequisitionItemOut]
    request_id: str | None = None
    order_no: str | None = None
    status: str | None = None
    business_at: datetime | None = None
    posted_at: datetime | None = None
    receipt: RequisitionReceiptOut | None = None


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
