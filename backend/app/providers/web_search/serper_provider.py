"""Serper.dev Web 搜索 Provider：调用 Google 搜索结果 API。

对应 SubTask 12.1：实现 ``SerperWebSearchProvider``，通过 Serper.dev API
获取 Google 搜索结果。Serper 是封装 Google 搜索的第三方服务，
覆盖度高、响应快（通常 < 1s），适合作为首选联网搜索后端。

Serper API 调用方式
--------------------
- 端点：``https://google.serper.dev/search``
- 方法：POST（注意是 POST 而非 GET，查询参数放 Body 而非 URL）
- 认证：请求头 ``X-API-KEY: {api_key}``（非 Bearer Token）
- 请求体：``{"q": "查询词", "num": 5}``
- 响应体：``{"organic": [{"title": "...", "link": "...", "snippet": "...", "date": "..."}]}``

设计要点
--------
1. **5 秒超时**：研究链路整体硬超时 15s，联网搜索仅占其中一段，
   5s 是搜索接口的合理上限。超时抛 ``ExternalSourceTimeoutError``，
   上层捕获后返回降级结果（``degraded=True``），不中断研究。
2. **错误分类**：
   - 超时（``httpx.TimeoutException``）→ ``ExternalSourceTimeoutError``（HTTP 504）
   - 其他错误（连接失败、4xx/5xx、JSON 解析失败）→ ``ExternalSourceFailedError``（HTTP 502）
   分类异常便于上层区分"慢"与"坏"，记录不同的降级原因。
3. **不复用客户端**：每次 ``search`` 创建独立 ``httpx.AsyncClient``，
   避免长连接在低频搜索场景下被对端关闭导致下次请求失败。
   搜索接口调用频率低（每次研究 1 次），连接复用收益有限。
4. **不重试**：搜索超时通常意味着后端异常或网络抖动，重试会加倍延迟
   并挤占研究链路的剩余时间预算。直接降级比"重试后成功"更可控。
"""
from __future__ import annotations

import logging
from datetime import datetime

import httpx

from app.core.exceptions import ExternalSourceFailedError, ExternalSourceTimeoutError
from app.providers.web_search import WebSearchResult

logger = logging.getLogger(__name__)


