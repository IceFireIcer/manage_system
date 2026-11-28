from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    account: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    id: int
    account: str
    name: str
    role: str

    model_config = {"from_attributes": True}


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=64)


class SwitchRoleRequest(BaseModel):
    role: str = Field(min_length=1, max_length=16)


class SessionClassOut(BaseModel):
    id: int
    name: str
    term_name: str | None = None


class SessionOut(BaseModel):
    user: UserOut
    must_change_password: bool
    session_expires_at: datetime | None = None
    roles: list[str] = Field(default_factory=list)
    active_role: str | None = None
    permissions: list[str] = Field(default_factory=list)
    available_classes: list[SessionClassOut] = Field(default_factory=list)
    active_class: SessionClassOut | None = None
    can_switch_class: bool = False
