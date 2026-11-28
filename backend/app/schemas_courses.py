from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

STATUS_ENABLED = "enabled"
STATUS_DISABLED = "disabled"
MAX_RECIPE_ITEMS = 3


def _strip(value: str) -> str:
    return value.strip()


class CourseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    class_label: str = Field(default="", max_length=64)
    semester: str = Field(default="", max_length=32)

    @field_validator("name", mode="after")
    @classmethod
    def ensure_name(cls, value: str) -> str:
        stripped = _strip(value)
        if not stripped:
            raise ValueError("课程名称不能为空白内容")
        return stripped

    @field_validator("class_label", "semester", mode="after")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _strip(value)


class CourseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    class_label: str | None = Field(default=None, max_length=64)
    semester: str | None = Field(default=None, max_length=32)
    status: Literal["enabled", "disabled"] | None = None
    expected_version: int | None = Field(default=None, ge=1)

    @field_validator("name", mode="after")
    @classmethod
    def ensure_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = _strip(value)
        if not stripped:
            raise ValueError("课程名称不能为空白内容")
        return stripped

    @field_validator("class_label", "semester", mode="after")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        return value if value is None else _strip(value)

    @model_validator(mode="after")
    def reject_explicit_null(self) -> "CourseUpdate":
        for field_name in ("name", "class_label", "semester", "status"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} 不能为空值")
        return self


class CourseOut(BaseModel):
    id: int
    class_id: int
    name: str
    class_label: str
    semester: str
    status: str
    created_by_name: str
    version: int
    session_count: int
    recipe_version: int | None = None
    created_at: datetime
    updated_at: datetime


class CoursePageOut(BaseModel):
    items: list[CourseOut]
    total: int
    page: int
    page_size: int


class CourseSessionCreate(BaseModel):
    seq: int = Field(ge=1)
    session_date: date
    headcount: int = Field(ge=0)
    class_label: str = Field(default="", max_length=64)
    status: Literal["planned", "completed", "cancelled"] = "planned"

    @field_validator("class_label", mode="after")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        return _strip(value)


class CourseSessionUpdate(BaseModel):
    seq: int | None = Field(default=None, ge=1)
    session_date: date | None = None
    headcount: int | None = Field(default=None, ge=0)
    class_label: str | None = Field(default=None, max_length=64)
    status: Literal["planned", "completed", "cancelled"] | None = None

    @field_validator("class_label", mode="after")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        return value if value is None else _strip(value)

    @model_validator(mode="after")
    def reject_explicit_null(self) -> "CourseSessionUpdate":
        for field_name in ("seq", "session_date", "headcount", "class_label", "status"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} 不能为空值")
        return self


class CourseSessionOut(BaseModel):
    id: int
    course_id: int
    seq: int
    session_date: date
    headcount: int
    class_label: str
    status: str
    recipe_snapshot_id: int | None = None
    recipe_version: int | None = None
    created_by_name: str
    created_at: datetime
    updated_at: datetime


class CourseSessionPageOut(BaseModel):
    items: list[CourseSessionOut]
    total: int
    page: int
    page_size: int


