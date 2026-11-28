import hashlib
import json
import secrets
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import business_time
from app.models import OperationLog, StudentImportBatch, User
from app.roles import ensure_grants
from app.schemas_users import (
    ACCOUNT_MAX_LENGTH,
    NAME_MAX_LENGTH,
    ON_EXISTING_UPDATE,
    ROSTER_STATUS_ERROR,
    ROSTER_STATUS_EXISTING,
    ROSTER_STATUS_READY,
    ROSTER_STATUS_UPDATE,
    RosterImportPreviewOut,
    RosterImportReceiptOut,
    RosterImportReceiptRowOut,
    RosterImportRequest,
    RosterImportSummaryOut,
    RosterRowErrorOut,
    RosterRowOut,
)
from app.security import hash_password

ROLE_STUDENT = "student"
STATUS_ENABLED = "enabled"
TEMP_PASSWORD_BYTES = 9


def _snapshot(user: User) -> str:
    return json.dumps(
        {
            "id": user.id,
            "account": user.account,
            "name": user.name,
            "role": user.role,
            "status": user.status,
        },
        ensure_ascii=False,
    )


def _text(
    value: str | None,
    *,
    field: str,
    label: str,
    max_length: int,
    errors: list[RosterRowErrorOut],
) -> str | None:
    if value is None:
        errors.append(
            RosterRowErrorOut(
                field=field, code=f"missing_{field}", reason=f"缺少{label}"
            )
        )
        return None
    stripped = value.strip()
    if not stripped:
        errors.append(
            RosterRowErrorOut(
                field=field, code=f"blank_{field}", reason=f"{label}不能为空白内容"
            )
        )
        return None
    if len(stripped) > max_length:
        errors.append(
            RosterRowErrorOut(
                field=field,
                code=f"invalid_{field}",
                reason=f"{label}不能超过 {max_length} 个字符",
            )
        )
        return None
    if field == "account" and any(ch.isspace() for ch in stripped):
        errors.append(
            RosterRowErrorOut(
                field=field,
                code="invalid_account",
                reason="学号不能包含空格等空白字符",
            )
        )
        return None
    return stripped


def _evaluate_row(
    index: int,
    row,
    *,
    existing_by_account: dict[str, User],
    names_by_class: dict[str, list[User]],
    seen_accounts: set[str],
    on_existing: str,
    class_id: int,
) -> RosterRowOut:
    line_no = row.line_no or index
    errors: list[RosterRowErrorOut] = []
    account = _text(
        row.account,
        field="account",
        label="学号",
        max_length=ACCOUNT_MAX_LENGTH,
        errors=errors,
    )
    name = _text(
        row.name,
        field="name",
        label="姓名",
        max_length=NAME_MAX_LENGTH,
        errors=errors,
    )

    existing: User | None = None
    same_name_ids: list[int] = []
    if account is not None:
        if account in seen_accounts:
            errors.append(
                RosterRowErrorOut(
                    field="account",
                    code="duplicate_account_in_file",
                    reason="批内学号重复",
                )
            )
        else:
            seen_accounts.add(account)
            existing = existing_by_account.get(account)
            if existing is not None:
                if existing.role != ROLE_STUDENT:
                    errors.append(
                        RosterRowErrorOut(
                            field="account",
                            code="account_taken_by_staff",
                            reason="该学号已被教师或班委账号使用",
                        )
                    )
                    existing = None
                elif existing.class_id != class_id:
                    errors.append(
                        RosterRowErrorOut(
                            field="account",
                            code="account_in_other_class",
                            reason="该学号属于其他班级",
                        )
                    )
                    existing = None
    if name is not None:
        same_name_ids = [
            item.id
            for item in names_by_class.get(name, [])
            if item.account != account
        ]

    status_value = ROSTER_STATUS_READY
    message = None
    if errors:
        status_value = ROSTER_STATUS_ERROR
        message = errors[0].reason
    elif existing is not None:
        if on_existing == ON_EXISTING_UPDATE:
            status_value = ROSTER_STATUS_UPDATE
            message = "学号已存在，将按稳定用户 ID 更正姓名"
        else:
            status_value = ROSTER_STATUS_EXISTING
            message = "学号已存在，本次跳过"
        if same_name_ids:
            message = f"{message}；与其他人员同名，请以学号区分"
    elif same_name_ids:
        message = "与现有人员同名，请以学号区分"

    return RosterRowOut(
        line_no=line_no,
        account=account,
        name=name,
        status=status_value,
        existing_user_id=existing.id if existing is not None else None,
        existing_name=existing.name if existing is not None else None,
        same_name_ids=same_name_ids,
        message=message,
        errors=errors,
    )


