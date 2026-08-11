"""基金行情查询 Handler（占位实现）。

对应 SubTask 13.3：``fund_market`` 工具的具体实现。

当前为 Mock 实现，返回模拟数据。
TODO: 接入真实行情数据源（如天天基金、东方财富 API）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class FundMarketHandler:
    """基金行情查询 Handler。

    执行 ``fund_market`` 工具调用，查询基金净值、年初至今收益等行情指标。

    当前为 Mock 实现，返回固定模拟数据。
    实际接入时需在 ``execute`` 中调用外部行情 API，并根据 ``config``
    中的端点配置切换数据源。
    """

    async def execute(self, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """执行基金行情查询。

        Args:
            inputs: 入参，包含 ``fund_codes``（必填）与 ``metrics``（可空）。
            config: 项目级配置，可包含 ``api_endpoint``、``api_key`` 等。

        Returns:
            基金行情数据 dict，结构符合 ToolDefinition.output_schema：
            - ``fund_codes``: 查询的基金代码列表
            - ``data``: 行情数据列表，每项含 fund_code / nav / return_ytd 等
            - ``data_as_of``: 数据截至时间（ISO8601）
        """
        # 从入参提取基金代码列表
        fund_codes: list[str] = inputs.get("fund_codes", [])
        # metrics 可空，默认返回全部指标
        metrics: list[str] | None = inputs.get("metrics")

        # TODO: 接入真实行情数据源
        # 当前返回 Mock 数据：每个基金代码生成一条占位记录
        data: list[dict[str, Any]] = []
        for code in fund_codes:
            # 根据 metrics 过滤返回字段；未指定 metrics 则返回全部
            item: dict[str, Any] = {"fund_code": code}
            if metrics is None or "nav" in metrics:
                item["nav"] = 1.2345  # Mock 净值
            if metrics is None or "return_ytd" in metrics:
                item["return_ytd"] = 0.0876  # Mock 年初至今收益（8.76%）
            if metrics is None or "volatility" in metrics:
                item["volatility"] = 0.1234  # Mock 波动率
            if metrics is None or "max_drawdown" in metrics:
                item["max_drawdown"] = -0.0567  # Mock 最大回撤
            data.append(item)

        # 数据截至时间：当前 UTC 时间，ISO8601 格式
        data_as_of = datetime.now(timezone.utc).isoformat()

        return {
            "fund_codes": fund_codes,
            "data": data,
            "data_as_of": data_as_of,
        }
