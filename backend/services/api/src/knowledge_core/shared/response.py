"""统一封装控制面和运行面的成功、失败响应，确保 requestId 可追踪。"""

from __future__ import annotations

from typing import Any

from knowledge_core.shared.errors import CoreError
from knowledge_core.shared.request_id import get_request_id


def success(data: Any, *, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """生成标准成功信封，不改变领域数据本身。"""
    return {
        "success": True,
        "requestId": get_request_id(),
        "data": data,
        "meta": meta or {},
    }


def failure(error: CoreError) -> dict[str, Any]:
    """将领域错误转换为稳定、脱敏且可指导重试的失败信封。"""
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
