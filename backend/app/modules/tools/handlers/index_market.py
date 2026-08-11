"""指数行情查询 Handler（占位实现）。

对应 SubTask 13.3：``index_market`` 工具的具体实现。

当前为 Mock 实现，返回模拟数据。
TODO: 接入真实指数行情数据源。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


# Mock 指数名称映射表，便于返回可读的指数名称
_MOCK_INDEX_NAMES: dict[str, str] = {
    "000300": "沪深 300",
    "000905": "中证 500",
    "000001": "上证指数",
    "399006": "创业板指",
}


class IndexMarketHandler:
    """指数行情查询 Handler。

    执行 ``index_market`` 工具调用，查询股票指数行情。
    当前为 Mock 实现，返回固定模拟数据。
    """

    async def execute(self, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """执行指数行情查询。

        Args:
            inputs: 入参，包含 ``index_codes``（必填）。
            config: 项目级配置，可包含 ``api_endpoint`` 等。

        Returns:
            指数行情数据 dict：
            - ``index_codes``: 查询的指数代码列表
            - ``data``: 行情数据列表，每项含 index_code / name / close / change_pct
            - ``data_as_of``: 数据截至时间
        """
        # 从入参提取指数代码列表
        index_codes: list[str] = inputs.get("index_codes", [])

        # TODO: 接入真实指数行情数据源
        data: list[dict[str, Any]] = []
        for code in index_codes:
            data.append({
                "index_code": code,
                # 从映射表取名称，未匹配返回占位
                "name": _MOCK_INDEX_NAMES.get(code, f"指数 {code}"),
                "close": 3850.42,  # Mock 收盘点位
                "change_pct": 0.0123,  # Mock 涨跌幅（1.23%）
            })

        # 数据截至时间
        data_as_of = datetime.now(timezone.utc).isoformat()

        return {
            "index_codes": index_codes,
            "data": data,
            "data_as_of": data_as_of,
        }
