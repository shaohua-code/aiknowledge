"""网页提取服务：从 URL 提取标题、摘要、正文、发布时间。

对应 SubTask 12.2：实现 ``WebPageExtractor`` 与 ``WebPageContent``，
将搜索结果 URL 转为可读正文，供研究链路作为临时证据使用。

提取流程
--------
1. **抓取页面**：用 ``httpx`` 异步 GET，限制响应体 ≤ 5MB（防止超大页面拖垮内存），
   超时 5s（与研究链路整体超时预算匹配）。
2. **提取正文**：用 ``trafilatura.extract`` 提取主要正文内容，
   ``include_links=False`` 去除链接，``include_tables=False`` 去除表格，
   保证正文干净简洁，适合作为模型上下文。
3. **提取标题**：优先用 ``trafilatura.extract_metadata`` 获取结构化标题；
   失败则用 BeautifulSoup 解析 ``<title>`` 标签作为降级方案。
4. **提取发布时间**：解析 ``<meta property="article:published_time">`` 或
   ``<time datetime="...">`` 标签，失败则置 None（非所有页面都有发布时间）。
5. **截取摘要**：正文前 300 字符作为摘要，便于研究链路快速预览。

为什么用 Trafilatura？
----------------------
Trafilatura 是专业的网页正文提取库，相比 BeautifulSoup 手写规则：
1. **自动识别主内容区**：去除导航、广告、侧边栏、页脚等噪音。
2. **对各种页面结构鲁棒**：新闻、博客、文档、论文均能提取。
3. **保留语义结构**：段落、列表、引用等结构化输出，便于模型理解。
4. **轻量无依赖**：纯 Python，无外部服务依赖。

降级机制
--------
- Trafilatura 提取失败（返回 None）：尝试用 BeautifulSoup 提取 ``<p>`` 文本拼接。
- 标题提取失败：用 URL 最后一段作为标题（如 ``/article/123`` → ``123``）。
- 任何步骤失败都不抛异常，返回部分字段（``content=None``），
  研究链路据此跳过此证据或仅使用搜索摘要。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import httpx
import trafilatura
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


@dataclass
class WebPageContent:
    """网页提取结果。

    由 ``WebPageExtractor.extract`` 返回，承载单页面的标题、摘要、正文、
    发布时间。研究链路据此构造 ``WebEvidence`` 作为临时证据。

    Attributes:
        title: 页面标题。提取失败时降级为 URL 最后一段。
        url: 原始 URL，用于溯源。
        snippet: 摘要（正文前 300 字符），便于快速预览。
            正文为空时使用空字符串。
        content: 正文（Trafilatura 提取结果）。提取失败时为 None，
            研究链路据此跳过此证据或仅使用搜索摘要。
        published_at: 发布时间，可空（非所有页面都有发布时间）。
    """

    title: str
    url: str
    snippet: str
    content: str | None
    published_at: datetime | None = None


class WebPageExtractor:
    """网页提取器：从 URL 提取标题、摘要、正文、发布时间。

    设计要点
    --------
    1. **5MB 响应体限制**：防止超大页面（如长篇 PDF、视频页面）拖垮内存。
       5MB 对应约 500 万字符，足够覆盖绝大多数文章页面。
    2. **5 秒超时**：与 ``settings.web_search_timeout_seconds`` 一致，
       保证网页提取不会挤占研究链路的剩余时间预算。
    3. **降级链路**：Trafilatura 失败 → BeautifulSoup ``<p>`` 拼接 → 空正文。
       每层降级都记录日志，便于排查。
    4. **不抛异常**：任何提取步骤失败都返回部分结果，
       研究链路据此决定是否采信此证据，而非中断整个研究。
    """

    # 响应体大小上限：5MB，防止超大页面拖垮内存
    MAX_RESPONSE_BYTES = 5 * 1024 * 1024
    # 摘要长度：正文前 300 字符，便于快速预览
    SNIPPET_LENGTH = 300

    def __init__(self, timeout: int = 5) -> None:
        """初始化网页提取器。

        Args:
            timeout: 单页抓取超时秒数，默认 5s。
        """
        self.timeout = timeout
        # 请求头：模拟浏览器，避免被部分站点识别为爬虫拦截
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    async def extract(self, url: str, timeout: int | None = None) -> WebPageContent:
        """从 URL 提取页面内容。

        流程：
            1. httpx GET 抓取页面（限制响应体 ≤ 5MB，超时 5s）
            2. Trafilatura 提取正文（include_links=False, include_tables=False）
            3. 提取标题（trafilatura.extract_metadata 或 BeautifulSoup <title>）
            4. 提取发布时间（<meta property="article:published_time"> 或 <time>）
            5. 截取摘要（正文前 300 字符）
            6. 任何步骤失败降级处理，不抛异常

        Args:
            url: 待提取的页面 URL。
            timeout: 单页超时秒数，None 表示使用实例默认值（5s）。

        Returns:
            ``WebPageContent`` 实例。抓取或提取失败时 ``content=None``，
            ``title`` 降级为 URL 最后一段，``snippet`` 为空字符串。
        """
        # 超时：参数优先，否则用实例默认值
        effective_timeout = timeout if timeout is not None else self.timeout

        # 步骤 1：抓取页面 HTML
        html = await self._fetch_html(url, effective_timeout)
        if not html:
            # 抓取失败：返回空内容（content=None），不抛异常
            logger.warning("网页抓取失败，返回空内容：%s", url)
            return WebPageContent(
                title=self._fallback_title(url),
                url=url,
                snippet="",
                content=None,
                published_at=None,
            )

        # 步骤 2：用 Trafilatura 提取正文
        content = self._extract_content(html)

        # 步骤 3：提取标题
        title = self._extract_title(html, url)

        # 步骤 4：提取发布时间
        published_at = self._extract_published_at(html)

        # 步骤 5：截取摘要（正文前 300 字符）
        snippet = self._make_snippet(content)

        return WebPageContent(
            title=title,
            url=url,
            snippet=snippet,
            content=content,
            published_at=published_at,
        )

    async def _fetch_html(self, url: str, timeout: int) -> str | None:
        """用 httpx 抓取页面 HTML，限制响应体大小与超时。

        Args:
            url: 待抓取的 URL。
            timeout: 超时秒数。

        Returns:
            HTML 字符串；抓取失败（超时、网络错误、响应体超限）返回 None。
        """
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                # stream 流式读取：便于在响应体超过 5MB 时提前中断
                async with client.stream("GET", url, headers=self._headers) as response:
                    # 非 2xx 状态码：返回 None（如 404、503）
                    if response.status_code >= 400:
                        logger.warning(
                            "网页抓取返回 %d：%s",
                            response.status_code,
                            url,
                        )
                        return None

                    # 流式读取：累计字节数，超过 5MB 则中断
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > self.MAX_RESPONSE_BYTES:
                            # 超过 5MB：中断读取，使用已读部分
                            logger.warning(
                                "网页响应体超过 5MB，截断：%s（已读 %d 字节）",
                                url,
                                total,
                            )
                            break
                        chunks.append(chunk)

                    # 合并字节并解码为字符串
                    raw = b"".join(chunks)
                    # 推断编码：优先响应头 charset，失败用 UTF-8 容错
                    encoding = response.encoding or "utf-8"
                    try:
                        return raw.decode(encoding, errors="replace")
                    except (LookupError, TypeError):
                        # 未知编码：降级 UTF-8 + replace
                        return raw.decode("utf-8", errors="replace")
        except httpx.TimeoutException:
            # 超时：返回 None，上层降级处理
            logger.warning("网页抓取超时（%ds）：%s", timeout, url)
            return None
        except httpx.HTTPError as exc:
            # 其他网络错误：返回 None
            logger.warning("网页抓取网络错误：%s（%s）", url, exc)
            return None
        except Exception as exc:
            # 未预期异常：兜底返回 None，避免中断研究链路
            logger.warning("网页抓取未预期异常：%s（%s）", url, exc, exc_info=True)
            return None

    def _extract_content(self, html: str) -> str | None:
        """用 Trafilatura 提取正文。

        Args:
            html: 页面 HTML 字符串。

        Returns:
            正文字符串；Trafilatura 提取失败时尝试 BeautifulSoup 降级，
            仍失败返回 None。
        """
        # Trafilatura 提取：include_links=False 去链接，include_tables=False 去表格
        # 保证正文干净简洁，适合作为模型上下文
        try:
            content = trafilatura.extract(
                html,
                include_links=False,
                include_tables=False,
                include_images=False,
                favor_precision=True,  # 精度优先：宁可少提取也不要噪音
            )
            if content:
                return content
        except Exception as exc:
            # Trafilatura 异常：记录日志，尝试降级
            logger.warning("Trafilatura 提取异常：%s", exc)

        # 降级方案：BeautifulSoup 提取所有 <p> 标签文本拼接
        # 此方案噪音较多，但保证有内容可用
        try:
            soup = BeautifulSoup(html, "html.parser")
            paragraphs = soup.find_all("p")
            text = "\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
            return text if text else None
        except Exception as exc:
            # 降级也失败：返回 None
            logger.warning("BeautifulSoup 降级提取失败：%s", exc)
            return None

    def _extract_title(self, html: str, url: str) -> str:
        """提取页面标题。

        提取优先级：
            1. ``trafilatura.extract_metadata`` 的 title 字段（结构化提取，最准）
            2. BeautifulSoup 解析 ``<title>`` 标签
            3. BeautifulSoup 解析 ``<meta property="og:title">``
            4. URL 最后一段作为兜底标题

        Args:
            html: 页面 HTML 字符串。
            url: 原始 URL，用于兜底标题生成。

        Returns:
            页面标题字符串。所有提取方式均失败时返回 URL 最后一段。
        """
        # 优先级 1：Trafilatura 元数据
        try:
            metadata = trafilatura.extract_metadata(html)
            if metadata and metadata.title:
                return metadata.title.strip()
        except Exception as exc:
            logger.debug("Trafilatura 元数据提取异常：%s", exc)

        # 优先级 2 & 3：BeautifulSoup 解析 <title> 与 og:title
        try:
            soup = BeautifulSoup(html, "html.parser")
            # <title> 标签
            title_tag = soup.find("title")
            if title_tag and title_tag.get_text(strip=True):
                return title_tag.get_text(strip=True)
            # og:title meta
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                return og_title["content"].strip()
        except Exception as exc:
            logger.debug("BeautifulSoup 标题提取异常：%s", exc)

        # 优先级 4：URL 最后一段作为兜底
        return self._fallback_title(url)

    def _extract_published_at(self, html: str) -> datetime | None:
        """提取页面发布时间。

        提取优先级：
            1. ``<meta property="article:published_time">``（新闻类页面标准）
            2. ``<meta name="publishdate">``（部分站点使用）
            3. ``<time datetime="...">`` 标签（HTML5 标准）

        Args:
            html: 页面 HTML 字符串。

        Returns:
            发布时间 ``datetime``；提取失败返回 None。
        """
        try:
            soup = BeautifulSoup(html, "html.parser")

            # 优先级 1：article:published_time meta
            meta = soup.find("meta", property="article:published_time")
            if meta and meta.get("content"):
                dt = self._parse_datetime(meta["content"])
                if dt:
                    return dt

            # 优先级 2：publishdate meta
            meta = soup.find("meta", attrs={"name": "publishdate"})
            if meta and meta.get("content"):
                dt = self._parse_datetime(meta["content"])
                if dt:
                    return dt

            # 优先级 3：<time datetime="..."> 标签
            time_tag = soup.find("time")
            if time_tag:
                # 优先 datetime 属性，其次标签文本
                dt_str = time_tag.get("datetime") or time_tag.get_text(strip=True)
                if dt_str:
                    dt = self._parse_datetime(dt_str)
                    if dt:
                        return dt
        except Exception as exc:
            logger.debug("发布时间提取异常：%s", exc)

        return None

    @staticmethod
    def _parse_datetime(dt_str: str) -> datetime | None:
        """尝试解析日期时间字符串为 ``datetime``。

        支持格式：
            - ISO 8601（如 ``2024-01-01T12:00:00+08:00``）
            - 简单日期（如 ``2024-01-01``）

        Args:
            dt_str: 日期时间字符串。

        Returns:
            解析成功的 ``datetime``；失败返回 None。
        """
        if not dt_str:
            return None
        try:
            # fromisoformat 支持 ISO 8601，Python 3.11+ 已支持时区
            return datetime.fromisoformat(dt_str)
        except ValueError:
            # 其他格式暂不支持
            # TODO: 后续可引入 dateutil.parser 解析更多格式
            return None

    def _make_snippet(self, content: str | None) -> str:
        """从正文截取摘要（前 300 字符）。

        Args:
            content: 正文字符串，可空。

        Returns:
            摘要字符串。正文为空返回空字符串。
            超过 300 字符则截断并追加省略号。
        """
        if not content:
            return ""
        # 去除首尾空白与多余换行
        text = content.strip()
        if len(text) <= self.SNIPPET_LENGTH:
            return text
        # 截断并追加省略号
        return text[: self.SNIPPET_LENGTH] + "..."

    @staticmethod
    def _fallback_title(url: str) -> str:
        """从 URL 生成兜底标题。

        取 URL 路径最后一段作为标题，去除查询参数与文件扩展名。
        如 ``https://example.com/article/123.html?q=1`` → ``123``。

        Args:
            url: 原始 URL。

        Returns:
            兜底标题字符串。
        """
        from urllib.parse import urlparse

        try:
            parsed = urlparse(url)
            path = parsed.path.rstrip("/")
            if not path:
                return url
            # 取最后一段
            last_segment = path.split("/")[-1]
            # 去除文件扩展名
            if "." in last_segment:
                last_segment = last_segment.rsplit(".", 1)[0]
            return last_segment or url
        except Exception:
            return url
