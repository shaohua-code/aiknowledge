"""联网搜索服务：编排 Provider 搜索、域名过滤、网页提取。

对应 SubTask 12.3：实现 ``WebResearchService``，作为研究链路中
"联网搜索"环节的统一入口，封装搜索 → 过滤 → 提取的完整流程。

核心职责
--------
1. **校验项目配置**：检查 ``ProjectSettings.web_search_enabled``，
   未启用时返回降级结果（``degraded_reason='web_search_disabled'``）。
2. **调用 Provider 搜索**：通过工厂获取 Provider，5 秒超时，
   超时或失败返回降级结果，不中断研究链路。
3. **域名过滤**：合并项目级 ``SourcePolicy``（allow/block 列表）与
   ``ProjectSettings.allowed_domains/blocked_domains``，
   按"先 block 再 allow"规则过滤搜索结果。
4. **并行网页提取**：对过滤后的 URL 调用 ``WebPageExtractor`` 提取正文，
   每批最多 3 个并行，整体超时 5s，避免单页慢拖垮整批。
5. **降级容错**：任何步骤失败都不抛异常，记录降级原因，返回部分结果。

为什么默认仅作临时证据？
------------------------
联网搜索结果的可信度低于内部知识库（内容未审核、来源不可控），
研究链路将联网证据标记为 ``score=0.5``（默认可信度），
模型生成时需结合内部证据交叉验证，避免单一联网来源误导结论。
联网证据不写入知识库，仅用于本次研究，下次研究需重新搜索。

降级策略
--------
研究链路遵循"宁可降级不可中断"原则，本服务在以下场景返回降级结果：
- 项目未启用联网搜索（``web_search_disabled``）
- Provider 未配置（``provider_not_configured``）
- 搜索超时（``search_timeout``）
- 搜索失败（``search_failed``）
- 网页提取整体超时（``extraction_timeout``）
降级结果 ``degraded=True``，``degraded_reasons`` 列举所有降级原因，
调用方据此决定是否仅基于内部知识库完成研究。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from app.core.config import settings
from app.core.exceptions import (
    ExternalSourceFailedError,
    ExternalSourceTimeoutError,
)
from app.db.repositories.crawler import SourcePolicyRepository
from app.db.repositories.project import ProjectSettingsRepository
from app.modules.web_research.domain_filter import is_domain_allowed
from app.modules.web_research.extractor import WebPageContent, WebPageExtractor
from app.providers.web_search import (
    WebSearchProvider,
    WebSearchResult,
    get_web_search_provider,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.project_context import ProjectContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class WebEvidence:
    """联网证据：单条搜索结果 + 可选正文。

    由 ``WebResearchService`` 构造，作为研究链路的临时证据使用。
    与内部知识库证据的区别：
    - ``score`` 默认 0.5（内部证据通常 0.7~0.9），需模型交叉验证后采信。
    - ``content`` 可为 None（仅搜索摘要可用时），研究链路据此降级使用 snippet。

    Attributes:
        title: 证据标题（搜索结果标题或网页标题）。
        url: 来源 URL，用于溯源与去重。
        snippet: 摘要（搜索摘要或网页正文前 300 字符）。
        content: 正文，未抓取或抓取失败时为 None。
        published_at: 发布时间，可空。
        score: 默认可信度 0.5，研究链路可按来源域名调整
            （如权威站点 .gov.cn 上调至 0.8，内容农场下调至 0.2）。
    """

    title: str
    url: str
    snippet: str
    content: str | None
    published_at: datetime | None
    score: float = 0.5


@dataclass
class WebResearchResult:
    """联网研究结果。

    由 ``WebResearchService.search_and_extract`` 返回，
    承载过滤与提取后的证据列表，以及降级状态。

    Attributes:
        results: 证据列表，已过滤与提取，长度 ≤ ``max_results``。
        degraded: 是否降级。True 表示搜索或提取环节出现部分失败，
            研究链路需结合 ``degraded_reasons`` 判断可信度。
        degraded_reasons: 降级原因列表，如 ``["search_timeout"]``。
            多个原因共存时按出现顺序排列。
    """

    results: list[WebEvidence]
    degraded: bool
    degraded_reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# WebResearchService
# ---------------------------------------------------------------------------
class WebResearchService:
    """联网搜索服务：编排 Provider 搜索、域名过滤、网页提取。

    使用方式
    --------
    .. code-block:: python

        service = WebResearchService(session=db_session)
        result = await service.search_and_extract(
            ctx=project_context,
            query="2024 年基金市场回顾",
            max_results=5,
            fetch_content=True,
        )
        if result.degraded:
            logger.warning("联网搜索降级：%s", result.degraded_reasons)
        for evidence in result.results:
            # 使用 evidence.content 或 evidence.snippet 作为模型上下文
            ...

    设计要点
    --------
    1. **不抛异常**：所有外部调用（Provider 搜索、网页提取）失败均捕获，
       返回降级结果，保证研究链路不因联网环节中断。
    2. **5 秒整体超时**：搜索 5s + 提取 5s，共 10s，
       留足 5s 给模型生成（研究整体硬超时 15s）。
    3. **并行提取限流**：每批最多 3 个 URL 并行提取，
       避免对目标站点造成并发压力，也便于控制内存占用。
    4. **域名过滤合并**：项目级 SourcePolicy 与 ProjectSettings 的
       allowed/blocked_domains 合并，确保两种配置都生效。
    """

    # 网页提取批次大小：每批最多 3 个 URL 并行，避免并发压力过大
    EXTRACTION_BATCH_SIZE = 3
    # 网页提取整体超时：5s，与研究链路预算匹配
    EXTRACTION_OVERALL_TIMEOUT = 5

    def __init__(self, session: "AsyncSession") -> None:
        """初始化联网搜索服务。

        Args:
            session: 异步数据库会话，用于查询项目设置与来源策略。
        """
        self.session = session
        # 网页提取器：单例复用，内部 httpx 客户端按请求创建
        self._extractor = WebPageExtractor(timeout=settings.web_search_timeout_seconds)

    async def search_and_extract(
        self,
        ctx: "ProjectContext",
        query: str,
        max_results: int = 5,
        fetch_content: bool = True,
    ) -> WebResearchResult:
        """执行联网搜索与网页提取。

        完整流程：
            1. 校验项目是否启用联网搜索（ProjectSettings.web_search_enabled）
            2. 获取 Provider，未配置或未启用 → 返回空结果 + 降级原因
            3. 调用 Provider 搜索（5 秒超时，超时返回降级结果）
            4. 加载项目 SourcePolicy + ProjectSettings.allowed/blocked_domains
            5. 过滤结果：URL 域名在 block 列表 → 丢弃；
               allow 列表非空时仅保留 allow 列表域名
            6. 若 fetch_content=True，对每个 URL 调用 WebPageExtractor.extract()
               提取正文（并行，每批最多 3 个，整体超时 5s）
            7. 返回 WebResearchResult

        Args:
            ctx: 项目上下文，提供 project_id 用于查询项目设置与来源策略。
            query: 查询字符串，由研究链路根据用户问题构造。
            max_results: 最大结果数，默认 5，对应 PRD "最多 5 条结果"。
            fetch_content: 是否抓取网页正文。False 时仅返回搜索摘要，
                适用于快速预览或对延迟敏感的场景。

        Returns:
            ``WebResearchResult`` 实例。``degraded=True`` 表示出现降级，
            研究链路需结合 ``degraded_reasons`` 判断可信度。
        """
        degraded_reasons: list[str] = []

        # 步骤 1：校验项目是否启用联网搜索
        # 从 ProjectSettings 读取 web_search_enabled，未配置视为关闭
        settings_repo = ProjectSettingsRepository(self.session)
        project_settings = await settings_repo.get_by_project(ctx)
        web_search_enabled = (
            project_settings.web_search_enabled if project_settings else False
        )
        if not web_search_enabled:
            # 未启用：返回空结果 + 降级原因，不抛异常
            # 研究链路据此降级为 knowledge_only 策略
            return WebResearchResult(
                results=[],
                degraded=True,
                degraded_reasons=["web_search_disabled"],
            )

        # 步骤 2：获取 Provider
        # 工厂函数返回 None 表示未配置 Provider
        provider = get_web_search_provider()
        if provider is None:
            # Provider 未配置：返回空结果 + 降级原因
            return WebResearchResult(
                results=[],
                degraded=True,
                degraded_reasons=["provider_not_configured"],
            )

        # 步骤 3：调用 Provider 搜索
        # 5 秒超时由 Provider 内部控制，超时抛 ExternalSourceTimeoutError
        search_results = await self._search_with_degradation(
            provider, query, max_results, degraded_reasons
        )
        if not search_results:
            # 搜索失败或无结果：返回空结果（降级原因已记录）
            return WebResearchResult(
                results=[],
                degraded=bool(degraded_reasons),
                degraded_reasons=degraded_reasons,
            )

        # 步骤 4 & 5：加载域名策略并过滤搜索结果
        # 合并 SourcePolicy 与 ProjectSettings 的 allowed/blocked_domains
        allowed_domains, blocked_domains = await self._load_domain_policies(
            ctx, project_settings
        )
        filtered_results = self._filter_results(
            search_results, allowed_domains, blocked_domains
        )

        # 过滤后无结果：返回空（不算降级，仅是无符合条件的结果）
        if not filtered_results:
            return WebResearchResult(
                results=[],
                degraded=bool(degraded_reasons),
                degraded_reasons=degraded_reasons,
            )

        # 步骤 6：并行网页提取（可选）
        if fetch_content:
            extracted_map = await self._extract_in_batches(
                filtered_results, degraded_reasons
            )
        else:
            # 不提取正文：所有结果 content=None
            extracted_map = {}

        # 步骤 7：构造 WebEvidence 列表
        evidences = self._build_evidences(
            filtered_results, extracted_map, fetch_content
        )

        return WebResearchResult(
            results=evidences,
            degraded=bool(degraded_reasons),
            degraded_reasons=degraded_reasons,
        )

    async def _search_with_degradation(
        self,
        provider: WebSearchProvider,
        query: str,
        max_results: int,
        degraded_reasons: list[str],
    ) -> list[WebSearchResult]:
        """调用 Provider 搜索，捕获异常并记录降级原因。

        Provider 超时或失败时不抛异常，返回空列表，
        降级原因追加到 ``degraded_reasons``。

        Args:
            provider: Web 搜索 Provider 实例。
            query: 查询字符串。
            max_results: 最大结果数。
            degraded_reasons: 降级原因列表（可变，追加新原因）。

        Returns:
            搜索结果列表；失败时返回空列表。
        """
        try:
            return await provider.search(query, max_results=max_results)
        except ExternalSourceTimeoutError as exc:
            # 搜索超时：记录降级原因，返回空列表
            logger.warning("联网搜索超时（query=%s）：%s", query, exc)
            degraded_reasons.append("search_timeout")
            return []
        except ExternalSourceFailedError as exc:
            # 搜索失败：记录降级原因，返回空列表
            logger.warning("联网搜索失败（query=%s）：%s", query, exc)
            degraded_reasons.append("search_failed")
            return []
        except Exception as exc:
            # 未预期异常：兜底降级，避免中断研究链路
            logger.warning(
                "联网搜索未预期异常（query=%s）：%s",
                query,
                exc,
                exc_info=True,
            )
            degraded_reasons.append("search_unexpected_error")
            return []

    async def _load_domain_policies(
        self,
        ctx: "ProjectContext",
        project_settings,
    ) -> tuple[list[str], list[str]]:
        """加载项目域名策略：合并 SourcePolicy 与 ProjectSettings。

        数据来源
        --------
        1. ``SourcePolicy`` 表（``policy_type='allow'`` / ``'block'``）：
           项目级来源策略，供采集与联网搜索共用。
        2. ``ProjectSettings.allowed_domains`` / ``blocked_domains``：
           项目设置的快捷配置，与 SourcePolicy 等效但更轻量。

        合并策略
        --------
        - allow 列表 = SourcePolicy(allow) 的 domain + ProjectSettings.allowed_domains
        - block 列表 = SourcePolicy(block) 的 domain + ProjectSettings.blocked_domains
        - 去重：避免重复条目影响匹配性能

        Args:
            ctx: 项目上下文。
            project_settings: 项目设置 ORM 实例，可空（未配置时）。

        Returns:
            元组 ``(allowed_domains, blocked_domains)``，均为去重后的域名列表。
        """
        # 从 SourcePolicy 表加载
        policy_repo = SourcePolicyRepository(self.session)
        allow_policies = await policy_repo.list(ctx, policy_type="allow")
        block_policies = await policy_repo.list(ctx, policy_type="block")

        allowed: set[str] = {p.domain for p in allow_policies if p.domain}
        blocked: set[str] = {p.domain for p in block_policies if p.domain}

        # 合并 ProjectSettings 的快捷配置
        if project_settings:
            if project_settings.allowed_domains:
                allowed.update(project_settings.allowed_domains)
            if project_settings.blocked_domains:
                blocked.update(project_settings.blocked_domains)

        return list(allowed), list(blocked)

    @staticmethod
    def _filter_results(
        results: list[WebSearchResult],
        allowed_domains: list[str],
        blocked_domains: list[str],
    ) -> list[WebSearchResult]:
        """按域名过滤搜索结果。

        过滤规则（与 ``is_domain_allowed`` 一致）：
            1. 先检查黑名单：命中即丢弃
            2. 再检查白名单：非空时仅保留列表内域名

        Args:
            results: 待过滤的搜索结果列表。
            allowed_domains: 允许域名白名单，空列表表示不限制。
            blocked_domains: 禁用域名黑名单。

        Returns:
            过滤后的结果列表，顺序与输入一致。
        """
        filtered: list[WebSearchResult] = []
        for result in results:
            # 域名过滤：先 block 再 allow
            if is_domain_allowed(result.url, allowed_domains, blocked_domains):
                filtered.append(result)
        return filtered

    async def _extract_in_batches(
        self,
        results: list[WebSearchResult],
        degraded_reasons: list[str],
    ) -> dict[str, WebPageContent]:
        """分批并行提取网页正文，整体超时控制。

        并行策略
        --------
        - 每批最多 ``EXTRACTION_BATCH_SIZE``（3）个 URL 并行提取
        - 使用 ``asyncio.gather`` 并发执行
        - 整体超时 ``EXTRACTION_OVERALL_TIMEOUT``（5s），
          超时则中断未完成的批次，使用已完成的成果

        为什么每批 3 个？
        ---------------
        1. 避免对目标站点造成并发压力（如同时抓取 5 个页面可能触发反爬）
        2. 控制内存占用（每页可能高达 5MB）
        3. 单批 3 个并行已能显著缩短总时长（5 个 URL 2 批 ≈ 10s → 串行 25s）

        Args:
            results: 已过滤的搜索结果列表。
            degraded_reasons: 降级原因列表（可变，追加新原因）。

        Returns:
            ``{url: WebPageContent}`` 字典。提取失败的 URL 不在字典中，
            调用方据此判断是否使用搜索摘要降级。
        """
        extracted: dict[str, WebPageContent] = {}
        urls = [r.url for r in results]

        # 分批：每批 EXTRACTION_BATCH_SIZE 个 URL
        batches = [
            urls[i : i + self.EXTRACTION_BATCH_SIZE]
            for i in range(0, len(urls), self.EXTRACTION_BATCH_SIZE)
        ]

        try:
            # 整体超时控制：5s 内完成所有批次
            # 超时则中断，使用已完成的成果
            await asyncio.wait_for(
                self._extract_all_batches(batches, extracted),
                timeout=self.EXTRACTION_OVERALL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            # 整体超时：记录降级原因，使用已完成的成果
            logger.warning(
                "网页提取整体超时（%ds），使用已完成的部分结果",
                self.EXTRACTION_OVERALL_TIMEOUT,
            )
            degraded_reasons.append("extraction_timeout")

        return extracted

    async def _extract_all_batches(
        self,
        batches: list[list[str]],
        extracted: dict[str, WebPageContent],
    ) -> None:
        """顺序执行所有批次的并行提取。

        批次间顺序执行（批次内并行），避免所有 URL 同时抓取造成压力。
        已完成的成果实时写入 ``extracted`` 字典，便于超时时保留部分结果。

        Args:
            batches: 批次列表，每批一组 URL。
            extracted: 提取成果字典（可变，实时写入）。
        """
        for batch in batches:
            # 单批内并行：asyncio.gather 同时提取多个 URL
            tasks = [self._extractor.extract(url) for url in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for url, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    # 单页提取失败：跳过此 URL，不影响其他页
                    # 研究链路将仅使用搜索摘要作为证据
                    logger.warning("网页提取失败（%s）：%s", url, result)
                    continue
                extracted[url] = result

    @staticmethod
    def _build_evidences(
        results: list[WebSearchResult],
        extracted_map: dict[str, WebPageContent],
        fetch_content: bool,
    ) -> list[WebEvidence]:
        """构造 WebEvidence 列表。

        优先使用网页提取结果（标题、正文、摘要），失败时降级为搜索结果（标题、摘要）。

        Args:
            results: 已过滤的搜索结果列表。
            extracted_map: 网页提取成果字典，key 为 URL。
            fetch_content: 是否抓取了网页正文。False 时所有 content=None。

        Returns:
            ``WebEvidence`` 列表，顺序与 ``results`` 一致。
        """
        evidences: list[WebEvidence] = []
        for result in results:
            page = extracted_map.get(result.url)

            if page:
                # 有网页提取结果：优先使用页面标题与正文
                evidences.append(
                    WebEvidence(
                        title=page.title or result.title,
                        url=result.url,
                        snippet=page.snippet or result.snippet,
                        content=page.content,
                        published_at=page.published_at or result.published_at,
                        score=0.5,  # 默认可信度，研究链路可按来源调整
                    )
                )
            else:
                # 无网页提取结果：仅使用搜索摘要
                # content 为 None，研究链路据此降级使用 snippet
                evidences.append(
                    WebEvidence(
                        title=result.title,
                        url=result.url,
                        snippet=result.snippet,
                        content=None if fetch_content else None,
                        published_at=result.published_at,
                        score=0.5,
                    )
                )

        return evidences
