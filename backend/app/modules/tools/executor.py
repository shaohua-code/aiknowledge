"""工具调用执行器：白名单校验、超时控制、降级处理。

对应 SubTask 13.2：``ToolExecutor`` 负责执行工具调用。

设计要点
--------
1. 三层校验保障跨项目工具隔离：
   - 第一层：ProjectTool 白名单（项目级，project_id 过滤）
     检查 tool_code 是否在当前项目的 ``project_tools`` 表中且 enabled=True。
   - 第二层：ToolDefinition.applicable_projects（全局表）
     即使白名单中存在，也要求工具定义的 ``applicable_projects`` 包含当前项目 code。
     防止运营误把 fund_market 加入 ai-resume 白名单（白名单可写但执行被拒）。
   - 第三层：input_schema 校验
     简化校验：检查 required 字段是否存在。

2. 超时控制与降级
   - 通过 ``asyncio.wait_for`` 限制 Handler 执行时间，超时抛
     ``ExternalSourceTimeoutError``（HTTP 504，retryable=True）。
   - 其他异常（Handler 内部抛出）统一抛 ``ExternalSourceFailedError``（HTTP 502）。
   - 上层研究链路捕获这些异常后按 ``ToolDefinition.degradation`` 策略降级，
     而非中断整个研究流程。

3. ToolExecutionResult
   统一返回结构，包含 success / data / error_code / error_message / degraded 等，
   便于研究流程编排时判断工具调用结果并决定是否降级。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.core.exceptions import (
    ExternalSourceFailedError,
    ExternalSourceTimeoutError,
    ToolNotAllowedError,
)
from app.db.repositories.tool import (
    ProjectToolRepository,
    ToolDefinitionRepository,
)
from app.modules.tools.handlers import get_tool_handler

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.project_context import ProjectContext


@dataclass
class ToolExecutionResult:
    """工具执行结果。

    统一返回结构，由 ``ToolExecutor.execute`` 返回。研究流程编排时根据此结果
    判断工具调用是否成功、是否需要降级。

    Attributes:
        success: 是否成功。True 表示 Handler 正常返回数据；False 表示失败或降级。
        data: 工具返回的数据，符合 ToolDefinition.output_schema。失败时为 None。
        error_code: 失败码，如 ``"EXTERNAL_SOURCE_TIMEOUT"``、
            ``"EXTERNAL_SOURCE_FAILED"``。成功时为 None。
        error_message: 人类可读错误消息，便于日志与排查。
        degraded: 是否触发降级。True 表示工具调用失败但已按降级策略返回兜底数据。
        degraded_reason: 降级原因，如 ``"timeout"``、``"handler_error"``。
    """

    success: bool
    data: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    degraded: bool = field(default=False)
    degraded_reason: str | None = field(default=None)


class ToolExecutor:
    """工具调用执行器：负责执行工具调用，统一处理白名单校验、超时与降级。

    调用链路（务必阅读）
    --------------------
    1. ``execute(ctx, tool_code, inputs)`` 入口
    2. 查询 ProjectTool 白名单（带 project_id 过滤）
       - 不存在或 enabled=False → 抛 ``ToolNotAllowedError``
    3. 查询 ToolDefinition（全局）
       - 不存在 → 抛 ``ToolNotAllowedError``（工具未注册）
       - applicable_projects 不包含 ctx.project_code → 抛 ``ToolNotAllowedError``
    4. 校验 inputs（简化：检查 required 字段）
       - 缺失 required 字段 → 抛 ``ValidationError``
    5. 通过 ``get_tool_handler(tool_code)`` 获取 Handler
       - 无 Handler → 抛 ``ExternalSourceFailedError``
    6. ``asyncio.wait_for(handler.execute(inputs, config), timeout=tool.timeout_seconds)``
       - 超时 → 抛 ``ExternalSourceTimeoutError``
       - 其他异常 → 抛 ``ExternalSourceFailedError``
    7. 返回 ``ToolExecutionResult(success=True, data=...)``
    """

    def __init__(self, session: "AsyncSession") -> None:
        """初始化执行器。

        Args:
            session: 异步数据库会话，用于查询 ProjectTool 白名单与 ToolDefinition。
        """
        self.session = session
        # Repository 在执行时按需创建（轻量对象）
        self._project_tool_repo = ProjectToolRepository(session)
        self._tool_def_repo = ToolDefinitionRepository(session)

    async def execute(
        self,
        ctx: "ProjectContext",
        tool_code: str,
        inputs: dict[str, Any],
    ) -> ToolExecutionResult:
        """执行工具调用。

        Args:
            ctx: 项目上下文，提供 project_id 与 project_code。
            tool_code: 工具编码，如 ``"fund_market"``。
            inputs: 工具入参 dict。

        Returns:
            ``ToolExecutionResult``：成功时 success=True + data；失败时 success=False + 错误信息。

        Raises:
            ToolNotAllowedError: 工具不在项目白名单中、已禁用、或工具定义不允许当前项目。
            ExternalSourceTimeoutError: 工具调用超时。
            ExternalSourceFailedError: Handler 未注册或执行失败（非超时）。
        """
        # ------------------------------------------------------------------
        # 步骤 1：查询 ProjectTool 白名单（带 project_id 过滤）
        # ------------------------------------------------------------------
        # ProjectToolRepository.get_by_code 内部 WHERE 含 project_id == ctx.project_id
        # 即使其他项目也启用了 fund_market，这里也只返回当前项目的配置
        project_tool = await self._project_tool_repo.get_by_code(ctx, tool_code)
        if project_tool is None:
            # 工具未在当前项目白名单中：跨项目隔离的第一道防线
            raise ToolNotAllowedError(
                f"工具 {tool_code} 未在当前项目 {ctx.project_code} 白名单中",
                details={
                    "tool_code": tool_code,
                    "project_code": ctx.project_code,
                },
            )
        if not project_tool.enabled:
            # 工具在白名单中但已禁用：enabled=False
            raise ToolNotAllowedError(
                f"工具 {tool_code} 已在项目 {ctx.project_code} 中禁用",
                details={
                    "tool_code": tool_code,
                    "project_code": ctx.project_code,
                    "enabled": False,
                },
            )

        # ------------------------------------------------------------------
        # 步骤 2：查询 ToolDefinition（全局表）
        # ------------------------------------------------------------------
        tool_def = await self._tool_def_repo.get_by_code(tool_code)
        if tool_def is None:
            # 工具定义不存在：可能是 tool_code 拼写错误或未 seed
            raise ToolNotAllowedError(
                f"工具定义不存在：{tool_code}",
                details={"tool_code": tool_code},
            )

        # 双重校验：applicable_projects 必须包含当前项目 code
        # 防止运营误把 fund_market 加入 ai-resume 白名单（白名单可写但执行被拒）
        if tool_def.applicable_projects and ctx.project_code not in tool_def.applicable_projects:
            # 工具不适用于当前项目：跨项目隔离的第二道防线
            raise ToolNotAllowedError(
                f"工具 {tool_code} 不适用于项目 {ctx.project_code}（applicable_projects 限制）",
                details={
                    "tool_code": tool_code,
                    "project_code": ctx.project_code,
                    "applicable_projects": tool_def.applicable_projects,
                },
            )

        # ------------------------------------------------------------------
        # 步骤 3：校验 inputs（简化：检查 required 字段）
        # ------------------------------------------------------------------
        # 简化校验：仅检查 input_schema.required 中的字段是否在 inputs 中存在
        # 完整 JSON Schema 校验需引入 jsonschema 库，这里仅做关键字段检查
        input_schema = tool_def.input_schema or {}
        required_fields = input_schema.get("required", [])
        missing_fields = [f for f in required_fields if f not in inputs]
        if missing_fields:
            # 缺失必填字段：返回失败结果（不抛异常，便于上层降级）
            return ToolExecutionResult(
                success=False,
                data=None,
                error_code="VALIDATION_ERROR",
                error_message=f"工具入参缺失必填字段：{missing_fields}",
                degraded=False,
                degraded_reason=None,
            )

        # ------------------------------------------------------------------
        # 步骤 4：获取 Handler 实例
        # ------------------------------------------------------------------
        handler = get_tool_handler(tool_code)
        if handler is None:
            # 工具定义存在但未注册 Handler：可能是新工具尚未实现
            raise ExternalSourceFailedError(
                f"工具 {tool_code} 未注册 Handler 实现",
                details={"tool_code": tool_code},
            )

        # 项目级配置：来自 ProjectTool.config，可包含 API 端点、参数默认值等
        # config 可能为 None（项目未自定义配置），统一转为空 dict
        config = project_tool.config or {}

        # ------------------------------------------------------------------
        # 步骤 5：超时控制执行
        # ------------------------------------------------------------------
        # 使用 asyncio.wait_for 限制 Handler 执行时间
        # 超时抛 ExternalSourceTimeoutError（HTTP 504），其他异常抛 ExternalSourceFailedError
        # 上层研究链路捕获后按 tool_def.degradation 降级策略处理
        timeout_seconds = tool_def.timeout_seconds
        try:
            # asyncio.wait_for 在超时后取消协程并抛 asyncio.TimeoutError
            data = await asyncio.wait_for(
                handler.execute(inputs, config),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            # 超时：抛 ExternalSourceTimeoutError
            # retryable=True，客户端可重试
            raise ExternalSourceTimeoutError(
                f"工具 {tool_code} 调用超时（{timeout_seconds}s）",
                details={
                    "tool_code": tool_code,
                    "timeout_seconds": timeout_seconds,
                },
            )
        except Exception as exc:
            # 其他异常：Handler 内部抛出的任意异常
            # 统一转为 ExternalSourceFailedError，避免泄露内部堆栈
            raise ExternalSourceFailedError(
                f"工具 {tool_code} 调用失败：{type(exc).__name__}",
                details={
                    "tool_code": tool_code,
                    "exception_type": type(exc).__name__,
                    # 开发环境可附异常消息，生产环境应屏蔽
                    "exception_message": str(exc) if _is_dev_env() else None,
                },
            ) from exc

        # ------------------------------------------------------------------
        # 步骤 6：返回成功结果
        # ------------------------------------------------------------------
        return ToolExecutionResult(
            success=True,
            data=data,
            error_code=None,
            error_message=None,
            degraded=False,
            degraded_reason=None,
        )


def _is_dev_env() -> bool:
    """判断当前是否为开发环境。

    用于决定异常详情中是否包含内部异常消息。
    生产环境应屏蔽内部错误细节，避免泄露实现。

    Returns:
        True 表示开发环境；False 表示生产或预发环境。
    """
    # 延迟导入避免循环依赖
    from app.core.config import settings
    return settings.app_env == "development"
