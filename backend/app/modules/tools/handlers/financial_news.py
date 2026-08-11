"""财经新闻检索 Handler（占位实现）。

对应 SubTask 13.3：``financial_news`` 工具的具体实现。

当前为 Mock 实现，返回模拟数据。
TODO: 接入真实新闻数据源（如财联社、新华财经 API）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


class FinancialNewsHandler:
    """财经新闻检索 Handler。

    执行 ``financial_news`` 工具调用，按关键词检索财经新闻。
    当前为 Mock 实现，返回固定模拟数据。
    """

    async def execute(self, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """执行财经新闻检索。

        Args:
            inputs: 入参，包含 ``keywords``（必填）与 ``limit``（可空）。
            config: 项目级配置，可包含 ``api_endpoint`` 等。

        Returns:
            新闻数据 dict：
            - ``total``: 命中总数
            - ``items``: 新闻列表，每项含 title / summary / source / published_at / url
        """
        # 从入参提取关键词
        keywords: list[str] = inputs.get("keywords", [])
        # 返回条数，默认 10
        limit: int = inputs.get("limit", 10)

        # TODO: 接入真实新闻数据源
        # Mock：为每个关键词生成一条新闻
        items: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        for i, kw in enumerate(keywords[:limit]):
            # 发布时间按小时递减，模拟时效性
            published_at = (now - timedelta(hours=i)).isoformat()
            items.append({
                "title": f"关于 {kw} 的最新财经报道",
                "summary": f"本文讨论了 {kw} 相关的市场动态与投资机会。",
                "source": "Mock 财经通讯社",
                "published_at": published_at,
                "url": f"https://example.com/news/{kw}",
            })

        return {
            "total": len(items),
            "items": items,
        }
