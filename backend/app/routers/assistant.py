"""悬浮助手：会话线程、消息持久化与只读问答接口。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import business_time
from app.assistant import context as assistant_context
from app.assistant import service as assistant_service
from app.database import get_db
from app.deps import (
    CurrentUser,
    effective_role,
    get_active_class_id,
    require_permission,
)
from app.models import AssistantMessage, AssistantThread
from app.schemas_assistant import (
    AssistantStatusOut,
    ChatRequest,
    ChatResponse,
    MessageOut,
    ThreadCreate,
    ThreadDetailOut,
    ThreadOut,
    ThreadUpdate,
)

router = APIRouter(
    prefix="/api/v1/assistant",
    tags=["assistant"],
    dependencies=[Depends(require_permission("assistant.view"))],
)

DEFAULT_TITLE = "新对话"
TITLE_LIMIT = 24
THREAD_NOT_FOUND = "会话不存在"


def _thread_out(thread: AssistantThread) -> ThreadOut:
    return ThreadOut(
        id=thread.id,
        title=thread.title,
        archived=thread.archived,
        class_id=thread.class_id,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
        last_message_at=thread.last_message_at,
    )


def _message_out(message: AssistantMessage) -> MessageOut:
    context = None
    if message.context_json:
        try:
            parsed = json.loads(message.context_json)
            context = parsed if isinstance(parsed, dict) else None
        except (TypeError, ValueError):
            context = None
    return MessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        context=context,
        created_at=message.created_at,
    )


def _get_thread(
    db: Session, thread_id: int, user_id: int, class_id: int
) -> AssistantThread:
    thread = db.scalars(
        select(AssistantThread).where(
            AssistantThread.id == thread_id,
            AssistantThread.user_id == user_id,
            AssistantThread.class_id == class_id,
        )
    ).first()
    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=THREAD_NOT_FOUND
        )
    return thread


def _thread_messages(db: Session, thread_id: int) -> list[AssistantMessage]:
    return list(
        db.scalars(
            select(AssistantMessage)
            .where(AssistantMessage.thread_id == thread_id)
            .order_by(AssistantMessage.id)
        ).all()
    )


@router.get("/status")
def get_status(
    db: Annotated[Session, Depends(get_db)],
) -> AssistantStatusOut:
    configured, message = assistant_service.status(db)
    return AssistantStatusOut(configured=configured, message=message)


@router.get("/threads")
def list_threads(
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
    class_id: Annotated[int, Depends(get_active_class_id)],
    include_archived: bool = False,
) -> list[ThreadOut]:
    stmt = select(AssistantThread).where(
        AssistantThread.user_id == current_user.id,
        AssistantThread.class_id == class_id,
    )
    if not include_archived:
        stmt = stmt.where(AssistantThread.archived.is_(False))
    rows = db.scalars(
        stmt.order_by(
            AssistantThread.last_message_at.desc(), AssistantThread.id.desc()
        )
    ).all()
    return [_thread_out(row) for row in rows]


@router.post("/threads", status_code=status.HTTP_201_CREATED)
def create_thread(
    payload: ThreadCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
    class_id: Annotated[int, Depends(get_active_class_id)],
) -> ThreadOut:
    title = (payload.title or "").strip()[:128] or DEFAULT_TITLE
    now = business_time.now()
    thread = AssistantThread(
        user_id=current_user.id,
        class_id=class_id,
        title=title,
        archived=False,
        created_at=now,
        updated_at=now,
        last_message_at=now,
    )
    db.add(thread)
    db.commit()
    db.refresh(thread)
    return _thread_out(thread)


@router.get("/threads/{thread_id}")
def get_thread_detail(
    thread_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
    class_id: Annotated[int, Depends(get_active_class_id)],
) -> ThreadDetailOut:
    thread = _get_thread(db, thread_id, current_user.id, class_id)
    messages = [_message_out(row) for row in _thread_messages(db, thread_id)]
    return ThreadDetailOut(thread=_thread_out(thread), messages=messages)


@router.patch("/threads/{thread_id}")
def update_thread(
    thread_id: int,
    payload: ThreadUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
    class_id: Annotated[int, Depends(get_active_class_id)],
) -> ThreadOut:
    thread = _get_thread(db, thread_id, current_user.id, class_id)
    changed = False
    if payload.title is not None:
        title = payload.title.strip()[:128]
        if title and title != thread.title:
            thread.title = title
            changed = True
    if payload.archived is not None and payload.archived != thread.archived:
        thread.archived = payload.archived
        changed = True
    if changed:
        thread.updated_at = business_time.now()
        db.commit()
        db.refresh(thread)
    return _thread_out(thread)


@router.delete("/threads/{thread_id}")
def delete_thread(
    thread_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
    class_id: Annotated[int, Depends(get_active_class_id)],
) -> dict[str, str]:
    thread = _get_thread(db, thread_id, current_user.id, class_id)
    db.delete(thread)
    db.commit()
    return {"status": "ok"}


@router.post("/threads/{thread_id}/messages")
def send_message(
    thread_id: int,
    payload: ChatRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
    class_id: Annotated[int, Depends(get_active_class_id)],
) -> ChatResponse:
    thread = _get_thread(db, thread_id, current_user.id, class_id)
    role = effective_role(current_user)
    context = assistant_context.build_context(
        db, role=role, class_id=class_id, raw=payload.context
    )
    history = _thread_messages(db, thread_id)
    now = business_time.now()

    user_message = AssistantMessage(
        thread_id=thread.id,
        role="user",
        content=payload.content,
        context_json=json.dumps(context, ensure_ascii=False),
        created_at=now,
    )
    db.add(user_message)
    db.flush()

    configured, answer = assistant_service.chat(
        db,
        question=payload.content,
        context=context,
        history=history,
    )

    assistant_message = AssistantMessage(
        thread_id=thread.id,
        role="assistant",
        content=answer,
        created_at=business_time.now(),
    )
    db.add(assistant_message)

    if not history and thread.title == DEFAULT_TITLE:
        thread.title = payload.content[:TITLE_LIMIT]
    thread.last_message_at = assistant_message.created_at
    thread.updated_at = assistant_message.created_at

    db.commit()
    db.refresh(thread)
    db.refresh(user_message)
    db.refresh(assistant_message)
    return ChatResponse(
        thread=_thread_out(thread),
        user_message=_message_out(user_message),
        assistant_message=_message_out(assistant_message),
        configured=configured,
        answer=answer,
    )