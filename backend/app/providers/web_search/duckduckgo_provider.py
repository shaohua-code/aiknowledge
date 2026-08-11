"""DuckDuckGo Web 搜索 Provider：备用实现。

对应 SubTask 12.1：实现 ``DuckDuckGoWebSearchProvider``，作为 Serper 的备用。
无需 API Key，但结果质量与稳定性不如 Serper，适合开发/测试环境或无 Serper Key 时使用。

实现方式
--------
抓取 DuckDuckGo HTML 搜索页面（``https://html.duckduckgo.com/html/``），
用 BeautifulSoup 解析结果列表。此方式比官方 API 更稳定（DuckDuckGo 官方 API
限流严格），但解析逻辑依赖 HTML 结构，可能因页面改版而失效。

设计要点
--------
1. **简化实现**：本模块标注 TODO，仅满足基本功能。生产环境建议优先使用 Serper。
2. **5 秒超时**：与 Serper 一致，超时抛 ``ExternalSourceTimeoutError``。
3. **HTML 解析容错**：DuckDuckGo HTML 结构可能变化，解析失败时返回已解析的部分结果，
   不抛异常（避免单次失败影响整个研究链路）。
4. **POST 表单**：DuckDuckGo HTML 接口需 POST 表单（``q=查询词``），
   而非 JSON Body，注意与 Serper 的差异。
"""
from __future__ import annotations

import logging

import httpx
from bs4 import BeautifulSoup

from app.core.exceptions import ExternalSourceFailedError, ExternalSourceTimeoutError
from app.providers.web_search import WebSearchResult

logger = logging.getLogger(__name__)


class DuckDuckGoWebSearchProvider:
    """DuckDuckGo Web 搜索 Provider（备用）。

    通过抓取 DuckDuckGo HTML 搜索页解析结果，无需 API Key。

    Attributes:
        timeout: 超时秒数，默认 5s。
    """

    # DuckDuckGo HTML 搜索端点：比官方 API 限流宽松
    DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"

    def __init__(self, timeout: int = 5) -> None:
        """初始化 DuckDuckGo Web 搜索 Provider。

        Args:
            timeout: 超时秒数，默认 5s。
        """
        self.timeout = timeout
        # 请求头：模拟浏览器，避免被识别为爬虫拦截
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Content-Type": "application/x-www-form-urlencoded",
        }

    async def search(
        self, query: str, max_results: int = 5
    ) -> list[WebSearchResult]:
        """执行 DuckDuckGo 搜索，返回结果列表。

        流程：
            1. POST 表单 ``{"q": query}`` 到 DuckDuckGo HTML 端点
            2. 用 BeautifulSoup 解析结果列表（``result__a`` 链接、``result__snippet`` 摘要）
            3. 逐条转换为 ``WebSearchResult``

        Args:
            query: 查询字符串。
            max_results: 最大结果数，默认 5。

        Returns:
            ``WebSearchResult`` 列表，长度 ≤ ``max_results``。

        Raises:
            ExternalSourceTimeoutError: 搜索超时。
            ExternalSourceFailedError: 网络错误或 HTTP 状态码异常。
        """
        # TODO: 此实现为简化备用版，生产环境建议优先使用 Serper Provider。
        # 后续可考虑集成 duckduckgo_search 库（更稳定），但该库非 pyproject 依赖，
        # 需评估引入成本。

        # 表单数据：q=查询词，注意 DuckDuckGo 用表单而非 JSON
        form_data = {"q": query}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    self.DDG_HTML_ENDPOINT,
                    data=form_data,
                    headers=self._headers,
                )
            except httpx.TimeoutException as exc:
                # 超时：抛 TimeoutError 触发降级
                logger.warning("DuckDuckGo 搜索超时（query=%s）：%s", query, exc)
                raise ExternalSourceTimeoutError(
                    f"DuckDuckGo 搜索超时：{exc}",
                    details={"query": query, "timeout": self.timeout},
                ) from exc
            except httpx.HTTPError as exc:
                # 其他网络错误：抛 FailedError
                logger.warning(
                    "DuckDuckGo 搜索网络错误（query=%s）：%s", query, exc
                )
                raise ExternalSourceFailedError(
                    f"DuckDuckGo 搜索网络错误：{exc}",
                    details={"query": query},
                ) from exc

        # HTTP 状态码非 2xx：抛 FailedError
        if response.status_code >= 400:
            logger.warning(
                "DuckDuckGo 搜索返回 %d（query=%s）",
                response.status_code,
                query,
            )
            raise ExternalSourceFailedError(
                f"DuckDuckGo 搜索返回 {response.status_code}",
                details={"query": query, "status_code": response.status_code},
            )

        # 解析 HTML：使用 BeautifulSoup 提取结果
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[WebSearchResult] = []

        # DuckDuckGo HTML 结构：每个结果在 class="result" 的 div 内
        # 标题链接：class="result__a"，摘要：class="result__snippet"
        for item in soup.select(".result"):
            link_tag = item.select_one(".result__a")
            snippet_tag = item.select_one(".result__snippet")

            if not link_tag:
                # 无链接的结果无意义，跳过
                continue

            title = link_tag.get_text(strip=True)
            url = link_tag.get("href", "")
            snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

            # DuckDuckGo 的 href 可能是重定向链接（//duckduckgo.com/l/?uddg=...），
            # 此处简化处理，不解析重定向，直接保留原值
            # TODO: 后续可解析 uddg 参数还原真实 URL

            results.append(
                WebSearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    published_at=None,  # DuckDuckGo HTML 不返回发布时间
                )
            )

            # 达到上限则停止
            if len(results) >= max_results:
                break

        return results
