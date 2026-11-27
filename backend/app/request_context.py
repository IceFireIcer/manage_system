from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

REQUEST_ID_HEADER = "X-Request-Id"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_request_id: ContextVar[str] = ContextVar("request_id", default="")


def new_request_id() -> str:
    return uuid.uuid4().hex


def incoming_request_id(value: str | None) -> str | None:
    candidate = (value or "").strip()
    if not candidate or not REQUEST_ID_PATTERN.match(candidate):
        return None
    return candidate


def set_request_id(value: str) -> object:
    return _request_id.set(value)


def reset_request_id(token: object) -> None:
    _request_id.reset(token)


def current_request_id() -> str:
    return _request_id.get()


def log_tag() -> str:
    rid = current_request_id()
    return f"[request_id={rid or '-'}]"
