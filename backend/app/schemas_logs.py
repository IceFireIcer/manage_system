from datetime import datetime

from pydantic import BaseModel


class OperationLogItemOut(BaseModel):
    id: int
    created_at: datetime
    operator_name: str | None
    operator_account: str | None = None
    role: str | None
    action: str
    result: str | None = None
    target_type: str | None
    target_id: int | None
    target_label: str | None = None
    before_value: str | None
    after_value: str | None
    before_status: str | None = None
    after_status: str | None = None
    remark: str | None


class OperationLogPageOut(BaseModel):
    items: list[OperationLogItemOut]
    total: int
    page: int
    page_size: int
