"""职位检索 Handler（占位实现）。

对应 SubTask 13.3：``job_search`` 工具的具体实现。

当前为 Mock 实现，返回模拟数据。
TODO: 接入真实职位数据源（如拉勾、BOSS 直聘 API）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


class JobSearchHandler:
    """职位检索 Handler。

    执行 ``job_search`` 工具调用，按关键词、城市、薪资等条件检索职位。
    当前为 Mock 实现，返回固定模拟数据。
    """

    async def execute(self, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """执行职位检索。

        Args:
            inputs: 入参，包含 ``keywords``（必填）、``city``、``salary_min``、``limit``。
            config: 项目级配置，可包含 ``api_endpoint`` 等。

        Returns:
            职位数据 dict：
            - ``total``: 命中总数
            - ``items``: 职位列表，每项含 title / company / salary / city / url
        """
        # 从入参提取检索条件
        keywords: list[str] = inputs.get("keywords", [])
        city: str | None = inputs.get("city")
        salary_min: int | None = inputs.get("salary_min")
        # 返回条数，默认 10
        limit: int = inputs.get("limit", 10)

        # TODO: 接入真实职位数据源
        # Mock：为每个关键词生成一条职位
        items: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        for i, kw in enumerate(keywords[:limit]):
            # Mock 薪资字符串：若指定 salary_min 则基于此生成
            base_salary = salary_min if salary_min else 15000
            salary_str = f"{base_salary + i * 1000}-{base_salary + i * 1000 + 5000} 元/月"
            # Mock 城市：使用入参或默认北京
            job_city = city if city else "北京"
            items.append({
                "title": f"{kw} 工程师",
                "company": f"Mock 公司 {i + 1}",
                "salary": salary_str,
                "city": job_city,
                "url": f"https://example.com/jobs/{kw}/{i}",
            })

        return {
            "total": len(items),
            "items": items,
        }