class RecipeItemIn(BaseModel):
    consumable_id: int = Field(ge=1)
    quantity_per_person: int = Field(ge=1)
    unit: str | None = Field(default=None, max_length=16)
    remark: str | None = Field(default=None, max_length=255)

    @field_validator("unit", "remark", mode="after")
    @classmethod
    def normalize_optional(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = _strip(value)
        return stripped or None


class RecipeSaveRequest(BaseModel):
    items: list[RecipeItemIn] = Field(min_length=1, max_length=MAX_RECIPE_ITEMS)
    note: str | None = Field(default=None, max_length=255)

    @field_validator("note", mode="after")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = _strip(value)
        return stripped or None

    @model_validator(mode="after")
    def ensure_unique_consumables(self) -> "RecipeSaveRequest":
        ids = [item.consumable_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("配方中耗材不可重复")
        return self


class RecipeItemOut(BaseModel):
    consumable_id: int
    name: str
    spec: str
    unit: str
    quantity_per_person: int
    remark: str | None = None


class RecipeSnapshotOut(BaseModel):
    id: int
    course_id: int
    version: int
    note: str | None = None
    created_by_name: str
    created_at: datetime
    items: list[RecipeItemOut]


class RecipeOut(BaseModel):
    configured: bool
    snapshot: RecipeSnapshotOut | None = None


class RecipeHistoryOut(BaseModel):
    items: list[RecipeSnapshotOut]

RiskLevel = Literal["sufficient", "critical", "insufficient", "unconfigured"]
ItemRiskLevel = Literal["sufficient", "critical", "insufficient"]


class CourseRiskItemOut(BaseModel):
    consumable_id: int
    consumable_name: str
    unit: str
    quantity_per_person: int
    required_quantity: int
    available_quantity: int
    safety_stock: int
    gap: int
    level: ItemRiskLevel


class CourseSessionRiskOut(BaseModel):
    configured: bool
    level: RiskLevel
    course_id: int
    course_name: str
    session_id: int
    seq: int
    session_date: date
    headcount: int
    recipe_version: int | None = None
    lead_days: int
    arrival_deadline: date | None = None
    gap: int
    gap_consumable_name: str | None = None
    items: list[CourseRiskItemOut]


class CourseRiskListOut(BaseModel):
    days: int
    lead_days: int
    items: list[CourseSessionRiskOut]

WeekRiskReason = Literal[
    "missing_recipe", "empty_recipe", "missing_headcount", "missing_consumable"
]


class WeekRiskSessionOut(BaseModel):
    course_id: int
    course_name: str
    session_id: int
    seq: int
    headcount: int
    required_quantity: int
    recipe_version: int | None = None


class WeekRiskItemOut(BaseModel):
    class_id: int
    class_label: str
    session_date: date
    consumable_id: int
    consumable_name: str
    unit: str
    required_quantity: int
    available_quantity: int
    safety_stock: int
    gap: int
    level: ItemRiskLevel
    arrival_deadline: date | None = None
    sessions: list[WeekRiskSessionOut]


class WeekRiskUnconfiguredOut(BaseModel):
    course_id: int
    course_name: str
    session_id: int
    seq: int
    session_date: date
    headcount: int
    reason: WeekRiskReason
    message: str


class WeekRiskOut(BaseModel):
    days: int
    lead_days: int
    page: int
    page_size: int
    total: int
    items: list[WeekRiskItemOut]
    unconfigured: list[WeekRiskUnconfiguredOut]


ArrivalCoverageStatus = Literal["matched", "unmatched", "not_arrived"]


class ArrivalCoverageItemOut(BaseModel):
    consumable_id: int
    consumable_name: str
    unit: str
    arrived_quantity: int
    required_quantity: int
    covered_quantity: int
    remaining_quantity: int
    level_before: ItemRiskLevel
    level_after: ItemRiskLevel
    covered: bool


class ArrivalCoverageSessionOut(BaseModel):
    course_id: int
    course_name: str
    session_id: int
    seq: int
    session_date: date
    headcount: int
    covered: bool
    items: list[ArrivalCoverageItemOut]


class ArrivalCoverageOut(BaseModel):
    matched: bool
    status: ArrivalCoverageStatus
    order_no: str
    message: str
    days: int
    lead_days: int
    business_at: datetime | None = None
    total: int
    items: list[ArrivalCoverageSessionOut]
    truncated: bool = False

REVIEW_TAG_CANDIDATES = (
    "学生操作不熟",
    "耗材损耗偏高",
    "配方估算偏高",
    "课时调整",
    "其他",
)
MAX_REVIEW_TAGS = 6
MAX_REVIEW_TAG_LENGTH = 32


def _normalize_review_tags(tags: list[str]) -> list[str]:
    normalized: list[str] = []
    for raw in tags:
        tag = raw.strip()
        if not tag:
            continue
        if len(tag) > MAX_REVIEW_TAG_LENGTH:
            raise ValueError(f"原因标签不能超过 {MAX_REVIEW_TAG_LENGTH} 个字符")
        if tag not in normalized:
            normalized.append(tag)
    if len(normalized) > MAX_REVIEW_TAGS:
        raise ValueError(f"原因标签最多 {MAX_REVIEW_TAGS} 个")
    return normalized


class TeachingReviewCreate(BaseModel):
    reason_tags: list[str] = Field(default_factory=list)
    teacher_note: str | None = Field(default=None, max_length=255)

    @field_validator("reason_tags", mode="after")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        return _normalize_review_tags(value)

    @field_validator("teacher_note", mode="after")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = _strip(value)
        return stripped or None


class TeachingReviewUpdate(BaseModel):
    reason_tags: list[str] | None = None
    teacher_note: str | None = Field(default=None, max_length=255)

    @field_validator("reason_tags", mode="after")
    @classmethod
    def normalize_tags(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _normalize_review_tags(value)

    @field_validator("teacher_note", mode="after")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = _strip(value)
        return stripped or None


class TeachingReviewRecipeItemOut(BaseModel):
    consumable_id: int
    consumable_name: str
    unit: str
    quantity_per_person: int
    standard_quantity: int


class TeachingReviewOut(BaseModel):
    id: int
    class_id: int
    course_id: int
    course_name: str
    session_id: int
    session_seq: int
    session_date: date
    headcount: int
    snapshot_id: int
    recipe_version: int | None = None
    actual_net_quantity: int
    standard_quantity: int
    deviation_ratio: float | None = None
    deviation_available: bool
    reason_tags: list[str]
    teacher_note: str | None = None
    created_by_name: str
    created_at: datetime
    version: int
    items: list[TeachingReviewRecipeItemOut]
    reason_candidates: list[str]


class TeachingReviewListOut(BaseModel):
    items: list[TeachingReviewOut]
    reason_candidates: list[str]
