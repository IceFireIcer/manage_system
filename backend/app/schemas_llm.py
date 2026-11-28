from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class ProviderOut(BaseModel):
    id: int
    name: str
    base_url: str
    model: str
    timeout: float
    temperature: float
    enabled: bool
    is_default: bool
    supports_tools: bool
    has_api_key: bool
    api_key_masked: str | None
    created_at: datetime
    updated_at: datetime


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=255)
    model: str = Field(min_length=1, max_length=128)
    api_key: str | None = Field(default=None, max_length=512)
    timeout: float = Field(default=30.0, ge=1, le=300)
    temperature: float = Field(default=0.3, ge=0, le=2)
    enabled: bool = True
    is_default: bool = False
    supports_tools: bool = False

    @field_validator("name", "base_url", "model")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("字段不能为空")
        return stripped

    @field_validator("api_key")
    @classmethod
    def _strip_api_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class ProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    base_url: str | None = Field(default=None, min_length=1, max_length=255)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    api_key: str | None = Field(default=None, max_length=512)
    timeout: float | None = Field(default=None, ge=1, le=300)
    temperature: float | None = Field(default=None, ge=0, le=2)
    enabled: bool | None = None
    is_default: bool | None = None
    supports_tools: bool | None = None

    @field_validator("name", "base_url", "model")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("字段不能为空")
        return stripped

    @field_validator("api_key")
    @classmethod
    def _strip_api_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class PromptOut(BaseModel):
    id: int
    feature: str
    version: int
    system_prompt: str
    enabled: bool
    active: bool
    created_by: str | None
    created_at: datetime


class PromptCreate(BaseModel):
    feature: str = Field(min_length=1, max_length=32)
    system_prompt: str = Field(min_length=1)
    enabled: bool = True
    active: bool = False


class PromptUpdate(BaseModel):
    system_prompt: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None


class BindingOut(BaseModel):
    id: int
    feature: str
    provider_id: int | None
    provider_name: str | None
    prompt_id: int | None
    prompt_version: int | None
    enabled: bool
    updated_at: datetime


class BindingUpdate(BaseModel):
    provider_id: int | None = None
    prompt_id: int | None = None
    enabled: bool = True


class ConnectionTestOut(BaseModel):
    ok: bool
    message: str
    latency_ms: int | None = None
    model: str | None = None


class ConnectionTestRequest(BaseModel):
    api_key: str | None = Field(default=None, max_length=512)


class FeatureOut(BaseModel):
    key: str
    label: str
    description: str
