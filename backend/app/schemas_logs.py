from datetime import datetime

from pydantic import BaseModel


class OperationLogItemOut(BaseModel):
    id: int
    created_at: datetime
    operator_name: str | None
    role: str | None
    action: str
    target_type: str | None
    target_id: int | None
    before_value: str | None
    after_value: str | None
    remark: str | None


class OperationLogPageOut(BaseModel):
    items: list[OperationLogItemOut]
    total: int
    page: int
    page_size: int
