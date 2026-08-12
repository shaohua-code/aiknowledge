from __future__ import annotations

from contextvars import ContextVar
from uuid import uuid4

_request_id: ContextVar[str] = ContextVar("request_id", default="")


def new_request_id() -> str:
    return f"req_{uuid4().hex}"


def set_request_id(value: str) -> None:
    _request_id.set(value)


def get_request_id() -> str:
    return _request_id.get() or new_request_id()
