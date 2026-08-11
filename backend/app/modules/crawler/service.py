"""爬虫执行服务：编排完整采集流程。

对应 SubTask 19.5：实现 ``CrawlerService.run_crawl`` 串联采集全流程。

采集流程总览
------------
1. **加载采集源**：带 project_id 过滤查询 CrawlSource，二次校验归属与状态
2. **初始化运行记录**：CrawlRun.status='running'，started_at=now
3. **按 type 分发 URL 发现**：
   - SINGLE_PAGE：直接抓取 start_urls（通常 1 个）
   - URL_LIST：逐个抓取 start_urls
   - RSS：解析 RSS feed，提取条目 URL
   - SITEMAP：解析 sitemap.xml，提取 URL 列表
   - LIST_PAGE：抓取列表页，按 extract_rules 提取详情页 URL
4. **逐 URL 处理**：
   a. SSRF 校验（validate_url）
   b. 下载页面（httpx，限制响应体 ≤ 5MB，超时 10s，关闭自动重定向）
   c. 重定向后重新校验（validate_redirect），每跳都校验
   d. URL 规范化 + 哈希（normalize_url + compute_url_hash）
   e. 去重查询（get_by_canonical_hash），已存在且 content_hash 未变 → duplicate_count++
   f. HTML 清洗 + 正文提取（sanitize_html + extract_text）
   g. 计算 content_hash
   h. 增量更新：已存在但 content_hash 变化 → 创建新版本 Document
   i. 创建 CrawlPage 记录
   j. 按 import_policy 处理：
      - REVIEW_REQUIRED → WebMaterial(status='pending')
      - AUTO_IMPORT → Document + IngestionJob
      - EVIDENCE_ONLY → WebMaterial(status='pending')，TODO 过期清理
   k. 更新计数（success_count / imported_count）
5. **失败处理**：failed_count++，记录 error_code
6. **限制遵守**：limits.maxPagesPerRun / maxDepth / requestIntervalMs / concurrencyPerDomain
7. **完成**：CrawlRun.status='success'，各 count，completed_at=now
8. **返回结果摘要**

入库策略说明
------------
- REVIEW_REQUIRED：采集结果先入待审核资料池（web_materials，status='pending'），
  人工审核通过后（POST /crawl-pages/{pageId}/approve）才入库到知识库。
  适用于不可信来源（如用户提交的 URL 列表），避免污染知识库。
- AUTO_IMPORT：采集结果直接创建 Document + IngestionJob，触发向量化入库。
  适用于可信来源（如官方 API 文档、合作伙伴数据），减少人工成本。
- EVIDENCE_ONLY：仅作证据短期保存，不入知识库。
  适用于研究链路的临时证据，TODO: 后续通过定时任务过期清理。

增量更新机制
------------
- URL 相同 + content_hash 相同 → 跳过（duplicate_count++），避免重复入库
- URL 相同 + content_hash 变化 → 创建新版本 Document（保留历史版本支持回溯）
- URL 不存在 → 新建 CrawlPage + Document
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    CrawlRuleInvalidError,
    CrawlSourceNotFoundError,
    ExternalSourceFailedError,
)
from app.core.project_context import ProjectContext
from app.db.models.crawler import CrawlPage, CrawlRun, CrawlSource
from app.db.repositories.crawler import (
    CrawlPageRepository,
    CrawlRunRepository,
    CrawlSourceRepository,
    WebMaterialRepository,
)
from app.db.repositories.ingestion import IngestionJobRepository
from app.db.repositories.knowledge import DocumentRepository, KnowledgeBaseRepository
from app.modules.crawler.html_sanitizer import extract_text, extract_title, sanitize_html
from app.modules.crawler.ssrf_guard import validate_redirect, validate_url
from app.modules.crawler.url_utils import (
    compute_content_hash,
    compute_url_hash,
    normalize_url,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量：默认采集限制与超时
# ---------------------------------------------------------------------------
# 单页抓取超时（秒）：10s，平衡成功率与采集效率
_DEFAULT_TIMEOUT = 10.0
# 单页响应体上限：5MB，防止超大页面拖垮 Worker 内存
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
# 默认每页抓取间隔（毫秒）：1000ms，避免拖垮目标站点
_DEFAULT_REQUEST_INTERVAL_MS = 1000
# 默认单次运行最大抓取页面数：100
_DEFAULT_MAX_PAGES_PER_RUN = 100
# 默认最大深度：1（仅抓起始 URL，不递归）
_DEFAULT_MAX_DEPTH = 1
# 默认单域名并发数：1（串行抓取，礼貌爬虫）
_DEFAULT_CONCURRENCY_PER_DOMAIN = 1
# 浏览器 UA：模拟 Chrome，避免被部分站点识别为爬虫拦截
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class CrawlRunResult:
    """采集运行结果摘要。

    由 ``CrawlerService.run_crawl`` 返回，承载运行的关键计数与状态，
    用于 Celery 任务记录日志与回写 CrawlRun。

    Attributes:
        run_id: 运行记录 ID。
        source_id: 采集源 ID。
        status: 最终状态（success / failed）。
        discovered_count: 发现页面数。
        success_count: 成功抓取数。
        duplicate_count: 重复页面数（URL 去重）。
        failed_count: 失败页面数。
        imported_count: 入库数。
        error_code: 失败错误码，可空。
    """

    run_id: str
    source_id: str
    status: str
    discovered_count: int = 0
    success_count: int = 0
    duplicate_count: int = 0
    failed_count: int = 0
    imported_count: int = 0
    error_code: str | None = None


@dataclass
class _CrawlLimits:
    """采集限制配置（从 CrawlSource.limits 解析，带默认值兜底）。

    Attributes:
        max_pages_per_run: 单次运行最大抓取页面数。
        max_depth: 最大递归深度（1=仅起始 URL，2=起始+一层链接）。
        request_interval_ms: 同域名请求间隔（毫秒），礼貌爬虫。
        concurrency_per_domain: 单域名并发数（当前实现串行，预留扩展）。
    """

    max_pages_per_run: int = _DEFAULT_MAX_PAGES_PER_RUN
    max_depth: int = _DEFAULT_MAX_DEPTH
    request_interval_ms: int = _DEFAULT_REQUEST_INTERVAL_MS
    concurrency_per_domain: int = _DEFAULT_CONCURRENCY_PER_DOMAIN


@dataclass
class _FetchedPage:
    """单页抓取结果。

    由 ``_fetch_page`` 返回，承载抓取后的原始数据，供后续清洗与入库使用。

    Attributes:
        url: 实际访问的最终 URL（可能经重定向）。
        http_status: HTTP 状态码。
        html: 原始 HTML 字符串。
        content_type: 响应 Content-Type 头，可空。
    """

    url: str
    http_status: int
    html: str
    content_type: str | None = None


class CrawlerService:
    """爬虫执行服务：编排完整采集流程。

    使用方式
    --------
    在 Celery 任务中调用：
        service = CrawlerService()
        result = await service.run_crawl(ctx, crawl_source_id, run_id, db)

    设计要点
    --------
    1. **无状态服务**：每个请求创建独立实例，不持有跨请求状态。
    2. **SSRF 防护**：每次抓取前校验 URL，重定向后重新校验。
    3. **去重与增量更新**：基于 canonical_url_hash 去重，content_hash 检测增量。
    4. **入库策略分发**：按 import_policy 决定入待审核池 / 自动入库 / 仅作证据。
    5. **限制遵守**：maxPagesPerRun / requestIntervalMs 等，避免拖垮目标站点。
    """

    def __init__(self) -> None:
        """初始化爬虫服务。"""
        # 共用 httpx 客户端配置（每次 run_crawl 内部创建独立 client）
        self._headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    async def run_crawl(
        self,
        ctx: ProjectContext,
        crawl_source_id: str,
        run_id: str,
        db: AsyncSession,
    ) -> CrawlRunResult:
        """执行一次完整采集流程。

        流程
        ----
        1. 加载 CrawlSource（带 project_id 过滤，二次校验）
        2. 初始化 CrawlRun（status='running'，started_at=now）
        3. 按 source.type 分发 URL 发现
        4. 逐 URL 处理：SSRF 校验 → 下载 → 清洗 → 去重 → 入库
        5. 更新 CrawlRun（status='success'，各 count，completed_at）

        Args:
            ctx: 项目上下文，提供 project_id 用于隔离。
            crawl_source_id: 采集源 ID。
            run_id: 运行记录 ID（已由调用方创建，初始 status='pending'）。
            db: 异步数据库会话。

        Returns:
            CrawlRunResult: 运行结果摘要。

        Raises:
            CrawlSourceNotFoundError: 采集源不存在或不属于当前项目。
            CrawlRuleInvalidError: 采集源配置无效（如 start_urls 为空）。
        """
        # 初始化各 Repository：共享同一会话，保证事务一致性
        source_repo = CrawlSourceRepository(db)
        run_repo = CrawlRunRepository(db)
        page_repo = CrawlPageRepository(db)
        material_repo = WebMaterialRepository(db)
        doc_repo = DocumentRepository(db)
        kb_repo = KnowledgeBaseRepository(db)
        job_repo = IngestionJobRepository(db)

        # ------------------------------------------------------------------
        # 步骤 1：加载采集源（带 project_id 过滤，二次校验）
        # ------------------------------------------------------------------
        source = await source_repo.get_by_id(ctx, crawl_source_id)
        if source is None:
            # 采集源不存在或不属于当前项目：标记运行失败并抛错
            await run_repo.update(ctx, run_id, status="failed", error_code="SOURCE_NOT_FOUND")
            await db.commit()
            raise CrawlSourceNotFoundError(
                f"采集源 {crawl_source_id} 不存在",
                details={"crawlSourceId": crawl_source_id},
            )

        # 校验采集源状态：仅 active 状态可执行采集
        if source.status != "active":
            await run_repo.update(
                ctx, run_id, status="failed", error_code="SOURCE_NOT_ACTIVE"
            )
            await db.commit()
            raise CrawlRuleInvalidError(
                f"采集源 {source.code} 当前状态为 {source.status}，仅 active 状态可执行采集",
                details={"crawlSourceId": crawl_source_id, "status": source.status},
            )

        # 解析采集限制（带默认值兜底）
        limits = self._parse_limits(source.limits)

        # ------------------------------------------------------------------
        # 步骤 2：初始化 CrawlRun（status='running'，started_at=now）
        # ------------------------------------------------------------------
        now = datetime.now(timezone.utc)
        await run_repo.update(
            ctx, run_id, status="running", started_at=now
        )
        await db.commit()

        # 结果计数器（内存累加，最终回写 CrawlRun）
        discovered_count = 0
        success_count = 0
        duplicate_count = 0
        failed_count = 0
        imported_count = 0
        error_code: str | None = None

        try:
            # ----------------------------------------------------------
            # 步骤 3：按 source.type 分发 URL 发现
            # ----------------------------------------------------------
            # 发现的 URL 列表（待抓取）
            urls_to_fetch = await self._discover_urls(ctx, source, db, limits)
            discovered_count = len(urls_to_fetch)

            # ----------------------------------------------------------
            # 步骤 4：逐 URL 处理
            # ----------------------------------------------------------
            # 计数实际抓取的页面数（受 max_pages_per_run 限制）
            fetched_count = 0
            async with httpx.AsyncClient(
                timeout=_DEFAULT_TIMEOUT,
                follow_redirects=False,  # 关闭自动重定向，手动处理以校验每一跳
                headers=self._headers,
            ) as client:
                for url in urls_to_fetch:
                    # 限制检查：达到 max_pages_per_run 则停止
                    if fetched_count >= limits.max_pages_per_run:
                        logger.info(
                            "运行 %s 达到 maxPagesPerRun(%d)，停止抓取",
                            run_id,
                            limits.max_pages_per_run,
                        )
                        break

                    # 单页处理（封装异常，单页失败不影响整体运行）
                    try:
                        outcome = await self._process_single_url(
                            ctx=ctx,
                            source=source,
                            run_id=run_id,
                            url=url,
                            client=client,
                            db=db,
                            page_repo=page_repo,
                            material_repo=material_repo,
                            doc_repo=doc_repo,
                            kb_repo=kb_repo,
                            job_repo=job_repo,
                        )
                        # 根据处理结果累加计数
                        if outcome == "success":
                            success_count += 1
                        elif outcome == "duplicate":
                            duplicate_count += 1
                        elif outcome == "imported":
                            success_count += 1
                            imported_count += 1
                        elif outcome == "failed":
                            failed_count += 1
                    except Exception as exc:
                        # 单页异常：记录日志，累加失败计数，继续下一页
                        logger.warning(
                            "运行 %s 抓取 URL %s 失败：%s",
                            run_id,
                            url,
                            exc,
                            exc_info=True,
                        )
                        failed_count += 1

                    # 礼貌爬虫：同域名请求间隔
                    if limits.request_interval_ms > 0:
                        await asyncio.sleep(limits.request_interval_ms / 1000.0)

            # 状态：成功
            status = "success"
        except Exception as exc:
            # 整体异常：标记运行失败
            logger.exception("运行 %s 整体失败：%s", run_id, exc)
            status = "failed"
            error_code = "CRAWL_INTERNAL_ERROR"

        # ------------------------------------------------------------------
        # 步骤 5：更新 CrawlRun（status / 各 count / completed_at）
        # ------------------------------------------------------------------
        completed_at = datetime.now(timezone.utc)
        await run_repo.update(
            ctx,
            run_id,
            status=status,
            discovered_count=discovered_count,
            success_count=success_count,
            duplicate_count=duplicate_count,
            failed_count=failed_count,
            imported_count=imported_count,
            completed_at=completed_at,
            error_code=error_code,
        )
        await db.commit()

        return CrawlRunResult(
            run_id=run_id,
            source_id=crawl_source_id,
            status=status,
            discovered_count=discovered_count,
            success_count=success_count,
            duplicate_count=duplicate_count,
            failed_count=failed_count,
            imported_count=imported_count,
            error_code=error_code,
        )

    # ------------------------------------------------------------------
    # URL 发现：按 source.type 分发
    # ------------------------------------------------------------------
    async def _discover_urls(
        self,
        ctx: ProjectContext,
        source: CrawlSource,
        db: AsyncSession,
        limits: _CrawlLimits,
    ) -> list[str]:
        """按采集源类型发现待抓取的 URL 列表。

        分发逻辑：
            - SINGLE_PAGE：直接返回 start_urls（通常 1 个）
            - URL_LIST：直接返回 start_urls（逐个抓取）
            - RSS：抓取 RSS feed，解析条目 URL
            - SITEMAP：抓取 sitemap.xml，解析 URL 列表
            - LIST_PAGE：抓取列表页，按 extract_rules 提取详情页 URL

        Args:
            ctx: 项目上下文。
            source: 采集源 ORM 实例。
            db: 异步数据库会话。
            limits: 采集限制。

        Returns:
            待抓取的 URL 列表（已去重，已限制数量）。

        Raises:
            CrawlRuleInvalidError: start_urls 为空或配置无效。
        """
        # 校验 start_urls 非空
        if not source.start_urls:
            raise CrawlRuleInvalidError(
                f"采集源 {source.code} 的 startUrls 为空",
                details={"crawlSourceId": source.id, "field": "startUrls"},
            )

        source_type = source.type.lower()
        allowed_domains = source.allowed_domains or []

        if source_type in ("single_page", "url_list"):
            # SINGLE_PAGE / URL_LIST：直接抓取 start_urls
            return list(source.start_urls)

        if source_type == "rss":
            # RSS：解析 RSS feed，提取条目 URL
            return await self._discover_from_rss(
                source.start_urls, allowed_domains, limits
            )

        if source_type == "sitemap":
            # SITEMAP：解析 sitemap.xml，提取 URL 列表
            return await self._discover_from_sitemap(
                source.start_urls, allowed_domains, limits
            )

        if source_type == "list_page":
            # LIST_PAGE：抓取列表页，按 extract_rules 提取详情页 URL
            return await self._discover_from_list_page(
                source, allowed_domains, limits
            )

        # 未知类型：抛错
        raise CrawlRuleInvalidError(
            f"采集源 {source.code} 的 type {source.type} 不被支持",
            details={"crawlSourceId": source.id, "type": source.type},
        )

    async def _discover_from_rss(
        self,
        feed_urls: list[str],
        allowed_domains: list[str],
        limits: _CrawlLimits,
    ) -> list[str]:
        """从 RSS feed 解析条目 URL。

        流程：
            1. 对每个 feed URL 做 SSRF 校验并抓取
            2. 用 BeautifulSoup 解析 XML，提取所有 ``<link>`` 或 ``<guid>`` 文本
            3. 应用 max_pages_per_run 限制

        Args:
            feed_urls: RSS feed URL 列表。
            allowed_domains: 域名白名单。
            limits: 采集限制。

        Returns:
            发现的条目 URL 列表（已去重）。
        """
        discovered: list[str] = []
        seen: set[str] = set()

        async with httpx.AsyncClient(
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=False,
            headers=self._headers,
        ) as client:
            for feed_url in feed_urls:
                # SSRF 校验
                try:
                    validate_url(feed_url, allowed_domains=allowed_domains)
                except Exception as exc:
                    logger.warning("RSS feed URL %s SSRF 校验失败：%s", feed_url, exc)
                    continue

                # 抓取 feed
                fetched = await self._fetch_page(feed_url, client, allowed_domains)
                if fetched is None:
                    continue

                # 解析 XML 提取条目 URL
                try:
                    soup = BeautifulSoup(fetched.html, "xml")
                    # RSS 2.0：item > link；Atom：entry > link[href]
                    for item in soup.find_all("item"):
                        link_tag = item.find("link")
                        if link_tag and link_tag.get_text(strip=True):
                            url = link_tag.get_text(strip=True)
                            self._add_discovered_url(url, seen, discovered, limits)
                    for entry in soup.find_all("entry"):
                        link_tag = entry.find("link")
                        if link_tag and link_tag.get("href"):
                            url = link_tag["href"]
                            self._add_discovered_url(url, seen, discovered, limits)
                except Exception as exc:
                    logger.warning("RSS feed %s 解析失败：%s", feed_url, exc)

        return discovered

    async def _discover_from_sitemap(
        self,
        sitemap_urls: list[str],
        allowed_domains: list[str],
        limits: _CrawlLimits,
    ) -> list[str]:
        """从 sitemap.xml 解析 URL 列表。

        流程：
            1. 对每个 sitemap URL 做 SSRF 校验并抓取
            2. 用 BeautifulSoup 解析 XML，提取所有 ``<url>`` 下的 ``<loc>``
            3. 支持 sitemapindex（嵌套 sitemap），递归一层
            4. 应用 max_pages_per_run 限制

        Args:
            sitemap_urls: sitemap.xml URL 列表。
            allowed_domains: 域名白名单。
            limits: 采集限制。

        Returns:
            发现的 URL 列表（已去重）。
        """
        discovered: list[str] = []
        seen: set[str] = set()

        async with httpx.AsyncClient(
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=False,
            headers=self._headers,
        ) as client:
            for sitemap_url in sitemap_urls:
                # SSRF 校验
                try:
                    validate_url(sitemap_url, allowed_domains=allowed_domains)
                except Exception as exc:
                    logger.warning("Sitemap URL %s SSRF 校验失败：%s", sitemap_url, exc)
                    continue

                # 抓取 sitemap
                fetched = await self._fetch_page(sitemap_url, client, allowed_domains)
                if fetched is None:
                    continue

                # 解析 XML
                try:
                    soup = BeautifulSoup(fetched.html, "xml")
                    # 标准 sitemap：<url><loc>...</loc></url>
                    for url_tag in soup.find_all("url"):
                        loc = url_tag.find("loc")
                        if loc and loc.get_text(strip=True):
                            url = loc.get_text(strip=True)
                            self._add_discovered_url(url, seen, discovered, limits)
                    # sitemapindex：<sitemap><loc>...</loc></sitemap>
                    # 仅递归一层，避免无限嵌套
                    for sitemap_tag in soup.find_all("sitemap"):
                        loc = sitemap_tag.find("loc")
                        if loc and loc.get_text(strip=True):
                            sub_url = loc.get_text(strip=True)
                            sub_fetched = await self._fetch_page(
                                sub_url, client, allowed_domains
                            )
                            if sub_fetched is None:
                                continue
                            sub_soup = BeautifulSoup(sub_fetched.html, "xml")
                            for url_tag in sub_soup.find_all("url"):
                                loc = url_tag.find("loc")
                                if loc and loc.get_text(strip=True):
                                    url = loc.get_text(strip=True)
                                    self._add_discovered_url(url, seen, discovered, limits)
                except Exception as exc:
                    logger.warning("Sitemap %s 解析失败：%s", sitemap_url, exc)

        return discovered

    async def _discover_from_list_page(
        self,
        source: CrawlSource,
        allowed_domains: list[str],
        limits: _CrawlLimits,
    ) -> list[str]:
        """从列表页提取详情页 URL。

        流程：
            1. 对每个 start_url 做 SSRF 校验并抓取
            2. 用 BeautifulSoup 解析 HTML
            3. 按 extract_rules.detailSelector 提取详情页链接
            4. 将相对 URL 转为绝对 URL（urljoin）
            5. 应用 max_pages_per_run 限制

        Args:
            source: 采集源 ORM 实例（含 extract_rules）。
            allowed_domains: 域名白名单。
            limits: 采集限制。

        Returns:
            发现的详情页 URL 列表（已去重）。
        """
        discovered: list[str] = []
        seen: set[str] = set()

        # 从 extract_rules 读取详情页选择器
        extract_rules = source.extract_rules or {}
        detail_selector = extract_rules.get("detailSelector")
        if not detail_selector:
            # 未配置选择器：直接返回 start_urls 作为详情页
            return list(source.start_urls)

        async with httpx.AsyncClient(
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=False,
            headers=self._headers,
        ) as client:
            for list_url in source.start_urls:
                # SSRF 校验
                try:
                    validate_url(list_url, allowed_domains=allowed_domains)
                except Exception as exc:
                    logger.warning("列表页 URL %s SSRF 校验失败：%s", list_url, exc)
                    continue

                # 抓取列表页
                fetched = await self._fetch_page(list_url, client, allowed_domains)
                if fetched is None:
                    continue

                # 解析 HTML，按选择器提取详情页链接
                try:
                    soup = BeautifulSoup(fetched.html, "html.parser")
                    for elem in soup.select(detail_selector):
                        href = elem.get("href")
                        if not href:
                            continue
                        # 相对 URL 转绝对 URL
                        absolute_url = urljoin(fetched.url, href)
                        self._add_discovered_url(absolute_url, seen, discovered, limits)
                except Exception as exc:
                    logger.warning("列表页 %s 解析失败：%s", list_url, exc)

        return discovered

    def _add_discovered_url(
        self,
        url: str,
        seen: set[str],
        discovered: list[str],
        limits: _CrawlLimits,
    ) -> None:
        """将发现的 URL 加入列表（去重 + 数量限制）。

        使用规范化 URL 做去重，避免同一页面以不同 URL 形式重复加入。

        Args:
            url: 待加入的 URL。
            seen: 已加入的规范化 URL 集合（去重用）。
            discovered: 已加入的原始 URL 列表。
            limits: 采集限制（max_pages_per_run）。
        """
        # 规范化 URL 用于去重
        canonical = normalize_url(url)
        if not canonical or canonical in seen:
            return
        # 数量限制
        if len(discovered) >= limits.max_pages_per_run:
            return
        seen.add(canonical)
        discovered.append(url)

    # ------------------------------------------------------------------
    # 单页处理：SSRF 校验 → 下载 → 清洗 → 去重 → 入库
    # ------------------------------------------------------------------
    async def _process_single_url(
        self,
        *,
        ctx: ProjectContext,
        source: CrawlSource,
        run_id: str,
        url: str,
        client: httpx.AsyncClient,
        db: AsyncSession,
        page_repo: CrawlPageRepository,
        material_repo: WebMaterialRepository,
        doc_repo: DocumentRepository,
        kb_repo: KnowledgeBaseRepository,
        job_repo: IngestionJobRepository,
    ) -> str:
        """处理单个 URL：SSRF 校验 → 下载 → 清洗 → 去重 → 入库。

        Args:
            ctx: 项目上下文。
            source: 采集源 ORM 实例。
            run_id: 运行记录 ID。
            url: 待处理的 URL。
            client: httpx 异步客户端（共享连接池）。
            db: 异步数据库会话。
            page_repo: 页面 Repository。
            material_repo: 待审核资料 Repository。
            doc_repo: 文档 Repository。
            kb_repo: 知识库 Repository。
            job_repo: 入库任务 Repository。

        Returns:
            处理结果标识：
            - "success"：成功抓取（未入库，如 review_required）
            - "duplicate"：重复页面，跳过
            - "imported"：成功抓取并自动入库
            - "failed"：失败
        """
        allowed_domains = source.allowed_domains or []

        # ------------------------------------------------------------------
        # 步骤 a：SSRF 校验
        # ------------------------------------------------------------------
        try:
            validate_url(url, allowed_domains=allowed_domains)
        except Exception as exc:
            # SSRF 校验失败：记录失败页面，不抛异常（继续下一页）
            logger.warning("URL %s SSRF 校验失败：%s", url, exc)
            await self._record_failed_page(
                ctx=ctx,
                source=source,
                run_id=run_id,
                url=url,
                error_code="SSRF_REJECTED",
                page_repo=page_repo,
            )
            await db.commit()
            return "failed"

        # ------------------------------------------------------------------
        # 步骤 b：下载页面（httpx，限制响应体 ≤ 5MB，超时 10s，关闭自动重定向）
        # ------------------------------------------------------------------
        fetched = await self._fetch_page(url, client, allowed_domains)
        if fetched is None:
            # 下载失败：记录失败页面
            await self._record_failed_page(
                ctx=ctx,
                source=source,
                run_id=run_id,
                url=url,
                error_code="FETCH_FAILED",
                page_repo=page_repo,
            )
            await db.commit()
            return "failed"

        # ------------------------------------------------------------------
        # 步骤 c：重定向后重新校验（已在 _fetch_page 内逐跳校验，此处仅校验最终 URL）
        # ------------------------------------------------------------------
        try:
            validate_redirect(fetched.url, allowed_domains=allowed_domains)
        except Exception as exc:
            logger.warning("重定向后 URL %s 校验失败：%s", fetched.url, exc)
            await self._record_failed_page(
                ctx=ctx,
                source=source,
                run_id=run_id,
                url=url,
                error_code="REDIRECT_REJECTED",
                page_repo=page_repo,
            )
            await db.commit()
            return "failed"

        # ------------------------------------------------------------------
        # 步骤 d：URL 规范化 + 哈希（去重键）
        # ------------------------------------------------------------------
        canonical_url = normalize_url(fetched.url)
        canonical_url_hash = compute_url_hash(fetched.url)

        # ------------------------------------------------------------------
        # 步骤 e：去重查询
        # ------------------------------------------------------------------
        # 查询同源同 URL 哈希的已有页面
        existing_page = await page_repo.get_by_canonical_hash(
            ctx, source.id, canonical_url_hash
        )

        # ------------------------------------------------------------------
        # 步骤 f：HTML 清洗 + 正文提取
        # ------------------------------------------------------------------
        sanitized_html = sanitize_html(fetched.html)
        text_content = extract_text(fetched.html)
        title = extract_title(fetched.html) or fetched.url

        # ------------------------------------------------------------------
        # 步骤 g：计算 content_hash
        # ------------------------------------------------------------------
        content_hash = compute_content_hash(text_content)

        # ------------------------------------------------------------------
        # 步骤 e（续）：去重判定
        # ------------------------------------------------------------------
        if existing_page is not None and existing_page.content_hash == content_hash:
            # URL 已存在且 content_hash 未变：跳过（duplicate_count++）
            # 仅更新 fetched_at 为本次检查时间
            await page_repo.update(
                ctx,
                existing_page.id,
                fetched_at=datetime.now(timezone.utc),
            )
            await db.commit()
            return "duplicate"

        # ------------------------------------------------------------------
        # 步骤 h & i：创建/更新 CrawlPage 记录
        # ------------------------------------------------------------------
        if existing_page is None:
            # 新 URL：创建 CrawlPage
            page = await page_repo.create(
                ctx,
                crawl_source_id=source.id,
                crawl_run_id=run_id,
                url=url,
                canonical_url=canonical_url,
                canonical_url_hash=canonical_url_hash,
                title=title,
                content_hash=content_hash,
                fetched_at=datetime.now(timezone.utc),
                http_status=fetched.http_status,
                status="fetched",
                metadata_={"content_type": fetched.content_type},
            )
            if page is None:
                # 唯一约束冲突（极端竞态，另一 Worker 已创建）：视为重复
                await db.rollback()
                return "duplicate"
            page_id = page.id
        else:
            # URL 已存在但 content_hash 变化：增量更新，创建新版本
            # 更新现有 CrawlPage 记录为新版本（保留 document_id 指向新版本 Document）
            # 历史 Document 保留以支持回溯
            await page_repo.update(
                ctx,
                existing_page.id,
                title=title,
                content_hash=content_hash,
                fetched_at=datetime.now(timezone.utc),
                http_status=fetched.http_status,
                status="fetched",
                # document_id 置空，待新版本入库后回填
                document_id=None,
            )
            page_id = existing_page.id

        # ------------------------------------------------------------------
        # 步骤 j：按 import_policy 处理入库
        # ------------------------------------------------------------------
        policy = source.import_policy.lower()
        if policy == "review_required":
            # REVIEW_REQUIRED：创建 WebMaterial（status='pending'），等人工审核
            await material_repo.create(
                ctx,
                crawl_source_id=source.id,
                crawl_page_id=page_id,
                title=title,
                content=text_content,
                source_url=url,
                status="pending",
                knowledge_base_id=source.destination_knowledge_base_id,
            )
            # 页面状态更新为待审核
            await page_repo.update(ctx, page_id, status="review")
            await db.commit()
            return "success"

        if policy == "auto_import":
            # AUTO_IMPORT：创建 Document + IngestionJob，触发向量化
            document_id = await self._import_to_knowledge_base(
                ctx=ctx,
                source=source,
                title=title,
                content=text_content,
                url=url,
                content_hash=content_hash,
                doc_repo=doc_repo,
                kb_repo=kb_repo,
                job_repo=job_repo,
            )
            if document_id:
                # 回填 document_id 到 CrawlPage
                await page_repo.update(
                    ctx, page_id, status="imported", document_id=document_id
                )
                await db.commit()
                return "imported"
            # 入库失败：标记页面为 failed
            await page_repo.update(
                ctx, page_id, status="failed", error_code="IMPORT_FAILED"
            )
            await db.commit()
            return "failed"

        if policy == "evidence_only":
            # EVIDENCE_ONLY：仅入待审核池短期保存，标注 TODO 过期清理
            # TODO: 通过定时任务定期清理 evidence_only 策略下过期的 WebMaterial
            await material_repo.create(
                ctx,
                crawl_source_id=source.id,
                crawl_page_id=page_id,
                title=title,
                content=text_content,
                source_url=url,
                status="pending",
                # evidence_only 不入库知识库，knowledge_base_id 留空
                knowledge_base_id=None,
                metadata_={"policy": "evidence_only", "expire_hint": True},
            )
            await page_repo.update(ctx, page_id, status="review")
            await db.commit()
            return "success"

        # 未知策略：标记失败
        await page_repo.update(
            ctx, page_id, status="failed", error_code="UNKNOWN_POLICY"
        )
        await db.commit()
        return "failed"

    async def _fetch_page(
        self,
        url: str,
        client: httpx.AsyncClient,
        allowed_domains: list[str],
    ) -> _FetchedPage | None:
        """下载单个页面，手动处理重定向（每一跳 SSRF 校验）。

        流程：
            1. 发起 GET 请求（follow_redirects=False）
            2. 若 3xx 重定向：取 Location 头，校验后递归请求（最多 5 跳）
            3. 限制响应体 ≤ 5MB（stream 流式读取，超限中断）
            4. 非 2xx 状态码返回 None

        Args:
            url: 待抓取的 URL。
            client: httpx 异步客户端。
            allowed_domains: 域名白名单（重定向校验用）。

        Returns:
            _FetchedPage 实例；抓取失败返回 None。
        """
        max_redirects = 5  # 最大重定向跳数，防止无限重定向
        current_url = url

        for _ in range(max_redirects + 1):
            try:
                # stream 流式读取：便于在响应体超过 5MB 时提前中断
                async with client.stream("GET", current_url) as response:
                    # 3xx 重定向：取 Location 头，校验后递归
                    if 300 <= response.status_code < 400:
                        location = response.headers.get("location")
                        if not location:
                            # 无 Location 头：重定向异常，返回 None
                            return None
                        # 相对路径转绝对 URL
                        next_url = urljoin(current_url, location)
                        # 重定向后重新 SSRF 校验（关键：防 SSRF 绕过）
                        try:
                            validate_redirect(next_url, allowed_domains=allowed_domains)
                        except Exception as exc:
                            logger.warning("重定向 URL %s 校验失败：%s", next_url, exc)
                            return None
                        current_url = next_url
                        continue

                    # 非 2xx 状态码：返回 None
                    if response.status_code >= 400:
                        logger.warning(
                            "抓取 %s 返回 %d",
                            current_url,
                            response.status_code,
                        )
                        return None

                    # 2xx：流式读取响应体，限制 ≤ 5MB
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > _MAX_RESPONSE_BYTES:
                            # 超过 5MB：截断
                            logger.warning(
                                "页面 %s 响应体超过 5MB，截断",
                                current_url,
                            )
                            break
                        chunks.append(chunk)

                    raw = b"".join(chunks)
                    # 推断编码：优先响应头 charset，失败用 UTF-8 容错
                    encoding = response.encoding or "utf-8"
                    try:
                        html = raw.decode(encoding, errors="replace")
                    except (LookupError, TypeError):
                        html = raw.decode("utf-8", errors="replace")

                    return _FetchedPage(
                        url=current_url,
                        http_status=response.status_code,
                        html=html,
                        content_type=response.headers.get("content-type"),
                    )
            except httpx.TimeoutException:
                logger.warning("抓取 %s 超时", current_url)
                return None
            except httpx.HTTPError as exc:
                logger.warning("抓取 %s 网络错误：%s", current_url, exc)
                return None

        # 超过最大重定向次数
        logger.warning("抓取 %s 超过最大重定向次数 %d", url, max_redirects)
        return None

    async def _record_failed_page(
        self,
        *,
        ctx: ProjectContext,
        source: CrawlSource,
        run_id: str,
        url: str,
        error_code: str,
        page_repo: CrawlPageRepository,
    ) -> None:
        """记录失败页面到 CrawlPage（status='failed'）。

        即使抓取失败也记录页面，便于运维排查与重试。

        Args:
            ctx: 项目上下文。
            source: 采集源 ORM 实例。
            run_id: 运行记录 ID。
            url: 失败的 URL。
            error_code: 错误码。
            page_repo: 页面 Repository。
        """
        # 规范化 URL（即使抓取失败也记录规范化形式，便于去重查询）
        canonical_url = normalize_url(url)
        canonical_url_hash = compute_url_hash(url)

        # 尝试创建失败页面记录（若已存在相同 URL 哈希则跳过）
        await page_repo.create(
            ctx,
            crawl_source_id=source.id,
            crawl_run_id=run_id,
            url=url,
            canonical_url=canonical_url,
            canonical_url_hash=canonical_url_hash,
            status="failed",
            fetched_at=datetime.now(timezone.utc),
            error_code=error_code,
        )

    async def _import_to_knowledge_base(
        self,
        *,
        ctx: ProjectContext,
        source: CrawlSource,
        title: str,
        content: str,
        url: str,
        content_hash: str,
        doc_repo: DocumentRepository,
        kb_repo: KnowledgeBaseRepository,
        job_repo: IngestionJobRepository,
    ) -> str | None:
        """将抓取内容入库到知识库（创建 Document + IngestionJob）。

        流程：
            1. 校验 destination_knowledge_base_id 非空
            2. 校验知识库存在且属于当前项目
            3. 创建 Document（source_type='crawler'）
            4. 创建 IngestionJob（触发向量化入库流程）
            5. 返回 document_id

        Args:
            ctx: 项目上下文。
            source: 采集源 ORM 实例。
            title: 文档标题。
            content: 文档正文。
            url: 来源 URL。
            content_hash: 正文哈希。
            doc_repo: 文档 Repository。
            kb_repo: 知识库 Repository。
            job_repo: 入库任务 Repository。

        Returns:
            创建的 Document ID；失败返回 None。
        """
        # 校验目标知识库 ID 非空
        if not source.destination_knowledge_base_id:
            logger.warning(
                "采集源 %s 配置为 AUTO_IMPORT 但未设置 destinationKnowledgeBaseId",
                source.code,
            )
            return None

        # 校验知识库存在且属于当前项目
        kb = await kb_repo.get_by_id(ctx, source.destination_knowledge_base_id)
        if kb is None:
            logger.warning(
                "采集源 %s 的目标知识库 %s 不存在",
                source.code,
                source.destination_knowledge_base_id,
            )
            return None

        # 创建 Document（source_type='crawler'，标记为爬虫采集来源）
        doc = await doc_repo.create(
            ctx,
            knowledge_base_id=kb.id,
            source_type="crawler",
            title=title,
            source_url=url,
            content_hash=content_hash,
            processing_status="pending",
            # 元数据记录采集源信息，便于溯源
            metadata_={
                "crawl_source_id": source.id,
                "crawl_source_code": source.code,
            },
        )

        # 创建 IngestionJob（status='pending'，由 ingestion_tasks 处理向量化）
        await job_repo.create(ctx, doc.id)

        # 延迟导入避免循环依赖
        # 触发向量化入库任务（投递到 ingestion 队列）
        try:
            from app.workers.ingestion_tasks import process_document

            process_document.delay(ctx.project_id, doc.id, None)
        except Exception as exc:
            # 投递失败不阻塞采集流程，IngestionJob 保留 pending 状态，
            # 可由定时任务扫描重投
            logger.warning("触发入库任务失败（document_id=%s）：%s", doc.id, exc)

        return doc.id

    # ------------------------------------------------------------------
    # 辅助函数
    # ------------------------------------------------------------------
    def _parse_limits(self, limits: dict[str, Any] | None) -> _CrawlLimits:
        """解析采集限制配置，带默认值兜底。

        Args:
            limits: 原始 limits JSONB 字典，可空。

        Returns:
            _CrawlLimits 实例。
        """
        if not limits:
            return _CrawlLimits()

        # 逐字段解析，类型错误时降级为默认值
        try:
            return _CrawlLimits(
                max_pages_per_run=int(
                    limits.get("maxPagesPerRun", _DEFAULT_MAX_PAGES_PER_RUN)
                ),
                max_depth=int(limits.get("maxDepth", _DEFAULT_MAX_DEPTH)),
                request_interval_ms=int(
                    limits.get("requestIntervalMs", _DEFAULT_REQUEST_INTERVAL_MS)
                ),
                concurrency_per_domain=int(
                    limits.get("concurrencyPerDomain", _DEFAULT_CONCURRENCY_PER_DOMAIN)
                ),
            )
        except (TypeError, ValueError):
            # 配置类型错误：降级为默认值
            return _CrawlLimits()