def _build_summary(rows: list[RosterRowOut]) -> RosterImportSummaryOut:
    return RosterImportSummaryOut(
        total_rows=len(rows),
        ready_rows=sum(1 for row in rows if row.status == ROSTER_STATUS_READY),
        existing_rows=sum(1 for row in rows if row.status == ROSTER_STATUS_EXISTING),
        update_rows=sum(1 for row in rows if row.status == ROSTER_STATUS_UPDATE),
        error_rows=sum(1 for row in rows if row.status == ROSTER_STATUS_ERROR),
    )


def build_preview(
    db: Session, *, class_id: int, payload: RosterImportRequest
) -> RosterImportPreviewOut:
    existing_by_account = {
        user.account: user for user in db.scalars(select(User)).all()
    }
    names_by_class: dict[str, list[User]] = {}
    for user in db.scalars(select(User).where(User.class_id == class_id)).all():
        names_by_class.setdefault(user.name, []).append(user)

    seen_accounts: set[str] = set()
    rows = [
        _evaluate_row(
            index,
            row,
            existing_by_account=existing_by_account,
            names_by_class=names_by_class,
            seen_accounts=seen_accounts,
            on_existing=payload.on_existing,
            class_id=class_id,
        )
        for index, row in enumerate(payload.rows, start=1)
    ]
    summary = _build_summary(rows)
    return RosterImportPreviewOut(
        source=payload.source,
        on_existing=payload.on_existing,
        valid=summary.error_rows == 0 and summary.total_rows > 0,
        summary=summary,
        rows=rows,
    )


def _signature(preview: RosterImportPreviewOut) -> str:
    material = {
        "source": preview.source,
        "on_existing": preview.on_existing,
        "rows": [
            [row.line_no, row.account, row.name] for row in preview.rows
        ],
    }
    payload = json.dumps(material, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _receipt_from_batch(db: Session, batch: StudentImportBatch) -> RosterImportReceiptOut:
    stored = json.loads(batch.receipt_json or "{}")
    rows = [
        RosterImportReceiptRowOut(**row) for row in stored.get("rows", [])
    ]
    return RosterImportReceiptOut(
        batch_id=batch.id,
        request_id=batch.request_id,
        source=batch.source,
        on_existing=batch.on_existing,
        operator_name=batch.operator_name,
        created_at=batch.created_at,
        summary=RosterImportSummaryOut(
            total_rows=batch.total_rows,
            ready_rows=batch.created_rows,
            existing_rows=batch.existing_rows,
            update_rows=batch.updated_rows,
            error_rows=batch.error_rows,
        ),
        rows=rows,
    )


def get_receipt(
    db: Session, *, batch_id: int, class_id: int
) -> RosterImportReceiptOut:
    batch = db.get(StudentImportBatch, batch_id)
    if batch is None or batch.class_id != class_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="导入批次不存在"
        )
    return _receipt_from_batch(db, batch)


def _existing_batch(db: Session, request_id: str) -> StudentImportBatch | None:
    return db.scalars(
        select(StudentImportBatch).where(
            StudentImportBatch.request_id == request_id
        )
    ).first()


