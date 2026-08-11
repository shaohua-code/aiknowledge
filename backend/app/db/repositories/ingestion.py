"""入库任务 Repository：IngestionJob。

对应 SubTask 6.1：文档入库流程的状态跟踪。

设计要点
--------
1. 每个 Document 创建后由 Service 层生成一个 IngestionJob，跟踪解析/分块/
   向量化全流程。
2. ``update_status`` 同时更新 status / stage / error_code / error_message /
   started_at / completed_at 等字段，便于精细监控。
3. 所有查询强制带 project_id 过滤，杜绝跨项目访问其他项目的入库任务。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.db.models.ingestion import IngestionJob
from app.db.repositories.base import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.project_context import ProjectContext


class IngestionJobRepository(BaseRepository):
    """入库任务 Repository：操作 ``ingestion_jobs`` 表。

    强制带 project_id 过滤。
    """

    model = IngestionJob

    async def create(
        self,
        ctx: "ProjectContext",
        document_id: str,
    ) -> IngestionJob:
        """为文档创建入库任务，初始状态为 pending。

        Args:
            ctx: 项目上下文。
            document_id: 关联文档 ID（必须属于同一项目，由复合外键保证）。

        Returns:
            创建后的 IngestionJob 实例。
        """
        # project_id 由 ctx 注入
        job = IngestionJob(
            project_id=ctx.project_id,
            document_id=document_id,
            status="pending",  # 初始状态：待处理
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def get_by_id(
        self, ctx: "ProjectContext", id: str
    ) -> IngestionJob | None:
        """按 ID 查询入库任务（强制 project_id 过滤）。

        Args:
            ctx: 项目上下文。
            id: 入库任务主键。

        Returns:
            IngestionJob 实例；不存在或属于其他项目返回 None。
        """
        # 双重过滤：id + project_id
        stmt = select(IngestionJob).where(
            IngestionJob.id == id,
            IngestionJob.project_id == ctx.project_id,
        )
        return await self.session.scalar(stmt)

    async def get_by_document(
        self,
        ctx: "ProjectContext",
        document_id: str,
    ) -> list[IngestionJob]:
        """查询文档关联的所有入库任务（一个文档可能多次重试入库）。

        Args:
            ctx: 项目上下文。
            document_id: 文档 ID。

        Returns:
            IngestionJob 列表，按创建时间倒序。
        """
        # 双重过滤：project_id + document_id
        stmt = (
            select(IngestionJob)
            .where(
                IngestionJob.project_id == ctx.project_id,
                IngestionJob.document_id == document_id,
            )
            .order_by(IngestionJob.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def update_status(
        self,
        ctx: "ProjectContext",
        id: str,
        status: str,
        **extra: Any,
    ) -> IngestionJob | None:
        """更新入库任务状态及附加字段。

        附加字段说明：
            - ``stage``: 当前处理阶段（parsing / chunking / embedding）
            - ``error_code`` / ``error_message``: 失败时填写
            - ``started_at``: 进入 processing 阶段时填写
            - ``completed_at``: 进入 ready/failed 阶段时填写

        Args:
            ctx: 项目上下文。
            id: 入库任务主键。
            status: 整体状态（pending / parsing / chunking / embedding / ready / failed）。
            **extra: 附加字段，如 ``stage="parsing"``、``error_code="PARSE_FAILED"``。

        Returns:
            更新后的 IngestionJob 实例；不存在或属于其他项目返回 None。
        """
        # 合并 status 与附加字段
        fields = {"status": status, **extra}
        # 复用基类 update：UPDATE...RETURNING，带 project_id 过滤
        return await super().update(ctx, id, **fields)
