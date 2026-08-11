"""统一响应封装：ApiResponse / requestId 上下文 / meta 构造。

对应 SubTask 6.2：所有 API 响应统一格式，便于客户端解析与错误处理。

设计理念
--------
1. 所有响应（成功 / 失败）都遵循统一结构：
   - 成功：``{"success": true, "requestId": "...", "data": ..., "meta": {...}}``
   - 失败：``{"success": false, "requestId": "...", "error": {...}}``
2. ``requestId`` 通过 ``contextvars.ContextVar`` 跨协程传递，由中间件在请求开始时
   设置，``ApiResponse`` 自动读取，避免每个接口都手动传入。
3. ``meta`` 包含 ``projectCode`` / ``apiVersion`` / ``generatedAt``，便于客户端
   校验与调试。

为什么使用 contextvars？
------------------------
FastAPI 基于 asyncio，多个请求并发处理。``contextvars.ContextVar`` 是 Python
推荐的协程本地存储，每个请求有独立的上下文副本，避免 ``threading.local`` 在
协程切换时数据错乱。中间件在请求开始时 ``request_id_ctx.set(...)``，请求结束
自动清理，``ApiResponse.success()`` / ``error()`` 内部 ``request_id_ctx.get()``
自动获取当前请求的 ID。
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

# API 版本号：所有响应 meta 中固定为 v1
API_VERSION = "v1"

# 请求 ID 上下文：每个请求独立副本，由中间件 set，ApiResponse get
# 默认空字符串，未设置中间件时（如测试）也能正常工作
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")


def set_request_id(request_id: str) -> None:
    """设置当前请求的 requestId（由中间件调用）。

    Args:
        request_id: 请求 ID，由 ``get_request_id`` 依赖生成或从 X-Request-Id 头获取。
    """
    request_id_ctx.set(request_id)


def get_request_id() -> str:
    """获取当前请求的 requestId。

    Returns:
        当前请求的 ID；未设置时返回空字符串。
    """
    return request_id_ctx.get()


def build_meta(project_code: str | None = None) -> dict[str, Any]:
    """构造响应 meta 字段。

    包含以下字段：
        - projectCode: 当前项目编码（管理接口无项目上下文时为 None）
        - apiVersion: API 版本号，固定 ``v1``
        - generatedAt: 响应生成时间（ISO8601 带 UTC 时区）

    Args:
        project_code: 项目编码，可选（管理接口无项目上下文时为 None）。

    Returns:
        meta 字典。
    """
    # ISO8601 格式时间：带 UTC 时区（Z 后缀），便于客户端解析
    # 例：2026-07-30T12:34:56.789+00:00
    generated_at = datetime.now(timezone.utc).isoformat()
    return {
        "projectCode": project_code,
        "apiVersion": API_VERSION,
        "generatedAt": generated_at,
    }


class ApiResponse:
    """统一响应封装：提供 success / error 静态方法构造标准响应体。

    所有方法返回普通 dict，由 FastAPI 路由函数返回或异常处理器包装为
    ``JSONResponse``。``requestId`` 自动从 ``contextvars`` 读取。

    使用示例
    --------
    成功响应：
        return ApiResponse.success(data={"items": [...]})

    分页响应（带 meta）：
        return ApiResponse.success(
            data={"items": items},
            meta=build_meta(ctx.project_code) | {"pagination": {"page": 1, "total": 100}},
        )

    错误响应（异常处理器中）：
        return ApiResponse.error(
            code="KNOWLEDGE_BASE_NOT_FOUND",
            message="知识库不存在",
            retryable=False,
        )
    """

    @staticmethod
    def success(data: Any, meta: dict[str, Any] | None = None) -> dict[str, Any]:
        """构造成功响应。

        Args:
            data: 业务数据，可为 dict / list / 任意可序列化对象。
            meta: 元信息字典，可选。调用方可传入 ``build_meta()`` 结果或自定义 meta。

        Returns:
            标准成功响应字典：
            ``{"success": true, "requestId": "...", "data": ..., "meta": {...}}``
        """
        return {
            "success": True,
            "requestId": get_request_id(),  # 自动从 contextvars 读取
            "data": data,
            "meta": meta or {},
        }

    @staticmethod
    def error(
        code: str,
        message: str,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """构造失败响应。

        Args:
            code: 机器可读错误码，大写下划线，如 ``KNOWLEDGE_BASE_NOT_FOUND``。
            message: 人类可读错误消息。
            retryable: 客户端是否可重试，True 表示临时性错误（如限流、超时）。
            details: 额外上下文，如字段错误列表、缺失资源 ID 等。

        Returns:
            标准失败响应字典：
            ``{"success": false, "requestId": "...", "error": {...}}``
        """
        return {
            "success": False,
            "requestId": get_request_id(),  # 自动从 contextvars 读取
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "details": details or {},
            },
        }
