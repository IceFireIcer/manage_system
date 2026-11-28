from datetime import datetime

from pydantic import BaseModel, Field


class AccountItemOut(BaseModel):
    id: int
    account: str
    name: str
    role: str
    status: str
    class_id: int
    class_name: str
    roles: list[str] = Field(default_factory=list)
    must_change_password: bool = False
    created_at: datetime


class AccountCreateIn(BaseModel):
    account: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=64)
    role: str = Field(min_length=1, max_length=16)
    class_id: int | None = None


class AccountUpdateIn(BaseModel):
    account: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=64)
    class_id: int | None = None


class AccountStatusIn(BaseModel):
    status: str


class AccountRolesIn(BaseModel):
    roles: list[str] = Field(default_factory=list)


class AccountClassIn(BaseModel):
    class_id: int


class AccountPasswordResetOut(BaseModel):
    user: AccountItemOut
    temporary_password: str