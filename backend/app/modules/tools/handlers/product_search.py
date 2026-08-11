"""商品检索 Handler（占位实现）。

对应 SubTask 13.3：``product_search`` 工具的具体实现。

当前为 Mock 实现，返回模拟数据。
TODO: 接入真实商品数据源（如京东、淘宝开放平台 API）。
"""
from __future__ import annotations

from typing import Any


class ProductSearchHandler:
    """商品检索 Handler。

    执行 ``product_search`` 工具调用，按关键词、类目、价格区间检索商品。
    当前为 Mock 实现，返回固定模拟数据。
    """

    async def execute(self, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """执行商品检索。

        Args:
            inputs: 入参，包含 ``keywords``（必填）、``category``、``price_min``、
                ``price_max``、``limit``。
            config: 项目级配置，可包含 ``api_endpoint`` 等。

        Returns:
            商品数据 dict：
            - ``total``: 命中总数
            - ``items``: 商品列表，每项含 title / price / sales / url
        """
        # 从入参提取检索条件
        keywords: list[str] = inputs.get("keywords", [])
        category: str | None = inputs.get("category")
        price_min: float | None = inputs.get("price_min")
        price_max: float | None = inputs.get("price_max")
        # 返回条数，默认 10
        limit: int = inputs.get("limit", 10)

        # TODO: 接入真实商品数据源
        # Mock：为每个关键词生成一条商品
        items: list[dict[str, Any]] = []
        for i, kw in enumerate(keywords[:limit]):
            # Mock 价格：若指定 price_min 则基于此生成
            base_price = price_min if price_min else 99.0
            # 若指定 price_max 则不超过上限
            price = base_price + i * 10.0
            if price_max is not None and price > price_max:
                price = price_max
            # Mock 类目：使用入参或默认"综合"
            product_category = category if category else "综合"
            items.append({
                "title": f"{kw} 商品 {i + 1}（{product_category}）",
                "price": price,
                "sales": 1000 - i * 100,  # Mock 销量
                "url": f"https://example.com/products/{kw}/{i}",
            })

        return {
            "total": len(items),
            "items": items,
        }
