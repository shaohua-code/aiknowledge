from __future__ import annotations

from typing import Any

from knowledge_core.shared.errors import CoreError
from knowledge_core.shared.request_id import get_request_id


def success(data: Any, *, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "success": True,
        "requestId": get_request_id(),
        "data": data,
        "meta": meta or {},
    }


def failure(error: CoreError) -> dict[str, Any]:
    return {
        "success": False,
        "requestId": get_request_id(),
        "error": {
            "code": error.code,
            "title": error.title,
            "message": error.message,
            "retryable": error.retryable,
            "suggestion": error.suggestion,
            "details": error.details,
        },
    }
