from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator


class ClassOut(BaseModel):
    id: int
    name: str
    code: str
    status: str

    model_config = {"from_attributes": True}


class ClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    code: str = Field(default="", max_length=32)
    status: str = Field(default="enabled", pattern="^(enabled|disabled)$")

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("班级名称不能为空")
        return stripped


class ClassUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    code: str | None = Field(default=None, max_length=32)
    status: str | None = Field(default=None, pattern="^(enabled|disabled)$")


class ClassTermOut(BaseModel):
    id: int
    class_id: int
    name: str
    start_date: date | None
    end_date: date | None
    status: str

    model_config = {"from_attributes": True}


class ClassTermCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    start_date: date | None = None
    end_date: date | None = None
    status: str = Field(default="active", pattern="^(planned|active|archived)$")


class ClassTermUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    start_date: date | None = None
    end_date: date | None = None
    status: str | None = Field(default=None, pattern="^(planned|active|archived)$")


class TeacherAssignRequest(BaseModel):
    user_id: int


class ClassTeacherOut(BaseModel):
    user_id: int
    account: str
    name: str
    role: str


class ClassMemberOut(BaseModel):
    user_id: int
    account: str
    name: str
    role: str
    status: str


class ActiveClassOut(BaseModel):
    id: int
    name: str
    term_name: str | None = None
    available: list[dict] = Field(default_factory=list)
    can_switch: bool = False


class ClassDetailOut(ClassOut):
    terms: list[ClassTermOut] = Field(default_factory=list)