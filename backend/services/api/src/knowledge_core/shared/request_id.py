"""在异步请求和 Worker 任务上下文中生成、传递可追踪的请求标识。"""

from __future__ import annotations

from contextvars import ContextVar
from uuid import uuid4

_request_id: ContextVar[str] = ContextVar("request_id", default="")


def new_request_id() -> str:
    """创建不携带业务内容的随机请求标识。"""
    return f"req_{uuid4().hex}"


def set_request_id(value: str) -> None:
    """将当前协程或任务的请求标识写入上下文变量。"""
    _request_id.set(value)


def get_request_id() -> str:
    """读取当前请求标识；未初始化时懒生成一个新的标识。"""
    return _request_id.get() or new_request_id()
