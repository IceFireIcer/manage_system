"""助手动作：确认令牌、幂等执行、取消与审计。"""

from __future__ import annotations

import json
import secrets

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import business_time
from app.assistant import tools as assistant_tools
from app.models import AssistantToolAction, User
from app.operation_log import record_operation

ACTION_NOT_FOUND = "操作不存在或已失效"
ACTION_CANCELLED = "操作已取消"
ACTION_EXECUTED = "操作已执行，无法取消"
ACTION_NOT_CONFIRMABLE = "该操作无需再次确认"
ACTION_CONFIRM_DENIED = "当前角色无权确认该操作"
ACTION_CLASS_CHANGED = "当前班级已变更，请重新发起操作"


def _dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _loads(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _load_action(db: Session, token: str, user: User) -> AssistantToolAction:
    action = db.scalars(
        select(AssistantToolAction).where(
            AssistantToolAction.confirmation_token == token
        )
    ).first()
    if action is None or action.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ACTION_NOT_FOUND
        )
    return action


def _preview_of(action: AssistantToolAction) -> dict:
    preview = _loads(action.result_json).get("preview")
    return preview if isinstance(preview, dict) else {}


def serialize(action: AssistantToolAction) -> dict:
    spec = assistant_tools.ACTION_TOOL_MAP.get(action.tool_name)
    payload = _loads(action.result_json)
    preview = payload.get("preview") if isinstance(payload.get("preview"), dict) else {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else None
    error = payload.get("error") if isinstance(payload.get("error"), str) else None
    return {
        "token": action.confirmation_token,
        "tool": action.tool_name,
        "label": spec.label if spec is not None else action.tool_name,
        "kind": action.kind,
        "status": action.status,
        "requires_confirmation": bool(spec and spec.requires_confirmation),
        "summary": preview.get("summary"),
        "route": preview.get("route"),
        "query": preview.get("query") or {},
        "result": result,
        "error": error,
        "created_at": action.created_at,
        "confirmed_at": action.confirmed_at,
    }


def create_action(
    db: Session,
    *,
    thread_id: int | None,
    user: User,
    class_id: int,
    spec: assistant_tools.ActionToolSpec,
    args: dict,
    preview: dict,
) -> AssistantToolAction:
    token = secrets.token_urlsafe(32)[:64]
    action = AssistantToolAction(
        thread_id=thread_id,
        user_id=user.id,
        class_id=class_id,
        tool_name=spec.name,
        kind=spec.kind,
        args_json=_dumps(args),
        status=(
            assistant_tools.ACTION_PENDING
            if spec.requires_confirmation
            else assistant_tools.ACTION_PREFILLED
        ),
        confirmation_token=token,
        result_json=_dumps({"preview": preview}),
        idempotency_key=f"{spec.name}:{user.id}:{token}",
        created_at=business_time.now(),
    )
    db.add(action)
    db.flush()
    return action


def list_actions(
    db: Session, *, thread_id: int, user: User, class_id: int
) -> list[dict]:
    rows = db.scalars(
        select(AssistantToolAction)
        .where(
            AssistantToolAction.thread_id == thread_id,
            AssistantToolAction.user_id == user.id,
            AssistantToolAction.class_id == class_id,
        )
        .order_by(AssistantToolAction.id.desc())
        .limit(20)
    ).all()
    return [serialize(row) for row in reversed(rows)]


def confirm(
    db: Session, *, user: User, role: str, class_id: int, token: str
) -> dict:
    action = _load_action(db, token, user)
    if action.status == assistant_tools.ACTION_CONFIRMED:
        return serialize(action)
    if action.status == assistant_tools.ACTION_CANCELLED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=ACTION_CANCELLED
        )
    if action.status == assistant_tools.ACTION_FAILED:
        return serialize(action)
    if action.status != assistant_tools.ACTION_PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=ACTION_NOT_CONFIRMABLE
        )
    if action.class_id != class_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=ACTION_CLASS_CHANGED
        )
    spec = assistant_tools.ACTION_TOOL_MAP.get(action.tool_name)
    if spec is None or spec.execute is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ACTION_NOT_FOUND
        )
    ctx = assistant_tools.ToolContext(
        db=db, user=user, role=role, class_id=class_id
    )
    if not assistant_tools.has_permission(ctx, spec.permission_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=ACTION_CONFIRM_DENIED
        )

    locked = db.scalars(
        select(AssistantToolAction)
        .where(AssistantToolAction.id == action.id)
        .with_for_update()
    ).first()
    if locked is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ACTION_NOT_FOUND
        )
    if locked.status == assistant_tools.ACTION_CONFIRMED:
        return serialize(locked)
    if locked.status != assistant_tools.ACTION_PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=ACTION_NOT_CONFIRMABLE
        )

    args = _loads(locked.args_json)
    preview = _preview_of(locked)
    action_id = locked.id
    now = business_time.now()
    try:
        result = assistant_tools.execute_action(spec, ctx, args)
    except assistant_tools.ToolActionError as exc:
        db.rollback()
        action = db.get(AssistantToolAction, action_id)
        if action is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=ACTION_NOT_FOUND
            )
        action.status = assistant_tools.ACTION_FAILED
        action.confirmed_at = now
        action.result_json = _dumps(
            {"preview": preview, "error": str(exc)}
        )
        record_operation(
            db,
            action="assistant_tool_call",
            operator=user,
            target_type="assistant_tool",
            target_id=action.id,
            target_label=spec.name,
            result="failure",
            remark=f"助手确认执行 {spec.name} 失败：{exc}",
        )
        db.commit()
        db.refresh(action)
        return serialize(action)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    locked.status = assistant_tools.ACTION_CONFIRMED
    locked.confirmed_at = now
    locked.result_json = _dumps({"preview": preview, "result": result})
    record_operation(
        db,
        action="assistant_tool_call",
        operator=user,
        target_type="assistant_tool",
        target_id=locked.id,
        target_label=spec.name,
        result="success",
        remark=f"助手确认执行 {spec.name}：{preview.get('summary') or ''}",
    )
    db.commit()
    db.refresh(locked)
    return serialize(locked)


def cancel(db: Session, *, user: User, token: str) -> dict:
    action = _load_action(db, token, user)
    if action.status == assistant_tools.ACTION_CANCELLED:
        return serialize(action)
    if action.status in (
        assistant_tools.ACTION_CONFIRMED,
        assistant_tools.ACTION_FAILED,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=ACTION_EXECUTED
        )
    if action.status != assistant_tools.ACTION_PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=ACTION_NOT_CONFIRMABLE
        )
    spec = assistant_tools.ACTION_TOOL_MAP.get(action.tool_name)
    action.status = assistant_tools.ACTION_CANCELLED
    record_operation(
        db,
        action="assistant_tool_call",
        operator=user,
        target_type="assistant_tool",
        target_id=action.id,
        target_label=action.tool_name,
        result="success",
        remark=f"助手取消操作 {spec.label if spec else action.tool_name}",
    )
    db.commit()
    db.refresh(action)
    return serialize(action)