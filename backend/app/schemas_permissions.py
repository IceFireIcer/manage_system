from pydantic import BaseModel, Field


class PermissionListOut(BaseModel):
    permissions: list[str] = Field(default_factory=list)


class PermissionCatalogItemOut(BaseModel):
    key: str
    label: str
    category: str
    category_label: str
    scope: str
    sort_order: int


class PermissionCatalogOut(BaseModel):
    items: list[PermissionCatalogItemOut] = Field(default_factory=list)


class PermissionEntryOut(BaseModel):
    key: str
    label: str
    category: str
    category_label: str
    scope: str
    allowed: bool
    default_enabled: bool
    effective_enabled: bool
    toggleable_by_teacher: bool
    locked: bool
    overridden: bool


class RoleMatrixOut(BaseModel):
    role: str
    label: str
    entries: list[PermissionEntryOut] = Field(default_factory=list)


class PermissionMatrixOut(BaseModel):
    class_id: int
    active_role: str
    roles: list[RoleMatrixOut] = Field(default_factory=list)


class ClassOverrideUpdateIn(BaseModel):
    role: str = Field(min_length=1, max_length=16)
    updates: dict[str, bool] = Field(default_factory=dict)


class ClassOverrideUpdateOut(BaseModel):
    class_id: int
    role: str
    updated: list[str] = Field(default_factory=list)
    matrix: RoleMatrixOut
