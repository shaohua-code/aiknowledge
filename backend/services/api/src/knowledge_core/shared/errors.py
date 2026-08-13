from __future__ import annotations

from typing import Any


class CoreError(Exception):
    code = "CORE_ERROR"
    title = "请求处理失败"
    http_status = 400
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        suggestion: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.suggestion = suggestion


class AuthenticationError(CoreError):
    code = "AUTHENTICATION_REQUIRED"
    title = "需要身份验证"
    http_status = 401

    def __init__(self, message: str = "登录状态或 API Key 无效", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class ForbiddenError(CoreError):
    code = "FORBIDDEN"
    title = "没有操作权限"
    http_status = 403

    def __init__(self, message: str = "当前身份没有执行此操作的权限", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class NotFoundError(CoreError):
    code = "RESOURCE_NOT_FOUND"
    title = "资源不存在"
    http_status = 404

    def __init__(self, message: str = "资源不存在或无权访问", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class ConflictError(CoreError):
    code = "RESOURCE_CONFLICT"
    title = "资源状态冲突"
    http_status = 409


class ProviderUnavailableError(CoreError):
    code = "PROVIDER_UNAVAILABLE"
    title = "AI 服务暂不可用"
    http_status = 503
    retryable = True


class InsufficientEvidenceError(CoreError):
    code = "INSUFFICIENT_EVIDENCE"
    title = "证据不足"
    http_status = 422


class StructuredOutputError(CoreError):
    code = "MODEL_OUTPUT_INVALID"
    title = "模型输出不符合结构契约"
    http_status = 502
    retryable = True
