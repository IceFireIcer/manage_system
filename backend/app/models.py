from datetime import date, datetime

from sqlalchemy import (
    DATETIME,
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, object_session, relationship

from app.business_time import now as business_now
from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="student")
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="enabled")
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    session_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class UserProfile(Base):
    __tablename__ = "user_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_profiles_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_no: Mapped[str | None] = mapped_column(String(64), nullable=True, default="")
    student_no: Mapped[str | None] = mapped_column(String(64), nullable=True, default="")
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True, default="")
    email: Mapped[str | None] = mapped_column(String(128), nullable=True, default="")
    wechat: Mapped[str | None] = mapped_column(String(64), nullable=True, default="")
    qq: Mapped[str | None] = mapped_column(String(32), nullable=True, default="")
    github: Mapped[str | None] = mapped_column(String(64), nullable=True, default="")
    avatar_base64: Mapped[str | None] = mapped_column(
        Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)
    updated_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role", name="uq_user_roles_user_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class Class(Base):
    __tablename__ = "classes"
    __table_args__ = (
        UniqueConstraint("name", name="uq_classes_name"),
        CheckConstraint(
            "status IN ('enabled','disabled')", name="ck_classes_status"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="enabled")
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)
    updated_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class ClassTerm(Base):
    __tablename__ = "class_terms"
    __table_args__ = (
        UniqueConstraint("class_id", "name", name="uq_class_terms_class_name"),
        CheckConstraint(
            "status IN ('planned','active','archived')",
            name="ck_class_terms_status",
        ),
        Index("ix_class_terms_class_status", "class_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(
        ForeignKey("classes.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)
    updated_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class ClassMembership(Base):
    __tablename__ = "class_memberships"
    __table_args__ = (
        UniqueConstraint(
            "class_id", "user_id", name="uq_class_memberships_class_user"
        ),
        Index("ix_class_memberships_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(
        ForeignKey("classes.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="teacher")
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class Consumable(Base):
    __tablename__ = "consumables"
    __table_args__ = (
        UniqueConstraint(
            "class_id", "name", "spec", name="uq_consumables_class_name_spec"
        ),
        UniqueConstraint("class_id", "code", name="uq_consumables_class_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    spec: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    unit: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    location: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    safety_stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="enabled")
    disabled_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)
    updated_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class ConsumableImportBatch(Base):
    __tablename__ = "consumable_import_batches"
    __table_args__ = (
        UniqueConstraint(
            "request_id", name="uq_consumable_import_batches_request_id"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    counted_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False)
    operator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    operator_name: Mapped[str] = mapped_column(String(64), nullable=False)
    operator_role: Mapped[str] = mapped_column(String(16), nullable=False)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_kinds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_existing: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    opening_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    by_unit_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    receipt_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class StudentImportBatch(Base):
    __tablename__ = "student_import_batches"
    __table_args__ = (
        UniqueConstraint(
            "request_id", name="uq_student_import_batches_request_id"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    on_existing: Mapped[str] = mapped_column(String(16), nullable=False, default="skip")
    operator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    operator_name: Mapped[str] = mapped_column(String(64), nullable=False)
    operator_role: Mapped[str] = mapped_column(String(16), nullable=False)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    existing_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    receipt_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class Requisition(Base):
    __tablename__ = "requisitions"
    __table_args__ = (
        UniqueConstraint("order_no", name="uq_requisitions_order_no"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    consumable_id: Mapped[int] = mapped_column(ForeignKey("consumables.id"), nullable=False)
    name_snapshot: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    spec_snapshot: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    unit_snapshot: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_after: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    reversal_of: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reversal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reentry_of: Mapped[int | None] = mapped_column(
        ForeignKey("requisitions.id"), nullable=True, index=True
    )
    order_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    handler_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    handler_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    handler_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    operator_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operator_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    backfill_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    backfill_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    linked_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_events.id"), nullable=True
    )
    linked_count_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_counts.id"), nullable=True
    )
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class RequisitionCorrection(Base):
    __tablename__ = "requisition_corrections"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_requisition_corrections_request_id"),
        CheckConstraint(
            "status IN ('submitted','accepted','rejected')",
            name="ck_requisition_corrections_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requisition_id: Mapped[int] = mapped_column(
        ForeignKey("requisitions.id"), nullable=False, index=True
    )
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="submitted")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    applicant_name: Mapped[str] = mapped_column(String(64), nullable=False)
    applicant_role: Mapped[str] = mapped_column(String(16), nullable=False)
    handler_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    handler_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    handler_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    handling_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reversal_id: Mapped[int | None] = mapped_column(
        ForeignKey("requisitions.id"), nullable=True
    )
    reentry_id: Mapped[int | None] = mapped_column(
        ForeignKey("requisitions.id"), nullable=True
    )
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)
    handled_at: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)


class RequisitionOrder(Base):
    __tablename__ = "requisition_orders"
    __table_args__ = (
        UniqueConstraint("order_no", name="uq_requisition_orders_order_no"),
        UniqueConstraint("request_id", name="uq_requisition_orders_request_id"),
        CheckConstraint(
            "status IN ('processing','submitted','rejected')",
            name="ck_requisition_orders_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(40), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="processing")
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    owner_name: Mapped[str] = mapped_column(String(64), nullable=False)
    operator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    operator_name: Mapped[str] = mapped_column(String(64), nullable=False)
    operator_role: Mapped[str] = mapped_column(String(16), nullable=False)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    business_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    backfill_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    items_signature: Mapped[str] = mapped_column(Text, nullable=False)
    receipt_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    errors_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)
    updated_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class InventoryEvent(Base):
    __tablename__ = "inventory_events"
    __table_args__ = (
        UniqueConstraint(
            "consumable_id", "exec_seq", name="uq_inventory_events_consumable_seq"
        ),
        CheckConstraint(
            "delta <> 0 OR event_type = 'scrap'", name="ck_inventory_events_delta"
        ),
        CheckConstraint(
            "stock_after = stock_before + delta", name="ck_inventory_events_balance"
        ),
        CheckConstraint("stock_before >= 0", name="ck_inventory_events_before"),
        CheckConstraint("stock_after >= 0", name="ck_inventory_events_after"),
        CheckConstraint(
            "event_type IN ('opening','requisition','reversal','restock','return',"
            "'scrap','inventory_adjustment','restock_correction')",
            name="ck_inventory_events_type",
        ),
        CheckConstraint(
            "evidence_status IN ('trusted','insufficient','unverifiable')",
            name="ck_inventory_events_evidence",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    consumable_id: Mapped[int] = mapped_column(
        ForeignKey("consumables.id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_before: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_after: Mapped[int] = mapped_column(Integer, nullable=False)
    exec_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    business_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False)
    posted_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    requisition_id: Mapped[int | None] = mapped_column(
        ForeignKey("requisitions.id"), nullable=True, index=True
    )
    line_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    operator_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operator_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    name_snapshot: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    spec_snapshot: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    unit_snapshot: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    evidence_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="trusted"
    )
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class RequisitionReturn(Base):
    __tablename__ = "requisition_returns"
    __table_args__ = (
        UniqueConstraint("order_no", name="uq_requisition_returns_order_no"),
        UniqueConstraint("request_id", name="uq_requisition_returns_request_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(40), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    requisition_id: Mapped[int] = mapped_column(
        ForeignKey("requisitions.id"), nullable=False, index=True
    )
    consumable_id: Mapped[int] = mapped_column(
        ForeignKey("consumables.id"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_before: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_after: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    operator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    operator_name: Mapped[str] = mapped_column(String(64), nullable=False)
    operator_role: Mapped[str] = mapped_column(String(16), nullable=False)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    business_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False)
    posted_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False)
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class RequisitionScrap(Base):
    __tablename__ = "requisition_scraps"
    __table_args__ = (
        UniqueConstraint("order_no", name="uq_requisition_scraps_order_no"),
        UniqueConstraint("request_id", name="uq_requisition_scraps_request_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(40), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    requisition_id: Mapped[int] = mapped_column(
        ForeignKey("requisitions.id"), nullable=False, index=True
    )
    consumable_id: Mapped[int] = mapped_column(
        ForeignKey("consumables.id"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_before: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_after: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    operator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    operator_name: Mapped[str] = mapped_column(String(64), nullable=False)
    operator_role: Mapped[str] = mapped_column(String(16), nullable=False)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    business_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False)
    posted_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False)
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class RestockOrder(Base):
    __tablename__ = "restock_orders"
    __table_args__ = (
        UniqueConstraint("order_no", name="uq_restock_orders_order_no"),
        UniqueConstraint("request_id", name="uq_restock_orders_request_id"),
        CheckConstraint(
            "status IN ('processing','submitted','rejected')",
            name="ck_restock_orders_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(40), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="processing")
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    operator_name: Mapped[str] = mapped_column(String(64), nullable=False)
    operator_role: Mapped[str] = mapped_column(String(16), nullable=False)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    business_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    items_signature: Mapped[str] = mapped_column(Text, nullable=False)
    receipt_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    errors_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)
    updated_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class RestockLine(Base):
    __tablename__ = "restock_lines"
    __table_args__ = (
        UniqueConstraint("order_id", "line_no", name="uq_restock_lines_order_line"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("restock_orders.id"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    consumable_id: Mapped[int] = mapped_column(ForeignKey("consumables.id"), nullable=False)
    consumable_name: Mapped[str] = mapped_column(String(128), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_before: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_after: Mapped[int] = mapped_column(Integer, nullable=False)
    corrected_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class RestockCorrection(Base):
    __tablename__ = "restock_corrections"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_restock_corrections_request_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("restock_orders.id"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    operator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    operator_name: Mapped[str] = mapped_column(String(64), nullable=False)
    operator_role: Mapped[str] = mapped_column(String(16), nullable=False)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    business_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False)
    posted_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False)
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class RestockDisposition(Base):
    __tablename__ = "restock_dispositions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','awaiting','partial','resolved')",
            name="ck_restock_dispositions_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    consumable_id: Mapped[int] = mapped_column(
        ForeignKey("consumables.id"), nullable=False, index=True
    )
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    handler_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    handler_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("restock_orders.id"), nullable=True)
    order_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)
    updated_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class SuggestionFeedback(Base):
    __tablename__ = "suggestion_feedbacks"
    __table_args__ = (
        UniqueConstraint(
            "class_id",
            "consumable_id",
            "suggestion_key",
            "feedback_by",
            name="uq_suggestion_feedbacks_member",
        ),
        CheckConstraint(
            "reason IN ('substitute','schedule_adjust','low_usage','other')",
            name="ck_suggestion_feedbacks_reason",
        ),
        Index(
            "ix_suggestion_feedbacks_class_consumable_created",
            "class_id",
            "consumable_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    consumable_id: Mapped[int] = mapped_column(
        ForeignKey("consumables.id"), nullable=False, index=True
    )
    suggestion_key: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    feedback_by: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    feedback_name: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME, nullable=False, default=business_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME, nullable=False, default=business_now
    )


class StockAlert(Base):
    __tablename__ = "stock_alerts"
    __table_args__ = (
        UniqueConstraint(
            "class_id", "consumable_id", name="uq_stock_alerts_class_consumable"
        ),
        CheckConstraint(
            "status IN ('open','acknowledged','completed')",
            name="ck_stock_alerts_status",
        ),
        CheckConstraint(
            "risk_level IN ('low','out')", name="ck_stock_alerts_risk_level"
        ),
        Index("ix_stock_alerts_class_status", "class_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    consumable_id: Mapped[int] = mapped_column(
        ForeignKey("consumables.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    risk_level: Mapped[str] = mapped_column(String(8), nullable=False, default="low")
    current_stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    safety_stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gap: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    occurrence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    detection_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_detected_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False)
    last_detected_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    acknowledged_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    acknowledged_by_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    completed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    completed_by_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completed_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_order_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_source: Mapped[str | None] = mapped_column(String(24), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)
    updated_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class InventoryCount(Base):
    __tablename__ = "inventory_counts"
    __table_args__ = (
        UniqueConstraint("order_no", name="uq_inventory_counts_order_no"),
        UniqueConstraint("request_id", name="uq_inventory_counts_request_id"),
        CheckConstraint(
            "status IN ('open','posted')",
            name="ck_inventory_counts_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(40), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    count_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False)
    counter_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    counter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    counter_role: Mapped[str] = mapped_column(String(16), nullable=False)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    reviewer_name: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewer_role: Mapped[str] = mapped_column(String(16), nullable=False)
    operator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    operator_name: Mapped[str] = mapped_column(String(64), nullable=False)
    operator_role: Mapped[str] = mapped_column(String(16), nullable=False)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    incident_id: Mapped[int | None] = mapped_column(
        ForeignKey("incident_reports.id"), nullable=True, index=True
    )
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    posted_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    posted_by_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)
    updated_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class InventoryCountLine(Base):
    __tablename__ = "inventory_count_lines"
    __table_args__ = (
        UniqueConstraint("count_id", "line_no", name="uq_inventory_count_lines_no"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    count_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_counts.id"), nullable=False, index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    consumable_id: Mapped[int] = mapped_column(
        ForeignKey("consumables.id"), nullable=False, index=True
    )
    consumable_name: Mapped[str] = mapped_column(String(128), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    baseline_stock: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    counted_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    period_delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    diff: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)
    updated_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class OperationLog(Base):
    __tablename__ = "operation_logs"
    __table_args__ = (
        Index("ix_operation_logs_target", "target_type", "target_id"),
        Index("ix_operation_logs_action_created", "action", "created_at"),
        Index("ix_operation_logs_result", "result"),
        Index("ix_operation_logs_operator_created", "operator_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    operator_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operator_account: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    before_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (
        UniqueConstraint(
            "class_id", "name", "semester", name="uq_courses_class_name_semester"
        ),
        CheckConstraint(
            "status IN ('enabled','disabled')", name="ck_courses_status"
        ),
        Index("ix_courses_class_status", "class_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    class_label: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    semester: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="enabled")
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_by_name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)
    updated_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class CourseRecipeSnapshot(Base):
    __tablename__ = "course_recipe_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "course_id", "version", name="uq_course_recipe_snapshots_version"
        ),
        Index("ix_course_recipe_snapshots_course", "course_id", "version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id"), nullable=False, index=True
    )
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_by_name: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class CourseRecipeItem(Base):
    __tablename__ = "course_recipe_items"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id", "consumable_id", name="uq_course_recipe_items_consumable"
        ),
        CheckConstraint(
            "quantity_per_person > 0", name="ck_course_recipe_items_quantity"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("course_recipe_snapshots.id"), nullable=False, index=True
    )
    consumable_id: Mapped[int] = mapped_column(
        ForeignKey("consumables.id"), nullable=False, index=True
    )
    consumable_name: Mapped[str] = mapped_column(String(128), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity_per_person: Mapped[int] = mapped_column(Integer, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class CourseSession(Base):
    __tablename__ = "course_sessions"
    __table_args__ = (
        UniqueConstraint("course_id", "seq", name="uq_course_sessions_course_seq"),
        CheckConstraint("headcount >= 0", name="ck_course_sessions_headcount"),
        CheckConstraint(
            "status IN ('planned','completed','cancelled')",
            name="ck_course_sessions_status",
        ),
        Index("ix_course_sessions_course_date", "course_id", "session_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id"), nullable=False, index=True
    )
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    headcount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    class_label: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="planned")
    recipe_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("course_recipe_snapshots.id"), nullable=True
    )
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_by_name: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)
    updated_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class TeachingReview(Base):
    __tablename__ = "teaching_reviews"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "version", name="uq_teaching_reviews_session_version"
        ),
        CheckConstraint(
            "standard_quantity >= 0", name="ck_teaching_reviews_standard"
        ),
        CheckConstraint(
            "actual_net_quantity >= 0", name="ck_teaching_reviews_actual"
        ),
        Index("ix_teaching_reviews_class_session", "class_id", "session_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("course_sessions.id"), nullable=False, index=True
    )
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("course_recipe_snapshots.id"), nullable=False
    )
    actual_net_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    standard_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deviation_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason_tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    teacher_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_by_name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class IncidentReport(Base):
    __tablename__ = "incident_reports"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open','confirmed','rejected','converted_to_count')",
            name="ck_incident_reports_status",
        ),
        Index("ix_incident_reports_class_status", "class_id", "status"),
        Index("ix_incident_reports_reporter_created", "reporter_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    consumable_id: Mapped[int] = mapped_column(
        ForeignKey("consumables.id"), nullable=False, index=True
    )
    reporter_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    reporter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    reporter_role: Mapped[str] = mapped_column(String(16), nullable=False)
    location: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    photo_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    handler_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    handler_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    handled_at: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    linked_count_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_counts.id"), nullable=True
    )
    count_order_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)
    updated_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class HandoverChecklist(Base):
    __tablename__ = "handover_checklists"
    __table_args__ = (
        UniqueConstraint(
            "class_id", "version", name="uq_handover_checklists_class_version"
        ),
        CheckConstraint(
            "status IN ('pending','confirmed')",
            name="ck_handover_checklists_status",
        ),
        Index("ix_handover_checklists_class_created", "class_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    outgoing_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    outgoing_name: Mapped[str] = mapped_column(String(64), nullable=False)
    outgoing_account: Mapped[str] = mapped_column(String(64), nullable=False)
    incoming_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    incoming_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    incoming_account: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    confirmed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    confirmed_by_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmed_by_account: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class HandoverItem(Base):
    __tablename__ = "handover_items"
    __table_args__ = (
        UniqueConstraint("checklist_id", "seq", name="uq_handover_items_seq"),
        CheckConstraint(
            "kind IN ('stock','restock','requisition','alert','incident',"
            "'suggestion','count')",
            name="ck_handover_items_kind",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    checklist_id: Mapped[int] = mapped_column(
        ForeignKey("handover_checklists.id"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    ref_type: Mapped[str] = mapped_column(String(32), nullable=False)
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    snapshot_payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confirmed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    confirmed_by_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


@event.listens_for(OperationLog, "before_insert")
def _fill_operation_log_snapshot(_mapper, _connection, log: OperationLog) -> None:
    if log.result is None:
        log.result = "rejected" if log.action.endswith("_rejected") else "success"
    if log.operator_name is not None or log.operator_id is None:
        return
    session = object_session(log)
    if session is None:
        return
    with session.no_autoflush:
        operator = session.get(User, log.operator_id)
    if operator is None:
        return
    log.operator_name = operator.name
    if log.operator_account is None:
        log.operator_account = operator.account

class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)

class Permission(Base):
    __tablename__ = "permissions"
    __table_args__ = (
        CheckConstraint(
            "scope IN ('system','class','personal')", name="ck_permissions_scope"
        ),
        Index("ix_permissions_category", "category", "sort_order"),
    )

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="class")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role", "permission_key", name="uq_role_permissions_role_key"),
        Index("ix_role_permissions_role", "role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    permission_key: Mapped[str] = mapped_column(
        ForeignKey("permissions.key", ondelete="CASCADE"), nullable=False
    )
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    toggleable_by_teacher: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class ClassRolePermission(Base):
    __tablename__ = "class_role_permissions"
    __table_args__ = (
        UniqueConstraint(
            "class_id", "role", "permission_key",
            name="uq_class_role_permissions_class_role_key",
        ),
        Index("ix_class_role_permissions_class_role", "class_id", "role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(
        ForeignKey("classes.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    permission_key: Mapped[str] = mapped_column(
        ForeignKey("permissions.key", ondelete="CASCADE"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)
class LlmProvider(Base):
    __tablename__ = "llm_providers"
    __table_args__ = (
        UniqueConstraint("name", name="uq_llm_providers_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    encrypted_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeout: Mapped[float] = mapped_column(Float, nullable=False, default=30.0)
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.3)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supports_tools: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)
    updated_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class LlmPrompt(Base):
    __tablename__ = "llm_prompts"
    __table_args__ = (
        UniqueConstraint("feature", "version", name="uq_llm_prompts_feature_version"),
        Index("ix_llm_prompts_feature_active", "feature", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feature: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)


class LlmFeatureBinding(Base):
    __tablename__ = "llm_feature_bindings"
    __table_args__ = (
        UniqueConstraint("feature", name="uq_llm_feature_bindings_feature"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feature: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_providers.id", ondelete="SET NULL"), nullable=True
    )
    prompt_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_prompts.id", ondelete="SET NULL"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)

    provider: Mapped[LlmProvider | None] = relationship("LlmProvider")
    prompt: Mapped[LlmPrompt | None] = relationship("LlmPrompt")
