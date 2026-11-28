import json
import re
from typing import Final

from sqlalchemy.orm import Session

from app.models import Consumable, OperationLog, User

RESULT_SUCCESS: Final = "success"
RESULT_FAILURE: Final = "failure"
RESULT_REJECTED: Final = "rejected"
RESULT_VALUES: Final = (RESULT_SUCCESS, RESULT_FAILURE, RESULT_REJECTED)

SESSION_ACTIONS: Final = ("login", "logout", "logout_all")
AUDIT_ACTIONS: Final = (
    "log_export",
    "requisition_export",
    "password_change",
    "user_password_reset",
    "admin_bootstrap",
    "admin_bootstrap_rejected",
    "teacher_provision",
    "teacher_provision_rejected",
    "admin_handover",
    "admin_handover_rejected",
    "admin_handover_create",
    "admin_recover",
    "admin_recover_rejected",
    "permission_override_update",
    "permission_role_update",
    "account_create",
    "account_update",
    "account_status",
    "account_password_reset",
    "account_role_update",
    "account_class_update",
    "setup_complete",
    "audit_export",
)
SENSITIVE_ACTIONS: Final = SESSION_ACTIONS + AUDIT_ACTIONS

MASK: Final = "***"
SENSITIVE_KEY_TOKENS: Final = (
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "encrypted_api_key",
    "token",
    "student_no",
    "employee_no",
    "id_card",
    "idcard",
    "phone",
    "mobile",
    "email",
    "wechat",
    "qq",
    "github",
    "address",
)
SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|password|passwd|secret|token|access_token)\b\s*[:=]\s*"
    r"[^\s,;&\"'}]+"
)


def default_result(action: str) -> str:
    return RESULT_REJECTED if action.endswith("_rejected") else RESULT_SUCCESS


def is_sensitive(action: str) -> bool:
    return action.strip() in SENSITIVE_ACTIONS


def is_sensitive_query(value: str) -> bool:
    candidate = value.strip().lower()
    return any(sensitive in candidate for sensitive in SENSITIVE_ACTIONS)


def user_label(user: User | None) -> str | None:
    if user is None:
        return None
    return f"{user.account}（{user.name}）"


def consumable_label(consumable: Consumable | None) -> str | None:
    if consumable is None:
        return None
    name = consumable.name or ""
    code = consumable.code or ""
    return f"{code} {name}".strip() or None


def status_snapshot(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if text in ("enabled", "disabled"):
        return text
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return None
    if isinstance(parsed, dict):
        status = parsed.get("status")
        if isinstance(status, str):
            return status
    return None


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in SENSITIVE_KEY_TOKENS)


def _sanitize_value(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: MASK if _is_sensitive_key(str(key)) else _sanitize_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    return value


def _sanitize_text(value: str) -> str:
    return SENSITIVE_TEXT_PATTERN.sub(lambda match: f"{match.group(1)}={MASK}", value)


def sanitize_snapshot(value: str | None) -> str | None:
    if not value:
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return _sanitize_text(value)
    if isinstance(parsed, dict | list):
        return json.dumps(_sanitize_value(parsed), ensure_ascii=False)
    return value


def _operator_class_id(operator: User | None) -> int | None:
    if operator is None:
        return None
    active = getattr(operator, "active_class_id", None)
    if active is not None:
        return active
    return getattr(operator, "class_id", None)


def record_operation(
    db: Session,
    *,
    action: str,
    operator: User | None = None,
    role: str | None = None,
    operator_name: str | None = None,
    operator_account: str | None = None,
    result: str | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    target_label: str | None = None,
    before_value: str | None = None,
    after_value: str | None = None,
    remark: str | None = None,
    class_id: int | None = None,
) -> OperationLog:
    resolved_class_id = class_id if class_id is not None else _operator_class_id(operator)
    log = OperationLog(
        operator_id=operator.id if operator is not None else None,
        operator_name=operator.name if operator is not None else operator_name,
        operator_account=operator.account if operator is not None else operator_account,
        role=operator.role if operator is not None else role,
        class_id=resolved_class_id,
        action=action,
        result=result,
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
        before_value=before_value,
        after_value=after_value,
        remark=remark,
    )
    db.add(log)
    return log
