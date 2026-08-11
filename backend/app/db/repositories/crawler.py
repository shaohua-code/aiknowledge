"""爬虫 Repository：CrawlSource / CrawlRun / CrawlPage / WebMaterial / SourcePolicy。

对应 SubTask 6.1：网络采集源、运行记录、页面、待审核资料池、来源策略。

设计要点
--------
1. ``CrawlSource.code`` 在项目内唯一（复合唯一索引），所有查询带 project_id 过滤。
2. ``CrawlPage`` 通过 ``canonical_url_hash`` 实现 URL 去重，
   复合唯一索引 ``(project_id, crawl_source_id, canonical_url_hash)`` 保证幂等。
3. ``CrawlRun.increment_counts`` 使用 ``UPDATE SET col = col + ?`` 原子自增，
   避免并发场景下"先查后改"的计数丢失。
4. ``SourcePolicy`` 供采集与联网搜索共用，实现域名级白名单/黑名单。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.db.models.crawler import (
    CrawlPage,
    CrawlRun,
    CrawlSource,
    SourcePolicy,
    WebMaterial,
)
from app.db.repositories.base import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.project_context import ProjectContext


# ---------------------------------------------------------------------------
# CrawlSourceRepository
# ---------------------------------------------------------------------------
class CrawlSourceRepository(BaseRepository):
    """采集源 Repository：操作 ``crawl_sources`` 表。

    ``code`` 在项目内唯一。强制带 project_id 过滤。
    """

    model = CrawlSource

    async def create(self, ctx: "ProjectContext", **fields: Any) -> CrawlSource:
        """创建采集源。

        Args:
            ctx: 项目上下文。
            **fields: 采集源字段，必须包含 ``code`` / ``name`` / ``type``。

        Returns:
            创建后的 CrawlSource 实例。
        """
        # project_id 由 ctx 注入
        fields["project_id"] = ctx.project_id
        source = CrawlSource(**fields)
        self.session.add(source)
        await self.session.flush()
        return source

    async def get_by_id(
        self, ctx: "ProjectContext", id: str
    ) -> CrawlSource | None:
        """按 ID 查询采集源（强制 project_id 过滤）。

        Args:
            ctx: 项目上下文。
            id: 采集源主键。

        Returns:
            CrawlSource 实例；不存在或属于其他项目返回 None。
        """
        # 双重过滤：id + project_id
        stmt = select(CrawlSource).where(
            CrawlSource.id == id,
            CrawlSource.project_id == ctx.project_id,
        )
        return await self.session.scalar(stmt)

    async def get_by_code(
        self, ctx: "ProjectContext", code: str
    ) -> CrawlSource | None:
        """按 code 查询采集源（项目内唯一）。

        为什么必须带 project_id？
            ``code`` 仅在项目内唯一（复合唯一索引 ``uq_crawl_sources_project_code``），
            不同项目可有相同 code 的采集源。必须同时按 project_id 过滤。

        Args:
            ctx: 项目上下文。
            code: 采集源编码。

        Returns:
            CrawlSource 实例；不存在或属于其他项目返回 None。
        """
        # 双重过滤：project_id + code
        stmt = select(CrawlSource).where(
            CrawlSource.project_id == ctx.project_id,
            CrawlSource.code == code,
        )
        return await self.session.scalar(stmt)

    async def list(self, ctx: "ProjectContext") -> list[CrawlSource]:
        """列出当前项目的所有采集源。

        Args:
            ctx: 项目上下文。

        Returns:
            CrawlSource 列表，按创建时间倒序。
        """
        # 强制 project_id 过滤
        stmt = (
            select(CrawlSource)
            .where(CrawlSource.project_id == ctx.project_id)
            .order_by(CrawlSource.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def update(
        self,
        ctx: "ProjectContext",
        id: str,
        **fields: Any,
    ) -> CrawlSource | None:
        """按 ID 更新采集源字段（强制 project_id 过滤）。

        Args:
            ctx: 项目上下文。
            id: 采集源主键。
            **fields: 待更新字段。

        Returns:
            更新后的 CrawlSource 实例；不存在或属于其他项目返回 None。
        """
        # 复用基类 update
        return await super().update(ctx, id, **fields)

    async def set_status(
        self,
        ctx: "ProjectContext",
        id: str,
        status: str,
    ) -> CrawlSource | None:
        """更新采集源状态（active / disabled）。

        Args:
            ctx: 项目上下文。
            id: 采集源主键。
            status: 目标状态。

        Returns:
            更新后的 CrawlSource 实例；不存在返回 None。
        """
        # 复用 update，仅修改 status
        return await self.update(ctx, id, status=status)


# ---------------------------------------------------------------------------
# CrawlRunRepository
# ---------------------------------------------------------------------------
class CrawlRunRepository(BaseRepository):
    """采集运行 Repository：操作 ``crawl_runs`` 表。

    强制带 project_id 过滤。``increment_counts`` 使用原子自增避免并发计数丢失。
    """

    model = CrawlRun

    async def create(
        self,
        ctx: "ProjectContext",
        crawl_source_id: str,
    ) -> CrawlRun:
        """创建采集运行记录，初始状态为 pending。

        Args:
            ctx: 项目上下文。
            crawl_source_id: 关联采集源 ID（必须属于同一项目）。

        Returns:
            创建后的 CrawlRun 实例。
        """
        # project_id 由 ctx 注入
        run = CrawlRun(
            project_id=ctx.project_id,
            crawl_source_id=crawl_source_id,
            status="pending",  # 初始状态：待执行
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def get_by_id(self, ctx: "ProjectContext", id: str) -> CrawlRun | None:
        """按 ID 查询运行记录（强制 project_id 过滤）。

        Args:
            ctx: 项目上下文。
            id: 运行记录主键。

        Returns:
            CrawlRun 实例；不存在或属于其他项目返回 None。
        """
        # 双重过滤：id + project_id
        stmt = select(CrawlRun).where(
            CrawlRun.id == id,
            CrawlRun.project_id == ctx.project_id,
        )
        return await self.session.scalar(stmt)

    async def list_by_source(
        self,
        ctx: "ProjectContext",
        source_id: str,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[CrawlRun], int]:
        """分页列出指定采集源的运行记录。

        Args:
            ctx: 项目上下文。
            source_id: 采集源 ID。
            offset: 分页偏移。
            limit: 每页条数。

        Returns:
            元组 ``(items, total)``。
        """
        # 双重过滤：project_id + crawl_source_id
        conditions = [
            CrawlRun.project_id == ctx.project_id,
            CrawlRun.crawl_source_id == source_id,
        ]

        # 数据查询：按 created_at 倒序
        stmt_data = (
            select(CrawlRun)
            .where(*conditions)
            .order_by(CrawlRun.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        items = list((await self.session.execute(stmt_data)).scalars().all())

        # 计数查询
        stmt_count = select(func.count()).select_from(CrawlRun).where(*conditions)
        total = await self.session.scalar(stmt_count) or 0

        return items, total

    async def update(
        self,
        ctx: "ProjectContext",
        id: str,
        **fields: Any,
    ) -> CrawlRun | None:
        """按 ID 更新运行记录字段（强制 project_id 过滤）。

        Args:
            ctx: 项目上下文。
            id: 运行记录主键。
            **fields: 待更新字段。

        Returns:
            更新后的 CrawlRun 实例；不存在或属于其他项目返回 None。
        """
        # 复用基类 update
        return await super().update(ctx, id, **fields)

    async def increment_counts(
        self,
        ctx: "ProjectContext",
        id: str,
        **counters: int,
    ) -> None:
        """原子自增计数器（避免并发计数丢失）。

        使用 ``UPDATE SET col = col + ?`` 实现原子自增，
        避免并发场景下"先查后改"导致的计数丢失。

        支持的计数器：
            - discovered_count: 发现页面数
            - success_count: 成功抓取数
            - duplicate_count: 重复页面数
            - failed_count: 失败页面数
            - imported_count: 入库数

        Args:
            ctx: 项目上下文。
            id: 运行记录主键。
            **counters: 待自增的计数器，如 ``success_count=1, duplicate_count=2``。
        """
        if not counters:
            # 空计数器：直接返回
            return

        # 构造自增值字典：col = col + increment
        # 使用 ORM 字段 + value 构造原子自增表达式
        from sqlalchemy import literal

        values = {}
        for field, increment in counters.items():
            # 获取模型字段对象，构造 "col = col + ?" 表达式
            col = getattr(CrawlRun, field)
            values[field] = col + literal(increment)

        # UPDATE crawl_runs SET col = col + ? WHERE id = ? AND project_id = ?
        stmt = (
            update(CrawlRun)
            .where(
                CrawlRun.id == id,
                CrawlRun.project_id == ctx.project_id,
            )
            .values(**values)
        )
        await self.session.execute(stmt)


# ---------------------------------------------------------------------------
# CrawlPageRepository
# ---------------------------------------------------------------------------
class CrawlPageRepository(BaseRepository):
    """采集页面 Repository：操作 ``crawl_pages`` 表。

    ``canonical_url_hash`` 用于 URL 去重，复合唯一索引保证幂等。
    强制带 project_id 过滤。
    """

    model = CrawlPage

    async def create(self, ctx: "ProjectContext", **fields: Any) -> CrawlPage | None:
        """创建页面记录，处理唯一约束冲突。

        复合唯一索引 ``(project_id, crawl_source_id, canonical_url_hash)`` 保证
        同一采集源内 URL 去重。若已存在相同记录，触发 ``IntegrityError``，
        本方法捕获后返回 None 表示"已存在"。

        Args:
            ctx: 项目上下文。
            **fields: 页面字段，必须包含 ``crawl_source_id`` / ``crawl_run_id`` /
                ``url`` / ``canonical_url`` / ``canonical_url_hash``。

        Returns:
            创建后的 CrawlPage 实例；若已存在（冲突）返回 None。
        """
        # project_id 由 ctx 注入
        fields["project_id"] = ctx.project_id
        page = CrawlPage(**fields)
        self.session.add(page)
        try:
            await self.session.flush()
            return page
        except IntegrityError:
            # 唯一约束冲突：同一采集源内 URL 已存在
            await self.session.rollback()
            return None

    async def get_by_id(
        self, ctx: "ProjectContext", id: str
    ) -> CrawlPage | None:
        """按 ID 查询页面记录（强制 project_id 过滤）。

        Args:
            ctx: 项目上下文。
            id: 页面主键。

        Returns:
            CrawlPage 实例；不存在或属于其他项目返回 None。
        """
        # 双重过滤：id + project_id
        stmt = select(CrawlPage).where(
            CrawlPage.id == id,
            CrawlPage.project_id == ctx.project_id,
        )
        return await self.session.scalar(stmt)

    async def get_by_canonical_hash(
        self,
        ctx: "ProjectContext",
        source_id: str,
        canonical_url_hash: str,
    ) -> CrawlPage | None:
        """按规范化 URL 哈希查询页面（去重查询）。

        在抓取前查询是否已抓取过此 URL，避免重复抓取与入库。
        三重过滤保证查询结果仅属于当前项目的指定采集源。

        Args:
            ctx: 项目上下文。
            source_id: 采集源 ID。
            canonical_url_hash: 规范化 URL 哈希（SHA-256）。

        Returns:
            CrawlPage 实例；不存在返回 None。
        """
        # 三重过滤：project_id + crawl_source_id + canonical_url_hash
        stmt = select(CrawlPage).where(
            CrawlPage.project_id == ctx.project_id,
            CrawlPage.crawl_source_id == source_id,
            CrawlPage.canonical_url_hash == canonical_url_hash,
        )
        return await self.session.scalar(stmt)

    async def list_by_run(
        self,
        ctx: "ProjectContext",
        run_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[CrawlPage], int]:
        """分页列出指定运行的页面记录。

        Args:
            ctx: 项目上下文。
            run_id: 采集运行 ID。
            offset: 分页偏移。
            limit: 每页条数，默认 50。

        Returns:
            元组 ``(items, total)``。
        """
        # 双重过滤：project_id + crawl_run_id
        conditions = [
            CrawlPage.project_id == ctx.project_id,
            CrawlPage.crawl_run_id == run_id,
        ]

        # 数据查询：按 created_at 倒序
        stmt_data = (
            select(CrawlPage)
            .where(*conditions)
            .order_by(CrawlPage.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        items = list((await self.session.execute(stmt_data)).scalars().all())

        # 计数查询
        stmt_count = select(func.count()).select_from(CrawlPage).where(*conditions)
        total = await self.session.scalar(stmt_count) or 0

        return items, total

    async def update(
        self,
        ctx: "ProjectContext",
        id: str,
        **fields: Any,
    ) -> CrawlPage | None:
        """按 ID 更新页面记录字段（强制 project_id 过滤）。

        Args:
            ctx: 项目上下文。
            id: 页面主键。
            **fields: 待更新字段。

        Returns:
            更新后的 CrawlPage 实例；不存在或属于其他项目返回 None。
        """
        # 复用基类 update
        return await super().update(ctx, id, **fields)

    async def set_status(
        self,
        ctx: "ProjectContext",
        id: str,
        status: str,
        **extra: Any,
    ) -> CrawlPage | None:
        """更新页面状态及附加字段。

        页面状态：discovered=已发现 / fetched=已抓取 / imported=已入库 /
        review=待审核 / failed=失败 / source_unavailable=源不可用。

        Args:
            ctx: 项目上下文。
            id: 页面主键。
            status: 目标状态。
            **extra: 附加字段，如 ``http_status=200``、``title="xxx"``、
                ``content_hash="xxx"``、``document_id="xxx"``。

        Returns:
            更新后的 CrawlPage 实例；不存在返回 None。
        """
        # 合并 status 与附加字段
        fields = {"status": status, **extra}
        return await self.update(ctx, id, **fields)


# ---------------------------------------------------------------------------
# WebMaterialRepository
# ---------------------------------------------------------------------------
class WebMaterialRepository(BaseRepository):
    """网络待审核资料 Repository：操作 ``web_materials`` 表。

    ``review_required`` 策略下采集结果先入此表，人工审核通过后才入库到知识库。
    强制带 project_id 过滤。
    """

    model = WebMaterial

    async def create(self, ctx: "ProjectContext", **fields: Any) -> WebMaterial:
        """创建待审核资料。

        Args:
            ctx: 项目上下文。
            **fields: 资料字段，必须包含 ``title`` / ``content`` / ``source_url``。

        Returns:
            创建后的 WebMaterial 实例。
        """
        # project_id 由 ctx 注入
        fields["project_id"] = ctx.project_id
        material = WebMaterial(**fields)
        self.session.add(material)
        await self.session.flush()
        return material

    async def get_by_id(
        self, ctx: "ProjectContext", id: str
    ) -> WebMaterial | None:
        """按 ID 查询资料（强制 project_id 过滤）。

        Args:
            ctx: 项目上下文。
            id: 资料主键。

        Returns:
            WebMaterial 实例；不存在或属于其他项目返回 None。
        """
        # 双重过滤：id + project_id
        stmt = select(WebMaterial).where(
            WebMaterial.id == id,
            WebMaterial.project_id == ctx.project_id,
        )
        return await self.session.scalar(stmt)

    async def list(
        self,
        ctx: "ProjectContext",
        status_filter: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[WebMaterial], int]:
        """分页列出当前项目的待审核资料。

        Args:
            ctx: 项目上下文。
            status_filter: 审核状态过滤（pending / adopted / rejected / expired）。
            offset: 分页偏移。
            limit: 每页条数。

        Returns:
            元组 ``(items, total)``。
        """
        # 条件：始终带 project_id
        conditions = [WebMaterial.project_id == ctx.project_id]
        if status_filter is not None:
            # 按审核状态过滤，便于只看待审核或已采纳的
            conditions.append(WebMaterial.status == status_filter)

        # 数据查询：按 created_at 倒序
        stmt_data = (
            select(WebMaterial)
            .where(*conditions)
            .order_by(WebMaterial.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        items = list((await self.session.execute(stmt_data)).scalars().all())

        # 计数查询
        stmt_count = (
            select(func.count()).select_from(WebMaterial).where(*conditions)
        )
        total = await self.session.scalar(stmt_count) or 0

        return items, total

    async def update(
        self,
        ctx: "ProjectContext",
        id: str,
        **fields: Any,
    ) -> WebMaterial | None:
        """按 ID 更新资料字段（强制 project_id 过滤）。

        常用于审核操作：``status="adopted"``、``reviewed_at=now``、
        ``knowledge_base_id="xxx"``。

        Args:
            ctx: 项目上下文。
            id: 资料主键。
            **fields: 待更新字段。

        Returns:
            更新后的 WebMaterial 实例；不存在或属于其他项目返回 None。
        """
        # 复用基类 update
        return await super().update(ctx, id, **fields)

    async def set_status(
        self,
        ctx: "ProjectContext",
        id: str,
        status: str,
    ) -> WebMaterial | None:
        """更新资料审核状态。

        Args:
            ctx: 项目上下文。
            id: 资料主键。
            status: 审核状态（pending / adopted / rejected / expired）。

        Returns:
            更新后的 WebMaterial 实例；不存在返回 None。
        """
        # 复用 update，仅修改 status
        return await self.update(ctx, id, status=status)


# ---------------------------------------------------------------------------
# SourcePolicyRepository
# ---------------------------------------------------------------------------
class SourcePolicyRepository(BaseRepository):
    """来源策略 Repository：操作 ``source_policies`` 表。

    供采集与联网搜索共用，实现域名级白名单/黑名单。强制带 project_id 过滤。
    """

    model = SourcePolicy

    async def create(
        self,
        ctx: "ProjectContext",
        policy_type: str,
        domain: str,
        pattern: str | None = None,
    ) -> SourcePolicy:
        """创建来源策略。

        Args:
            ctx: 项目上下文。
            policy_type: 策略类型，``allow``=允许 / ``block``=禁用。
            domain: 域名，如 ``example.com``。
            pattern: 匹配模式（路径正则），可空。

        Returns:
            创建后的 SourcePolicy 实例。
        """
        # project_id 由 ctx 注入
        policy = SourcePolicy(
            project_id=ctx.project_id,
            policy_type=policy_type,
            domain=domain,
            pattern=pattern,
        )
        self.session.add(policy)
        await self.session.flush()
        return policy

    async def list(
        self,
        ctx: "ProjectContext",
        policy_type: str | None = None,
    ) -> list[SourcePolicy]:
        """列出当前项目的来源策略，可按类型过滤。

        Args:
            ctx: 项目上下文。
            policy_type: 策略类型过滤（allow / block），None 表示不过滤。

        Returns:
            SourcePolicy 列表，按创建时间倒序。
        """
        # 强制 project_id 过滤
        stmt = (
            select(SourcePolicy)
            .where(SourcePolicy.project_id == ctx.project_id)
            .order_by(SourcePolicy.created_at.desc())
        )
        if policy_type is not None:
            # 按策略类型过滤，便于单独查看白名单或黑名单
            stmt = stmt.where(SourcePolicy.policy_type == policy_type)
        return list((await self.session.execute(stmt)).scalars().all())

    async def delete(self, ctx: "ProjectContext", id: str) -> bool:
        """删除来源策略（强制 project_id 过滤）。

        Args:
            ctx: 项目上下文。
            id: 策略主键。

        Returns:
            True 表示删除成功；False 表示不存在或属于其他项目。
        """
        # 复用基类 delete
        return await super().delete(ctx, id)
