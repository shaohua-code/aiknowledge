"""工具 Handler 抽象与工厂函数。

对应 SubTask 13.3：工具调用执行器依赖具体 Handler 实现。

设计要点
--------
1. ``ToolHandler`` Protocol
   定义工具 Handler 的统一接口：``async execute(inputs, config) -> dict``。
   每个 tool_code 对应一个 Handler 实现类，便于执行器按 code 分发。

2. ``get_tool_handler(tool_code)``
   工厂函数：根据 tool_code 返回对应的 Handler 实例。
   未注册的 tool_code 返回 None，由 Executor 抛 ExternalSourceFailedError。

3. 跨项目工具隔离
   Handler 本身不感知项目（无 project_id 参数）。
   项目隔离由上层 Executor 负责：调用 Handler 前已校验项目白名单
   与 ``applicable_projects``，Handler 仅按 config（项目级配置）执行。
   ``config`` 字段由 ProjectTool 表存储，可包含 API 端点、参数默认值等。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.modules.tools.handlers.financial_news import FinancialNewsHandler
from app.modules.tools.handlers.fund_market import FundMarketHandler
from app.modules.tools.handlers.index_market import IndexMarketHandler
from app.modules.tools.handlers.job_search import JobSearchHandler
from app.modules.tools.handlers.product_search import ProductSearchHandler


@runtime_checkable
class ToolHandler(Protocol):
    """工具 Handler 协议：定义工具执行的统一接口。

    每个工具（如 fund_market）对应一个 Handler 实现类，实现 ``execute`` 方法。
    Executor 通过 ``get_tool_handler(tool_code)`` 获取 Handler 实例后调用。

    为什么使用 Protocol 而非 ABC？
        Protocol 是结构性子类型（鸭子类型），Handler 实现类无需显式继承，
        只要实现了 ``execute`` 方法即满足协议。便于扩展第三方 Handler。

    Methods
    -------
    execute(inputs, config):
        执行工具调用，返回结果 dict。
        - inputs: 客户端传入的参数（已通过 input_schema 校验）
        - config: 项目级配置（来自 ProjectTool.config），可包含 API 端点等
        - 返回: 工具执行结果，符合 ToolDefinition.output_schema
    """

    async def execute(self, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """执行工具调用。

        Args:
            inputs: 工具入参，已通过 input_schema 校验。
            config: 项目级配置，来自 ProjectTool.config，可空字典。

        Returns:
            工具执行结果 dict，应符合 ToolDefinition.output_schema。

        Raises:
            Exception: 调用外部数据源失败的任意异常，由 Executor 捕获并转换。
        """
        ...


# ============================================================================
# 工具编码 → Handler 实例映射表
# ============================================================================
# 新增工具时在此注册 Handler 即可，Executor 通过 get_tool_handler 分发
_HANDLER_REGISTRY: dict[str, ToolHandler] = {
    "fund_market": FundMarketHandler(),
    "index_market": IndexMarketHandler(),
    "financial_news": FinancialNewsHandler(),
    "job_search": JobSearchHandler(),
    "product_search": ProductSearchHandler(),
}


def get_tool_handler(tool_code: str) -> ToolHandler | None:
    """根据工具编码获取对应的 Handler 实例。

    工厂函数：Executor 在调用工具前通过此函数获取 Handler。
    未注册的 tool_code 返回 None，由 Executor 抛 ExternalSourceFailedError。

    Args:
        tool_code: 工具编码，如 ``"fund_market"``。

    Returns:
        对应的 Handler 实例；未注册返回 None。

    Example:
        >>> handler = get_tool_handler("fund_market")
        >>> if handler is None:
        ...     raise ExternalSourceFailedError("工具未注册 Handler")
        >>> result = await handler.execute(inputs, config)
    """
    # 从注册表获取，未注册返回 None（Executor 会处理 None 情况）
    return _HANDLER_REGISTRY.get(tool_code)