class SerperWebSearchProvider:
    """Serper.dev Web 搜索 Provider。

    调用 ``https://google.serper.dev/search`` 获取 Google 搜索结果。

    Attributes:
        api_key: Serper API Key，从 ``settings.web_search_api_key`` 注入。
        timeout: 超时秒数，默认 5s，对应 ``settings.web_search_timeout_seconds``。
    """

    # Serper API 端点：固定不变，构造时无需参数化
    SERPER_ENDPOINT = "https://google.serper.dev/search"

    def __init__(self, api_key: str, timeout: int = 5) -> None:
        """初始化 Serper Web 搜索 Provider。

        Args:
            api_key: Serper API Key，请求头 ``X-API-KEY``。
            timeout: 超时秒数，默认 5s。超过此值抛 ``ExternalSourceTimeoutError``。
        """
        self.api_key = api_key
        self.timeout = timeout
        # 请求头：Serper 使用 X-API-KEY 认证（非 Bearer Token）
        self._headers = {
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
        }

    async def search(
        self, query: str, max_results: int = 5
    ) -> list[WebSearchResult]:
        """执行 Serper 搜索，返回结果列表。

        流程：
            1. 构造请求体 ``{"q": query, "num": max_results}``
            2. POST 到 Serper 端点，超时 ``self.timeout`` 秒
            3. 解析响应 ``organic`` 数组，逐条转换为 ``WebSearchResult``
            4. 超时抛 ``ExternalSourceTimeoutError``，其他错误抛 ``ExternalSourceFailedError``

        Args:
            query: 查询字符串，由研究链路根据用户问题构造。
            max_results: 最大结果数，默认 5，对应 PRD "最多 5 条结果"。

        Returns:
            ``WebSearchResult`` 列表，长度 ≤ ``max_results``。

        Raises:
            ExternalSourceTimeoutError: 搜索超时（超过 ``self.timeout`` 秒）。
            ExternalSourceFailedError: Serper 返回错误、连接失败或响应解析失败。
        """
        # 请求体：q=查询词，num=结果数上限
        payload = {"q": query, "num": max_results}

        # 每次创建独立客户端：搜索频率低，复用连接收益有限，
        # 且长连接可能被 Serper 端关闭导致下次失败
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    self.SERPER_ENDPOINT,
                    json=payload,
                    headers=self._headers,
                )
            except httpx.TimeoutException as exc:
                # 超时：5s 内未收到响应，抛 TimeoutError 触发降级
                logger.warning("Serper 搜索超时（query=%s）：%s", query, exc)
                raise ExternalSourceTimeoutError(
                    f"Serper 搜索超时：{exc}",
                    details={"query": query, "timeout": self.timeout},
                ) from exc
            except httpx.HTTPError as exc:
                # 其他网络错误（连接失败、DNS 解析失败等）：抛 FailedError
                logger.warning("Serper 搜索网络错误（query=%s）：%s", query, exc)
                raise ExternalSourceFailedError(
                    f"Serper 搜索网络错误：{exc}",
                    details={"query": query},
                ) from exc

        # HTTP 状态码非 2xx：业务错误（API Key 无效、配额耗尽等）
        if response.status_code >= 400:
            logger.warning(
                "Serper 搜索返回 %d（query=%s）：%s",
                response.status_code,
                query,
                response.text,
            )
            raise ExternalSourceFailedError(
                f"Serper 搜索返回 {response.status_code}",
                details={
                    "query": query,
                    "status_code": response.status_code,
                    "response": response.text,
                },
            )

        # 解析 JSON 响应
        try:
            data = response.json()
        except Exception as exc:
            # JSON 解析失败：响应体格式异常
            logger.warning("Serper 响应 JSON 解析失败（query=%s）：%s", query, exc)
            raise ExternalSourceFailedError(
                f"Serper 响应 JSON 解析失败：{exc}",
                details={"query": query, "response": response.text},
            ) from exc

        # organic 数组：Google 自然搜索结果（非广告）
        organic = data.get("organic") or []
        results: list[WebSearchResult] = []
        for item in organic:
            # 逐条解析，单条字段缺失时跳过（容错）
            title = item.get("title") or ""
            url = item.get("link") or ""
            snippet = item.get("snippet") or ""
            # date 字段：如 "3 days ago"，简单保留字符串后再尝试解析为时间
            # Serper 返回的 date 格式不固定，此处仅尝试解析，失败则置 None
            published_at = self._parse_date(item.get("date"))

            # URL 为空的结果无意义（无法后续提取），跳过
            if not url:
                continue

            results.append(
                WebSearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    published_at=published_at,
                )
            )

            # 已达到 max_results 上限则停止（Serper 可能返回多于 num 的结果）
            if len(results) >= max_results:
                break

        return results

    @staticmethod
    def _parse_date(date_str: str | None) -> datetime | None:
        """尝试解析 Serper 返回的日期字符串为 ``datetime``。

        Serper 的 ``date`` 字段格式不固定（如 "3 days ago"、"2024-01-01"、
        "Jan 1, 2024"），此处仅做尽力解析，失败返回 None。
        研究链路对发布时间无强依赖，缺失不影响主流程。

        Args:
            date_str: 日期字符串，可空。

        Returns:
            解析成功的 ``datetime`` 实例；失败或输入为空返回 None。
        """
        if not date_str:
            return None
        # 尝试 ISO 格式（如 "2024-01-01"）
        try:
            return datetime.fromisoformat(date_str)
        except ValueError:
            # 其他格式暂不支持，返回 None
            # TODO: 后续可扩展 dateutil.parser 解析更多格式
            return None
