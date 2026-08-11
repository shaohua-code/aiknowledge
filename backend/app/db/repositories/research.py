"""研究 Repository：ResearchTask / ResearchEvidence / ResearchResult / RetrievalLog。

对应 SubTask 6.1：短链路一次生成的研究任务与证据、结果、检索日志。

设计要点
--------
1. ``ResearchTask`` 是研究流程的主任务，``request_id`` 为对外幂等键（UNIQUE）。
2. ``ResearchEvidence`` 存储三类证据（internal / web / tool），最多 8 条。
3. ``ResearchResult`` 存储大模型一次生成的结构化结论。
4. ``RetrievalLog`` 记录每次检索的命中数、耗时、分数，用于性能分析。
5. 所有查询强制带 project_id 过滤。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from app.db.models.research import (
    ResearchEvidence,
    ResearchResult,
    ResearchTask,
    RetrievalLog,
)
from app.db.repositories.base import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.project_context import ProjectContext


# ---------------------------------------------------------------------------
# ResearchTaskRepository
# ---------------------------------------------------------------------------
class ResearchTaskRepository(BaseRepository):
    """研究任务 Repository：操作 ``research_tasks`` 表。

    强制带 project_id 过滤。``request_id`` 为对外幂等键。
    """

    model = ResearchTask

    async def create(self, ctx: "ProjectContext", **fields: Any) -> ResearchTask:
        """创建研究任务。

        Args:
            ctx: 项目上下文。
            **fields: 任务字段，必须包含 ``request_id`` / ``question`` /
                ``output_type`` / ``strategy`` / ``use_web``。

        Returns:
            创建后的 ResearchTask 实例。
        """
        # project_id 由 ctx 注入
        fields["project_id"] = ctx.project_id
        task = ResearchTask(**fields)
        self.session.add(task)
        await self.session.flush()
        return task

    async def get_by_id(
        self, ctx: "ProjectContext", id: str
    ) -> ResearchTask | None:
        """按 ID 查询研究任务（强制 project_id 过滤）。

        Args:
            ctx: 项目上下文。
            id: 任务主键。

        Returns:
            ResearchTask 实例；不存在或属于其他项目返回 None。
        """
        # 双重过滤：id + project_id
        stmt = select(ResearchTask).where(
            ResearchTask.id == id,
            ResearchTask.project_id == ctx.project_id,
        )
        return await self.session.scalar(stmt)

    async def get_by_request_id(
        self,
        ctx: "ProjectContext",
        request_id: str,
    ) -> ResearchTask | None:
        """按对外 request_id 查询任务（客户端查询结果用）。

        ``request_id`` 全局 UNIQUE，但仍带 project_id 过滤作为防御性措施，
        避免任何潜在的全局查询路径泄露其他项目任务。

        Args:
            ctx: 项目上下文。
            request_id: 对外请求 ID。

        Returns:
            ResearchTask 实例；不存在或属于其他项目返回 None。
        """
        # 双重过滤：project_id + request_id
        stmt = select(ResearchTask).where(
            ResearchTask.project_id == ctx.project_id,
            ResearchTask.request_id == request_id,
        )
        return await self.session.scalar(stmt)

    async def list(
        self,
        ctx: "ProjectContext",
        offset: int = 0,
        limit: int = 20,
        status: str | None = None,
    ) -> tuple[list[ResearchTask], int]:
        """分页列出当前项目的研究任务，可按状态过滤。

        Args:
            ctx: 项目上下文。
            offset: 分页偏移。
            limit: 每页条数。
            status: 按任务状态过滤（小写形式，如 pending/running/success），
                None 表示不过滤，返回所有状态的任务。

        Returns:
            元组 ``(items, total)``。
        """
        # 条件：始终带 project_id，保证跨项目隔离
        conditions = [ResearchTask.project_id == ctx.project_id]
        # 可选状态过滤：传入 status 时追加等值条件
        if status is not None:
            conditions.append(ResearchTask.status == status)

        # 数据查询：按 created_at 倒序
        stmt_data = (
            select(ResearchTask)
            .where(*conditions)
            .order_by(ResearchTask.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        items = list((await self.session.execute(stmt_data)).scalars().all())

        # 计数查询
        stmt_count = (
            select(func.count()).select_from(ResearchTask).where(*conditions)
        )
        total = await self.session.scalar(stmt_count) or 0

        return items, total

    async def update(
        self,
        ctx: "ProjectContext",
        id: str,
        **fields: Any,
    ) -> ResearchTask | None:
        """按 ID 更新任务字段（强制 project_id 过滤）。

        常用于更新 status / started_at / completed_at / total_duration_ms /
        degraded / degraded_reasons 等运行时字段。

        Args:
            ctx: 项目上下文。
            id: 任务主键。
            **fields: 待更新字段。

        Returns:
            更新后的 ResearchTask 实例；不存在或属于其他项目返回 None。
        """
        # 复用基类 update
        return await super().update(ctx, id, **fields)


# ---------------------------------------------------------------------------
# ResearchEvidenceRepository
# ---------------------------------------------------------------------------
class ResearchEvidenceRepository(BaseRepository):
    """研究证据 Repository：操作 ``research_evidence`` 表。

    存储内部检索/联网搜索/业务工具三类证据。强制带 project_id 过滤。
    """

    model = ResearchEvidence

    async def create(self, ctx: "ProjectContext", **fields: Any) -> ResearchEvidence:
        """创建单条证据。

        Args:
            ctx: 项目上下文。
            **fields: 证据字段，必须包含 ``research_task_id`` / ``evidence_type`` /
                ``title`` / ``snippet``。

        Returns:
            创建后的 ResearchEvidence 实例。
        """
        # project_id 由 ctx 注入
        fields["project_id"] = ctx.project_id
        evidence = ResearchEvidence(**fields)
        self.session.add(evidence)
        await self.session.flush()
        return evidence

    async def bulk_create(
        self,
        ctx: "ProjectContext",
        evidences: list[dict[str, Any]],
    ) -> list[ResearchEvidence]:
        """批量创建证据（研究流程中证据收集后一次性写入）。

        Args:
            ctx: 项目上下文。
            evidences: 证据字段字典列表。

        Returns:
            创建后的 ResearchEvidence 实例列表。
        """
        # 统一注入 project_id，避免每条 dict 重复传入
        instances = [
            ResearchEvidence(**{**e, "project_id": ctx.project_id})
            for e in evidences
        ]
        self.session.add_all(instances)
        await self.session.flush()
        return instances

    async def list_by_task(
        self,
        ctx: "ProjectContext",
        task_id: str,
    ) -> list[ResearchEvidence]:
        """列出指定任务的所有证据。

        Args:
            ctx: 项目上下文。
            task_id: 研究任务 ID。

        Returns:
            ResearchEvidence 列表，按 score 降序（评分高在前）。
        """
        # 双重过滤：project_id + research_task_id
        stmt = (
            select(ResearchEvidence)
            .where(
                ResearchEvidence.project_id == ctx.project_id,
                ResearchEvidence.research_task_id == task_id,
            )
            .order_by(ResearchEvidence.score.desc().nulls_last())
        )
        return list((await self.session.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# ResearchResultRepository
# ---------------------------------------------------------------------------
class ResearchResultRepository(BaseRepository):
    """研究结果 Repository：操作 ``research_results`` 表。

    存储大模型一次生成的结构化结论。强制带 project_id 过滤。
    """

    model = ResearchResult

    async def create(self, ctx: "ProjectContext", **fields: Any) -> ResearchResult:
        """创建研究结果。

        Args:
            ctx: 项目上下文。
            **fields: 结果字段，必须包含 ``research_task_id`` / ``answer``。

        Returns:
            创建后的 ResearchResult 实例。
        """
        # project_id 由 ctx 注入
        fields["project_id"] = ctx.project_id
        result = ResearchResult(**fields)
        self.session.add(result)
        await self.session.flush()
        return result

    async def get_by_task(
        self,
        ctx: "ProjectContext",
        task_id: str,
    ) -> ResearchResult | None:
        """按任务 ID 查询研究结果（每任务至多一条结果）。

        Args:
            ctx: 项目上下文。
            task_id: 研究任务 ID。

        Returns:
            ResearchResult 实例；不存在或属于其他项目返回 None。
        """
        # 双重过滤：project_id + research_task_id
        stmt = select(ResearchResult).where(
            ResearchResult.project_id == ctx.project_id,
            ResearchResult.research_task_id == task_id,
        )
        return await self.session.scalar(stmt)


# ---------------------------------------------------------------------------
# RetrievalLogRepository
# ---------------------------------------------------------------------------
class RetrievalLogRepository(BaseRepository):
    """检索日志 Repository：操作 ``retrieval_logs`` 表。

    用于性能分析与优化。强制带 project_id 过滤。
    """

    model = RetrievalLog

    async def create(self, ctx: "ProjectContext", **fields: Any) -> RetrievalLog:
        """创建检索日志。

        Args:
            ctx: 项目上下文。
            **fields: 日志字段，必须包含 ``query``。

        Returns:
            创建后的 RetrievalLog 实例。
        """
        # project_id 由 ctx 注入
        fields["project_id"] = ctx.project_id
        log = RetrievalLog(**fields)
        self.session.add(log)
        await self.session.flush()
        return log

    async def list_by_project(
        self,
        ctx: "ProjectContext",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[RetrievalLog], int]:
        """分页列出当前项目的检索日志。

        Args:
            ctx: 项目上下文。
            offset: 分页偏移。
            limit: 每页条数。

        Returns:
            元组 ``(items, total)``。
        """
        # 条件：始终带 project_id
        conditions = [RetrievalLog.project_id == ctx.project_id]

        # 数据查询：按 created_at 倒序
        stmt_data = (
            select(RetrievalLog)
            .where(*conditions)
            .order_by(RetrievalLog.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        items = list((await self.session.execute(stmt_data)).scalars().all())

        # 计数查询
        stmt_count = (
            select(func.count()).select_from(RetrievalLog).where(*conditions)
        )
        total = await self.session.scalar(stmt_count) or 0

        return items, total
