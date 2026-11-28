from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class ThreadOut(BaseModel):
    id: int
    title: str
    archived: bool
    class_id: int
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime


class ThreadCreate(BaseModel):
    title: str | None = Field(default=None, max_length=128)


class ThreadUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=128)
    archived: bool | None = None


class ToolCallOut(BaseModel):
    name: str
    label: str
    ok: bool
    error: str | None = None


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    context: dict | None = None
    tool_calls: list[ToolCallOut] = []
    created_at: datetime


class ChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    context: dict | None = None

    @field_validator("content")
    @classmethod
    def _strip_content(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("消息内容不能为空")
        return stripped


class ChatResponse(BaseModel):
    thread: ThreadOut
    user_message: MessageOut
    assistant_message: MessageOut
    configured: bool
    answer: str
    tool_calls: list[ToolCallOut] = []


class ThreadDetailOut(BaseModel):
    thread: ThreadOut
    messages: list[MessageOut]


class AssistantStatusOut(BaseModel):
    configured: bool
    message: str