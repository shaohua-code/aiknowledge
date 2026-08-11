"""调度 Repository：Schedule / ScheduleRun。

对应 SubTask 6.1：定时任务定义与运行记录。

设计要点
--------
1. ``ScheduleRepository`` 操作 ``schedules`` 表，业务方法强制 project_id 过滤；
   但 ``list_due`` 是调度器（Celery Beat）全局扫描到期任务，不带 project_id。
2. ``ScheduleRunRepository`` 操作 ``schedule_runs`` 表，复合唯一约束
   ``(project_id, schedule_id, planned_at)`` 保证幂等。
3. ``claim_due_run`` 使用 PostgreSQL ``FOR UPDATE SKIP LOCKED`` 实现多实例
   并发领取同一到期任务时的安全加锁，避免重复执行。
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.models.schedule import Schedule, ScheduleRun
from app.db.repositories.base import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.project_context import ProjectContext


# ---------------------------------------------------------------------------
# ScheduleRepository
# ---------------------------------------------------------------------------
class ScheduleRepository(BaseRepository):
    """定时任务 Repository：操作 ``schedules`` 表。

    业务方法强制 project_id 过滤；``list_due`` 是调度器全局查询，不带 project_id。
    """

    model = Schedule

    async def create(self, ctx: "ProjectContext", **fields: Any) -> Schedule:
        """创建定时任务。

        Args:
            ctx: 项目上下文。
            **fields: 任务字段，必须包含 ``name`` / ``task_type`` / ``cron_expression``。

        Returns:
            创建后的 Schedule 实例。
        """
        # project_id 由 ctx 注入
        fields["project_id"] = ctx.project_id
        schedule = Schedule(**fields)
        self.session.add(schedule)
        await self.session.flush()
        return schedule

    async def get_by_id(self, ctx: "ProjectContext", id: str) -> Schedule | None:
        """按 ID 查询定时任务（强制 project_id 过滤）。

        Args:
            ctx: 项目上下文。
            id: 任务主键。

        Returns:
            Schedule 实例；不存在或属于其他项目返回 None。
        """
        # 双重过滤：id + project_id
        stmt = select(Schedule).where(
            Schedule.id == id,
            Schedule.project_id == ctx.project_id,
        )
        return await self.session.scalar(stmt)

    async def list(
        self,
        ctx: "ProjectContext",
        enabled_only: bool = False,
    ) -> list[Schedule]:
        """列出当前项目的定时任务，可选仅返回启用的任务。

        Args:
            ctx: 项目上下文。
            enabled_only: True 仅返回 enabled=true 的任务。

        Returns:
            Schedule 列表，按创建时间倒序。
        """
        # 条件：始终带 project_id
        stmt = (
            select(Schedule)
            .where(Schedule.project_id == ctx.project_id)
            .order_by(Schedule.created_at.desc())
        )
        if enabled_only:
            # 仅返回启用的任务
            stmt = stmt.where(Schedule.enabled.is_(True))
        return list((await self.session.execute(stmt)).scalars().all())

    async def update(
        self,
        ctx: "ProjectContext",
        id: str,
        **fields: Any,
    ) -> Schedule | None:
        """按 ID 更新任务字段（强制 project_id 过滤）。

        Args:
            ctx: 项目上下文。
            id: 任务主键。
            **fields: 待更新字段。

        Returns:
            更新后的 Schedule 实例；不存在或属于其他项目返回 None。
        """
        # 复用基类 update
        return await super().update(ctx, id, **fields)

    async def set_enabled(
        self,
        ctx: "ProjectContext",
        id: str,
        enabled: bool,
    ) -> Schedule | None:
        """启用或停用定时任务。

        停用后 Celery Beat 不再调度此任务。

        Args:
            ctx: 项目上下文。
            id: 任务主键。
            enabled: 是否启用。

        Returns:
            更新后的 Schedule 实例；不存在或属于其他项目返回 None。
        """
        # 复用 update，仅修改 enabled
        return await self.update(ctx, id, enabled=enabled)

    async def list_due(self, now: datetime) -> list[Schedule]:
        """查询所有到期需执行的任务（调度器全局查询，不带 project_id）。

        为什么不带 project_id？
            调度器（Celery Beat）是全局组件，需要扫描所有项目的到期任务并触发执行。
            过滤条件为：``enabled=true`` AND ``next_run_at <= now``，
            利用部分索引 ``idx_schedules_due`` 加速扫描。

        Args:
            now: 当前时间，用于判断 ``next_run_at`` 是否到期。

        Returns:
            到期任务列表，按 ``next_run_at`` 升序（最早到期的先执行）。
        """
        # 全局查询：仅过滤 enabled + next_run_at
        # 利用 idx_schedules_due 部分索引（WHERE enabled = true）
        stmt = (
            select(Schedule)
            .where(
                Schedule.enabled.is_(True),
                Schedule.next_run_at <= now,
            )
            .order_by(Schedule.next_run_at.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def update_next_run(
        self,
        ctx: "ProjectContext",
        id: str,
        next_run_at: datetime | None,
    ) -> None:
        """更新任务的下次执行时间。

        由调度器在触发任务后调用，根据 cron 表达式计算下次执行时间并更新。

        Args:
            ctx: 项目上下文。
            id: 任务主键。
            next_run_at: 下次执行时间，None 表示停止调度（cron 无法计算下次时间）。
        """
        # WHERE id = ? AND project_id = ?，避免越权修改其他项目的任务
        from sqlalchemy import update as sa_update

        stmt = (
            sa_update(Schedule)
            .where(
                Schedule.id == id,
                Schedule.project_id == ctx.project_id,
            )
            .values(
                next_run_at=next_run_at,
                last_run_at=datetime.utcnow(),
            )
        )
        await self.session.execute(stmt)


# ---------------------------------------------------------------------------
# ScheduleRunRepository
# ---------------------------------------------------------------------------
class ScheduleRunRepository(BaseRepository):
    """定时任务运行记录 Repository：操作 ``schedule_runs`` 表。

    复合唯一约束 ``(project_id, schedule_id, planned_at)`` 保证幂等。
    ``claim_due_run`` 使用 ``FOR UPDATE SKIP LOCKED`` 实现多实例并发领取。
    """

    model = ScheduleRun

    async def create(
        self,
        ctx: "ProjectContext",
        schedule_id: str,
        planned_at: datetime,
    ) -> ScheduleRun | None:
        """创建运行记录，需处理唯一约束冲突。

        复合唯一约束 ``(project_id, schedule_id, planned_at)`` 保证同一任务同一
        计划时间只执行一次。若已存在相同记录，触发 ``IntegrityError``，
        本方法捕获后返回 None 表示"已存在"。

        Args:
            ctx: 项目上下文。
            schedule_id: 关联定时任务 ID。
            planned_at: 计划执行时间（由 Beat 根据 cron 推算）。

        Returns:
            创建后的 ScheduleRun 实例；若已存在（冲突）返回 None。
        """
        run = ScheduleRun(
            project_id=ctx.project_id,
            schedule_id=schedule_id,
            planned_at=planned_at,
            status="pending",  # 初始状态：待执行
        )
        self.session.add(run)
        try:
            # flush 触发唯一约束检查
            await self.session.flush()
            return run
        except IntegrityError:
            # 唯一约束冲突：同一任务同一计划时间已存在运行记录
            # 回滚 pending 状态的对象，避免污染会话
            await self.session.rollback()
            return None

    async def get_by_id(
        self, ctx: "ProjectContext", id: str
    ) -> ScheduleRun | None:
        """按 ID 查询运行记录（强制 project_id 过滤）。

        Args:
            ctx: 项目上下文。
            id: 运行记录主键。

        Returns:
            ScheduleRun 实例；不存在或属于其他项目返回 None。
        """
        # 双重过滤：id + project_id
        stmt = select(ScheduleRun).where(
            ScheduleRun.id == id,
            ScheduleRun.project_id == ctx.project_id,
        )
        return await self.session.scalar(stmt)

    async def list_by_schedule(
        self,
        ctx: "ProjectContext",
        schedule_id: str,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[ScheduleRun], int]:
        """分页列出指定任务的运行记录。

        Args:
            ctx: 项目上下文。
            schedule_id: 定时任务 ID。
            offset: 分页偏移。
            limit: 每页条数。

        Returns:
            元组 ``(items, total)``。
        """
        # 双重过滤：project_id + schedule_id
        conditions = [
            ScheduleRun.project_id == ctx.project_id,
            ScheduleRun.schedule_id == schedule_id,
        ]

        # 数据查询：按 planned_at 倒序（最新执行在前）
        stmt_data = (
            select(ScheduleRun)
            .where(*conditions)
            .order_by(ScheduleRun.planned_at.desc())
            .offset(offset)
            .limit(limit)
        )
        items = list((await self.session.execute(stmt_data)).scalars().all())

        # 计数查询
        stmt_count = (
            select(func.count()).select_from(ScheduleRun).where(*conditions)
        )
        total = await self.session.scalar(stmt_count) or 0

        return items, total

    async def update(
        self,
        ctx: "ProjectContext",
        id: str,
        **fields: Any,
    ) -> ScheduleRun | None:
        """按 ID 更新运行记录字段（强制 project_id 过滤）。

        常用于更新 status / started_at / completed_at / result_summary /
        error_code / attempt / queue_job_id 等字段。

        Args:
            ctx: 项目上下文。
            id: 运行记录主键。
            **fields: 待更新字段。

        Returns:
            更新后的 ScheduleRun 实例；不存在或属于其他项目返回 None。
        """
        # 复用基类 update
        return await super().update(ctx, id, **fields)

    async def claim_due_run(
        self,
        schedule_id: str,
        planned_at: datetime,
    ) -> ScheduleRun | None:
        """领取到期任务（使用 ``FOR UPDATE SKIP LOCKED``，全局查询）。

        为什么不带 project_id？
            调度器是全局组件，需要跨项目领取到期任务。本方法通过行级锁
            保证多实例并发领取同一任务时的安全性。

        实现逻辑（事务内 SELECT + INSERT）：
            1. ``SELECT ... FOR UPDATE SKIP LOCKED`` 查询是否存在该 (schedule_id, planned_at)
               的运行记录，跳过被其他实例锁住的行。
            2. 若已存在：返回 None（已被其他实例领取）。
            3. 若不存在：INSERT 一条新的 pending 运行记录。
            4. 若 INSERT 触发唯一约束冲突（极端竞态）：返回 None。

        ``FOR UPDATE SKIP LOCKED`` 的作用：
            PostgreSQL 行级锁，多个 Worker 同时领取时，已被锁住的行会被跳过，
            避免阻塞等待，实现"先到先得 + 不阻塞"。

        Args:
            schedule_id: 定时任务 ID。
            planned_at: 计划执行时间。

        Returns:
            领取成功的 ScheduleRun 实例；已被其他实例领取返回 None。
        """
        # 步骤 1：SELECT ... FOR UPDATE SKIP LOCKED 查询是否已存在
        # with_for_update(skip_locked=True) 生成 "FOR UPDATE SKIP LOCKED"
        stmt_check = (
            select(ScheduleRun)
            .where(
                ScheduleRun.schedule_id == schedule_id,
                ScheduleRun.planned_at == planned_at,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        existing = await self.session.scalar(stmt_check)

        # 步骤 2：已存在 → 已被其他实例领取，返回 None
        if existing is not None:
            return None

        # 步骤 3：不存在 → 创建新的运行记录
        # 此时 project_id 未知（全局查询），需要先查询 schedule 获取 project_id
        stmt_schedule = select(Schedule).where(Schedule.id == schedule_id)
        schedule = await self.session.scalar(stmt_schedule)
        if schedule is None:
            # 调度器传入的 schedule_id 不存在，防御性返回 None
            return None

        run = ScheduleRun(
            project_id=schedule.project_id,
            schedule_id=schedule_id,
            planned_at=planned_at,
            status="pending",
        )
        self.session.add(run)
        try:
            await self.session.flush()
            return run
        except IntegrityError:
            # 极端竞态：步骤 1 与步骤 3 之间另一实例已插入
            # 触发唯一约束冲突，回滚并返回 None
            await self.session.rollback()
            return None
