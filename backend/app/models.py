from datetime import datetime

from sqlalchemy import (
    DATETIME,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

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


class Requisition(Base):
    __tablename__ = "requisitions"
    __table_args__ = (
        UniqueConstraint("order_no", name="uq_requisitions_order_no"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    consumable_id: Mapped[int] = mapped_column(ForeignKey("consumables.id"), nullable=False)
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


class OperationLog(Base):
    __tablename__ = "operation_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    before_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, default=business_now)
