"""Web 搜索 Provider 抽象层：构造查询、一轮搜索、最多 5 条结果。

对应 SubTask 12.1：定义 ``WebSearchProvider`` Protocol 与工厂函数
``get_web_search_provider``，业务代码依赖抽象协议而非具体实现，
便于在 Serper / DuckDuckGo / Bing 等搜索后端间切换而无需修改研究链路。

设计要点
--------
1. ``WebSearchProvider`` 是 ``Protocol``（结构化子类型），实现类无需显式继承，
   只需提供 ``search`` 方法即可被工厂返回，符合"鸭子类型"。
2. ``WebSearchResult`` 使用 ``dataclass`` 承载单条搜索结果（标题、URL、摘要、
   发布时间），与具体 Provider 解耦，业务层只消费此结构。
3. ``get_web_search_provider`` 根据 ``settings.web_search_provider`` 返回对应实现，
   未配置（空字符串）时返回 ``None``，由调用方据此触发"联网搜索禁用"降级，
   而非抛异常——这符合研究链路"宁可降级不可中断"的容错原则。
4. ``search`` 方法固定 5 秒超时（由 ``settings.web_search_timeout_seconds`` 控制），
   超时抛 ``ExternalSourceTimeoutError``，其他错误抛 ``ExternalSourceFailedError``，
   上层 ``WebResearchService`` 捕获后返回降级结果（``degraded=True``）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Protocol, runtime_checkable


@dataclass
class WebSearchResult:
    """单条联网搜索结果。

    由 Provider 从搜索后端响应解析得到，业务层（``WebResearchService``）
    据此构造 ``WebEvidence``，再交给研究链路作为临时证据使用。

    Attributes:
        title: 结果标题（搜索后端返回的页面标题）。
        url: 结果 URL（点击跳转地址，用于后续网页提取与域名过滤）。
        snippet: 摘要片段（搜索后端返回的页面摘要，通常 ≤ 200 字）。
        published_at: 发布时间，可空（部分搜索后端不返回此字段）。
    """

    title: str
    url: str
    snippet: str
    published_at: datetime | None = None


@runtime_checkable
class WebSearchProvider(Protocol):
    """联网搜索 Provider 抽象。

    所有实现（如 ``SerperWebSearchProvider``）必须满足此协议，
    研究链路通过此协议访问 Provider，不依赖具体实现类。

    协议要求的方法签名
    ------------------
    ``async search(query: str, max_results: int = 5) -> list[WebSearchResult]``

    - 输入：查询字符串与最大结果数（默认 5，对应 PRD "最多 5 条结果"）
    - 输出：``WebSearchResult`` 列表，长度 ≤ ``max_results``
    - 超时：实现需在 ``settings.web_search_timeout_seconds``（默认 5s）内返回，
      超时抛 ``ExternalSourceTimeoutError``
    - 其他错误：抛 ``ExternalSourceFailedError``，由上层降级处理

    为什么固定 5 秒超时？
    --------------------
    研究链路整体硬超时 ``research_hard_timeout_seconds`` 默认 15s，
    联网搜索仅是其中一环，后续还要做网页提取与模型生成。
    5s 是搜索接口的合理上限：正常请求 < 2s，5s 已留足网络抖动余量，
    超过 5s 通常意味着后端异常，继续等待会拖垮整个研究链路。
    """

    async def search(
        self, query: str, max_results: int = 5
    ) -> list[WebSearchResult]:
        """执行搜索，返回结果列表。

        Args:
            query: 查询字符串，由研究链路根据用户问题构造。
            max_results: 最大结果数，默认 5，对应 PRD "最多 5 条结果"。

        Returns:
            ``WebSearchResult`` 列表，长度 ≤ ``max_results``。

        Raises:
            ExternalSourceTimeoutError: 搜索超时（超过 5s）。
            ExternalSourceFailedError: 搜索后端返回错误（非超时）。
        """
        ...


@lru_cache
def get_web_search_provider() -> WebSearchProvider | None:
    """获取 Web 搜索 Provider 单例。

    根据 ``settings.web_search_provider`` 返回对应实现：
        - ``serper``：返回 ``SerperWebSearchProvider``，调用 Serper.dev API
          （Google 搜索结果 API，覆盖度高、响应快）
        - ``duckduckgo``：返回 ``DuckDuckGoWebSearchProvider``，备用实现
          （无需 API Key，但结果质量与稳定性不如 Serper）
        - 空字符串 / 未配置：返回 ``None``，触发"联网搜索禁用"降级

    为什么未配置返回 None 而非抛异常？
        研究链路遵循"宁可降级不可中断"原则。未配置搜索 Provider 时，
        研究仍可基于内部知识库完成（``knowledge_only`` 策略），
        抛异常会中断整个研究请求，违背容错设计。

    使用 ``lru_cache`` 缓存单例的原因：
        - Provider 内部持有 httpx.AsyncClient，重复创建会浪费连接池资源
        - 配置在运行期不变，缓存安全
        - 测试时可通过 ``get_web_search_provider.cache_clear()`` 重置

    Returns:
        满足 ``WebSearchProvider`` 协议的实例；未配置返回 ``None``。

    Raises:
        ValueError: 配置的 provider 名称不被支持（非空且未实现）。
    """
    # 延迟导入避免循环依赖：settings 依赖链较深，工厂函数运行时才需要
    from app.core.config import settings

    provider = (settings.web_search_provider or "").strip().lower()

    if not provider:
        # 未配置：返回 None，触发"联网搜索禁用"降级
        return None

    if provider == "serper":
        # Serper.dev：Google 搜索结果 API，需 API Key
        from app.providers.web_search.serper_provider import SerperWebSearchProvider

        return SerperWebSearchProvider(
            api_key=settings.web_search_api_key,
            timeout=settings.web_search_timeout_seconds,
        )

    if provider == "duckduckgo":
        # DuckDuckGo：备用 Provider，无需 API Key
        from app.providers.web_search.duckduckgo_provider import (
            DuckDuckGoWebSearchProvider,
        )

        return DuckDuckGoWebSearchProvider(
            timeout=settings.web_search_timeout_seconds,
        )

    # 未知 provider：抛 ValueError，提示开发者检查配置
    raise ValueError(f"不支持的 Web 搜索 Provider：{provider}")