def execute_import(
    db: Session, *, operator: User, payload: RosterImportRequest
) -> tuple[RosterImportReceiptOut, bool]:
    preview = build_preview(db, class_id=operator.class_id, payload=payload)
    request_id = payload.request_id or uuid.uuid4().hex
    signature = _signature(preview)
    existing = _existing_batch(db, request_id)
    if existing is not None:
        if existing.signature != signature:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="请求标识已用于不同内容，请勿复用",
            )
        return _receipt_from_batch(db, existing), False

    if preview.summary.error_rows or (
        preview.summary.ready_rows + preview.summary.update_rows == 0
        and preview.summary.existing_rows == 0
    ):
        detail = preview.model_dump(mode="json")
        detail["message"] = (
            "存在行级错误，请修正后重新预览"
            if preview.summary.error_rows
            else "没有可导入的人员"
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)

    now = business_time.now().replace(microsecond=0)
    batch = StudentImportBatch(
        request_id=request_id,
        signature=signature,
        source=preview.source,
        on_existing=preview.on_existing,
        operator_id=operator.id,
        operator_name=operator.name,
        operator_role=operator.role,
        class_id=operator.class_id,
        total_rows=preview.summary.total_rows,
        created_rows=0,
        existing_rows=preview.summary.existing_rows,
        updated_rows=0,
        error_rows=0,
        receipt_json="{}",
        created_at=now,
    )
    db.add(batch)
    try:
        db.flush()
        receipt_rows: list[dict] = []
        passwords: dict[int, str] = {}
        created_rows = 0
        updated_rows = 0
        for row in preview.rows:
            if row.status == ROSTER_STATUS_READY:
                temporary_password = secrets.token_urlsafe(TEMP_PASSWORD_BYTES)
                user = User(
                    account=row.account or "",
                    password_hash=hash_password(temporary_password),
                    name=row.name or "",
                    role=ROLE_STUDENT,
                    class_id=operator.class_id,
                    status=STATUS_ENABLED,
                    must_change_password=True,
                )
                db.add(user)
                db.flush()
                ensure_grants(db, user)
                db.add(
                    OperationLog(
                        operator_id=operator.id,
                        role=operator.role,
                        action="user_create",
                        target_type="user",
                        target_id=user.id,
                        after_value=_snapshot(user),
                        remark=f"花名册导入 批次 {batch.id}",
                    )
                )
                passwords[row.line_no] = temporary_password
                created_rows += 1
                receipt_rows.append(
                    {
                        "line_no": row.line_no,
                        "user_id": user.id,
                        "account": user.account,
                        "name": user.name,
                        "action": "created",
                    }
                )
            elif row.status == ROSTER_STATUS_UPDATE:
                user = db.get(User, row.existing_user_id)
                if user is None:
                    continue
                changed = user.name != row.name
                if changed:
                    db.add(
                        OperationLog(
                            operator_id=operator.id,
                            role=operator.role,
                            action="user_update",
                            target_type="user",
                            target_id=user.id,
                            before_value=_snapshot(user),
                            remark=f"花名册导入 批次 {batch.id}",
                        )
                    )
                    user.name = row.name or ""
                    db.flush()
                    db.add(
                        OperationLog(
                            operator_id=operator.id,
                            role=operator.role,
                            action="user_update",
                            target_type="user",
                            target_id=user.id,
                            after_value=_snapshot(user),
                            remark=f"花名册导入 批次 {batch.id}",
                        )
                    )
                    updated_rows += 1
                receipt_rows.append(
                    {
                        "line_no": row.line_no,
                        "user_id": user.id,
                        "account": user.account,
                        "name": user.name,
                        "action": "updated" if changed else "unchanged",
                    }
                )
            elif row.status == ROSTER_STATUS_EXISTING:
                receipt_rows.append(
                    {
                        "line_no": row.line_no,
                        "user_id": row.existing_user_id,
                        "account": row.account,
                        "name": row.existing_name,
                        "action": "skipped",
                    }
                )

        summary = RosterImportSummaryOut(
            total_rows=preview.summary.total_rows,
            ready_rows=created_rows,
            existing_rows=preview.summary.existing_rows,
            update_rows=updated_rows,
            error_rows=0,
        )
        batch.created_rows = created_rows
        batch.updated_rows = updated_rows
        batch.receipt_json = json.dumps(
            {"summary": summary.model_dump(), "rows": receipt_rows},
            ensure_ascii=False,
        )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        conflict = _existing_batch(db, request_id)
        if conflict is not None and conflict.signature == signature:
            return _receipt_from_batch(db, conflict), False
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="导入期间名单被并发修改，请重新预览后再导入",
        ) from error

    db.refresh(batch)
    receipt = _receipt_from_batch(db, batch)
    for row in receipt.rows:
        row.temporary_password = passwords.get(row.line_no)
    return receipt, True
