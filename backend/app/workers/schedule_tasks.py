"""定时任务派发与执行（Task 18.3）。

对应 Task 18：Celery Beat 每分钟扫描到期任务并投递执行，以及具体任务的执行逻辑。

整体架构
--------
1. **Celery Beat**（定时调度器，全局唯一实例）每分钟触发 ``dispatch_due_schedules``
   任务（见 ``celery_app.conf.beat_schedule``）。
2. ``dispatch_due_schedules`` 扫描所有 ``enabled=true AND next_run_at <= now()`` 的
   Schedule（全局查询，不带 project_id），对每个到期任务：
   a. 调用 ``ScheduleRunRepository.claim_due_run``（``FOR UPDATE SKIP LOCKED``）
      领取任务，幂等键 ``schedule_id + planned_at`` 防止重复执行。
   b. claim 成功 → 投递 ``execute_schedule_task.delay(run_id)`` 到 maintenance 队列。
   c. claim 失败（已被其他 Worker 领取）→ 跳过。
   d. 更新 ``Schedule.next_run_at`` 为 cron 推算的下次时间。
3. ``execute_schedule_task`` 在 Worker 中执行具体业务：
   a. 查询 ScheduleRun（带 project_id 过滤，二次校验归属）。
   b. 恢复 ProjectContext（从 Schedule.project_id + 关联 Project.code 构造）。
   c. 重新校验：项目是否 active、Schedule 是否仍 enabled、关联资源是否存在。
   d. 按 task_type 分发到具体业务执行器。
   e. 更新 ScheduleRun 状态与结果。
   f. 失败重试：若 attempt < max_retries 且为可重试错误 → ``self.retry()``。

为什么用 FOR UPDATE SKIP LOCKED？
---------------------------------
PostgreSQL 行级锁，多个 Worker 实例同时领取同一到期任务时：
- 普通锁（FOR UPDATE）：后续 Worker 阻塞等待第一个事务提交，造成排队与吞吐下降。
- SKIP LOCKED：已被锁住的行直接跳过，Worker 立即去领取其他任务，
  实现"先到先得 + 不阻塞"，多 Worker 并行领取不同任务，吞吐最大化。

幂等键设计（schedule_id + planned_at）
---------------------------------------
``schedule_runs`` 表有复合唯一约束 ``(project_id, schedule_id, planned_at)``。
同一任务同一计划时间只能创建一条运行记录：
- 正常场景：Beat 每分钟扫描，planned_at 为 cron 推算的下次时间，每次不同。
- 异常场景（Beat 重复触发 / 多实例）：相同 planned_at 触发 INSERT 冲突，
  ``IntegrityError`` 被 ``claim_due_run`` 捕获，返回 None，调用方跳过，
  保证同一计划时间只执行一次。

并发策略
--------
- ``skip``：若上次执行仍在 running，跳过本次触发（避免堆积）。
- ``queue``：允许排队执行（适用于幂等且可并行的任务）。
当前实现：claim_due_run 仅做"领取或跳过"，并发策略在执行前（``_execute_schedule_task_async``
阶段 4）检查是否有 running 记录，skip 策略下跳过本次执行。

状态机流转
----------
pending → running → success / failed / timeout
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

from celery import Task
from celery.exceptions import Retry, SoftTimeLimitExceeded
from sqlalchemy import select

from app.core.project_context import ProjectContext
from app.db.models.project import Project
from app.db.models.schedule import Schedule, ScheduleRun
from app.db.repositories.schedule import (
    ScheduleRepository,
    ScheduleRunRepository,
)
from app.db.session import AsyncSessionFactory
from app.modules.scheduler.cron_utils import compute_next_run
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 错误码常量：统一标识失败原因，便于监控与重试判断
# ---------------------------------------------------------------------------
# 运行记录不存在或项目归属不一致：参数被篡改或记录已被删除
ERROR_RUN_NOT_FOUND = "RUN_NOT_FOUND"
# 项目已停用：执行前发现项目 status=disabled
ERROR_PROJECT_DISABLED = "PROJECT_DISABLED"
# 任务已停用：执行前发现 schedule.enabled=false（可能在入队后被暂停）
ERROR_SCHEDULE_DISABLED = "SCHEDULE_DISABLED"
# 关联资源不存在：如采集源/知识库已被删除
ERROR_RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
# 执行超时：Celery soft_time_limit 触发
ERROR_TASK_TIMEOUT = "TASK_TIMEOUT"
# 执行异常：未预期的内部错误
ERROR_EXECUTION_FAILED = "EXECUTION_FAILED"

# 可重试错误码集合：这些错误认为是临时性的，可自动重试
# 项目停用/任务停用/资源不存在属于"配置类"错误，重试无意义，不在此集合
RETRYABLE_ERROR_CODES: frozenset[str] = frozenset({
    ERROR_TASK_TIMEOUT,
    ERROR_EXECUTION_FAILED,
})


# ---------------------------------------------------------------------------
# 任务 1：调度派发（Celery Beat 每分钟触发）
# ---------------------------------------------------------------------------
@celery_app.task(
    name="app.workers.schedule_tasks.dispatch_due_schedules",
    queue="maintenance",
    acks_late=True,
)
def dispatch_due_schedules() -> dict:
    """每分钟扫描到期任务并投递执行（Celery Beat 触发）。

    本任务由 Celery Beat 按 ``crontab(minute="*")`` 每分钟触发一次，
    扫描所有项目的到期定时任务并投递到 maintenance 队列。

    为什么用 ``asyncio.run``？
        Celery Worker 是同步执行模型（基于 prefork），但数据库访问是异步 API。
        ``asyncio.run`` 创建临时事件循环执行异步逻辑，结束后关闭循环。

    Returns:
        摘要字典，含 ``scanned``（扫描到的到期任务数）、
        ``dispatched``（成功投递数）、``skipped``（被其他实例领取的跳过数）。
        便于监控与日志排查。
    """
    return asyncio.run(_dispatch_due_schedules_async())


async def _dispatch_due_schedules_async() -> dict:
    """异步实现：扫描到期任务并投递执行。

    流程
    ----
    1. 查询所有 ``enabled=true AND next_run_at <= now()`` 的 Schedule（全局）。
    2. 对每个到期任务：
       a. ``claim_due_run``（FOR UPDATE SKIP LOCKED + INSERT）领取任务。
       b. claim 成功 → ``execute_schedule_task.delay(run_id)`` 投递执行。
       c. claim 失败 → 跳过（已被其他 Worker 领取）。
       d. 更新 ``Schedule.next_run_at`` 为 cron 推算的下次时间。
    3. 提交事务。

    Returns:
        摘要字典。
    """
    now = datetime.now(timezone.utc)
    scanned = 0
    dispatched = 0
    skipped = 0

    # 独立会话：派发任务在独立事务中完成
    async with AsyncSessionFactory() as session:
        schedule_repo = ScheduleRepository(session)
        run_repo = ScheduleRunRepository(session)

        # ------------------------------------------------------------------
        # 步骤 1：全局查询到期任务（不带 project_id，调度器是全局组件）
        # ------------------------------------------------------------------
        # list_due 利用部分索引 idx_schedules_due（WHERE enabled = true）加速扫描
        due_schedules = await schedule_repo.list_due(now)
        scanned = len(due_schedules)

        for schedule in due_schedules:
            # planned_at 使用 schedule.next_run_at（cron 推算的计划时间）
            # 幂等键 = schedule_id + planned_at，保证同一计划时间只执行一次
            planned_at = schedule.next_run_at or now

            # --------------------------------------------------------------
            # 步骤 2a：claim_due_run（FOR UPDATE SKIP LOCKED + INSERT）
            # --------------------------------------------------------------
            # 多实例并发领取同一到期任务时：
            # - 第一个实例 SELECT 未锁定行 → INSERT 成功 → 返回 run
            # - 后续实例 SELECT 命中已被锁定的行 → SKIP LOCKED 跳过 → 返回 None
            # 极端竞态下 INSERT 触发唯一约束冲突 → 返回 None
            run = await run_repo.claim_due_run(schedule.id, planned_at)

            if run is None:
                # 已被其他实例领取，跳过
                skipped += 1
                continue

            # --------------------------------------------------------------
            # 步骤 2b：claim 成功 → 投递执行任务
            # --------------------------------------------------------------
            # delay() 将任务投递到 maintenance 队列，立即返回
            # execute_schedule_task 在 Worker 中按 task_type 分发执行
            async_result = execute_schedule_task.delay(run.id)

            # 回写 queue_job_id，便于后续追踪队列任务
            # 构造只含 project_id 的 ctx 调用 update（update 方法带 project_id 过滤）
            claim_ctx = ProjectContext(
                project_id=run.project_id,
                project_code="",  # 派发阶段不需要 project_code
                environment="worker",
            )
            await run_repo.update(
                claim_ctx,
                run.id,
                queue_job_id=async_result.id,
            )
            dispatched += 1

            # --------------------------------------------------------------
            # 步骤 2c：更新 Schedule.next_run_at 为 cron 推算的下次时间
            # --------------------------------------------------------------
            # 以当前时间为基准计算下次触发点，保证下一周期被 Beat 扫描
            try:
                next_run_at = compute_next_run(
                    schedule.cron_expression,
                    schedule.timezone,
                    now,
                )
            except ValueError:
                # cron 表达式或时区异常（理论上不应发生，创建时已校验）
                # 设置为 None 停止后续调度，避免无限循环
                logger.exception(
                    "计算下次执行时间失败，停止调度 schedule=%s cron=%s tz=%s",
                    schedule.id,
                    schedule.cron_expression,
                    schedule.timezone,
                )
                next_run_at = None

            await schedule_repo.update_next_run(claim_ctx, schedule.id, next_run_at)

        # 提交事务：保证 run 创建、queue_job_id 回写、next_run_at 更新全部持久化
        await session.commit()

    logger.info(
        "调度派发完成：scanned=%d dispatched=%d skipped=%d",
        scanned,
        dispatched,
        skipped,
    )
    return {
        "scanned": scanned,
        "dispatched": dispatched,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# 任务 2：执行具体定时任务
# ---------------------------------------------------------------------------
@celery_app.task(
    name="app.workers.schedule_tasks.execute_schedule_task",
    queue="maintenance",
    bind=True,
    acks_late=True,
    time_limit=600,
    soft_time_limit=540,
)
def execute_schedule_task(self: Task, run_id: str) -> None:
    """执行具体的定时任务（Worker 中执行）。

    本任务由 ``dispatch_due_schedules`` 或手动触发接口投递，按 task_type
    分发到具体业务执行器。

    超时与重试策略
    --------------
    - ``soft_time_limit=540``：任务执行 540 秒后抛 ``SoftTimeLimitExceeded``，
      业务代码捕获后标记 run status='timeout'，优雅退出。
    - ``time_limit=600``：硬超时 600 秒，超过将被 Worker 强制终止（SIGKILL）。
    - 失败重试：若 ``attempt < max_retries`` 且错误码在 ``RETRYABLE_ERROR_CODES``
      中，调用 ``self.retry()`` 自动重试，否则标记 failed 不重试。

    Args:
        self: Celery Task 实例（``bind=True`` 注入），用于 ``self.retry()``。
        run_id: 运行记录 ID（ScheduleRun.id），由派发任务或手动触发传入。
    """
    try:
        # 在独立事件循环中执行异步逻辑
        asyncio.run(_execute_schedule_task_async(self, run_id))
    except SoftTimeLimitExceeded:
        # 软超时：任务执行超过 540s，标记 timeout 并优雅退出
        # 原事件循环已被 asyncio.run 关闭，需新建循环处理 timeout 标记
        logger.warning("定时任务执行超时 run_id=%s", run_id)
        asyncio.run(_mark_timeout(run_id))
    except Retry:
        # Retry 异常由 _execute_schedule_task_async 中的 self.retry() 抛出
        # 必须重新抛出，让 Celery 框架捕获并按 countdown 重新投递任务
        # 若被下方 except Exception 吞掉，重试将永远不会发生
        raise
    except Exception:
        # 其他未预期异常：记录日志，不再重试
        # 业务错误已在异步层标记 failed；可重试错误已通过 raise task.retry() 触发重试
        logger.exception("定时任务执行失败 run_id=%s", run_id)


async def _execute_schedule_task_async(task: Task, run_id: str) -> None:
    """异步实现：执行具体定时任务。

    流程
    ----
    1. 查询 ScheduleRun（带 project_id 过滤，二次校验归属）。
    2. 恢复 ProjectContext（从 Schedule.project_id + 关联 Project.code 构造）。
    3. 重新校验：
       - 项目是否 active（停用 → 标记 failed，error_code='PROJECT_DISABLED'）
       - Schedule 是否仍 enabled（停用 → 标记 failed，error_code='SCHEDULE_DISABLED'）
    4. 并发策略检查（skip 策略下若有 running 记录则跳过）。
    5. 更新 run status='running'，记录 started_at。
    6. 按 task_type 分发执行。
    7. 更新 run status='success'/'failed'，记录 completed_at / result_summary / error_code。
    8. 失败重试：若可重试且 attempt < max_retries → ``task.retry()``。

    Args:
        task: Celery Task 实例，用于重试。
        run_id: 运行记录 ID。
    """
    async with AsyncSessionFactory() as session:
        # ------------------------------------------------------------------
        # 阶段 1：查询 ScheduleRun（全局查询，无 project_id 过滤）
        # ------------------------------------------------------------------
        # Worker 接收 run_id，此时 project_id 未知，需先查询 run 获取 project_id
        # 再构造 ctx 做后续带 project_id 过滤的操作
        stmt_run = select(ScheduleRun).where(ScheduleRun.id == run_id)
        run = await session.scalar(stmt_run)
        if run is None:
            # 运行记录不存在（理论不应发生，防御性处理）
            logger.warning("运行记录不存在 run_id=%s，跳过执行", run_id)
            return

        # ------------------------------------------------------------------
        # 阶段 2：查询关联 Schedule 与 Project，恢复 ProjectContext
        # ------------------------------------------------------------------
        # 查询 Schedule（全局，claim_due_run 已保证 run.schedule_id 有效）
        stmt_schedule = select(Schedule).where(Schedule.id == run.schedule_id)
        schedule = await session.scalar(stmt_schedule)
        if schedule is None:
            # Schedule 已被删除（运行记录保留但任务定义不存在）
            await _mark_failed_global(
                session,
                run,
                ERROR_RESOURCE_NOT_FOUND,
                f"关联定时任务 {run.schedule_id} 不存在（可能已被删除）",
            )
            await session.commit()
            return

        # 查询关联 Project，获取 project_code 与 status
        stmt_project = select(Project).where(Project.id == schedule.project_id)
        project = await session.scalar(stmt_project)
        if project is None:
            # 项目不存在（理论不应发生，外键约束保证）
            await _mark_failed_global(
                session,
                run,
                ERROR_RESOURCE_NOT_FOUND,
                f"关联项目 {schedule.project_id} 不存在",
            )
            await session.commit()
            return

        # 二次校验归属：run.project_id 必须与 schedule.project_id 一致
        # 防止 run 记录被篡改后指向其他项目的 schedule
        if run.project_id != schedule.project_id:
            logger.error(
                "归属不一致 run.project_id=%s schedule.project_id=%s",
                run.project_id,
                schedule.project_id,
            )
            await _mark_failed_global(
                session,
                run,
                ERROR_RUN_NOT_FOUND,
                "运行记录与定时任务归属不一致",
            )
            await session.commit()
            return

        # 恢复 ProjectContext：用于后续带 project_id 过滤的 Repository 操作
        ctx = ProjectContext(
            project_id=project.id,
            project_code=project.code,
            environment="worker",
        )

        # ------------------------------------------------------------------
        # 阶段 3：重新校验项目与任务状态
        # ------------------------------------------------------------------
        # 为什么 Worker 要再次校验？
        #   任务入队到执行有时间差，期间项目可能被停用、任务可能被暂停，
        #   执行前必须重新校验，避免执行已失效的任务。

        # 项目停用 → 标记 failed，不重试（配置类错误，重试无意义）
        if project.status != "active":
            await _mark_failed(
                ctx,
                session,
                run,
                ERROR_PROJECT_DISABLED,
                f"项目 {project.code} 已停用",
            )
            await session.commit()
            return

        # 任务停用 → 标记 failed，不重试
        # 注：手动触发接口已校验 enabled，但自动调度的任务可能在入队后被暂停
        if not schedule.enabled:
            await _mark_failed(
                ctx,
                session,
                run,
                ERROR_SCHEDULE_DISABLED,
                f"定时任务 {schedule.name} 已停用",
            )
            await session.commit()
            return

        # ------------------------------------------------------------------
        # 阶段 4：并发策略检查（skip 策略下若有 running 记录则跳过）
        # ------------------------------------------------------------------
        # 查询是否有其他 running 状态的运行记录（排除当前 run）
        if schedule.concurrency_policy == "skip":
            stmt_running = (
                select(ScheduleRun)
                .where(
                    ScheduleRun.schedule_id == schedule.id,
                    ScheduleRun.status == "running",
                    ScheduleRun.id != run.id,
                )
                .limit(1)
            )
            existing_running = await session.scalar(stmt_running)
            if existing_running is not None:
                # 已有运行中的任务，按 skip 策略跳过本次
                await _mark_failed(
                    ctx,
                    session,
                    run,
                    "SKIPPED_BY_CONCURRENCY",
                    f"已有运行中的任务 {existing_running.id}，按 skip 策略跳过",
                )
                # 标记为 skipped（用 failed + 特定 error_code 表达），不重试
                await session.commit()
                logger.info(
                    "按 skip 策略跳过 schedule=%s run=%s（已有 running=%s）",
                    schedule.id,
                    run.id,
                    existing_running.id,
                )
                return

        # ------------------------------------------------------------------
        # 阶段 5：更新 run status='running'，记录 started_at
        # ------------------------------------------------------------------
        run_repo = ScheduleRunRepository(session)
        started_at = datetime.now(timezone.utc)
        await run_repo.update(
            ctx,
            run.id,
            status="running",
            started_at=started_at,
        )
        # 提交状态变更：保证客户端查询时能看到 RUNNING 状态
        await session.commit()

        # ------------------------------------------------------------------
        # 阶段 6：按 task_type 分发执行
        # ------------------------------------------------------------------
        result_summary: dict
        try:
            result_summary = await _dispatch_by_task_type(ctx, session, schedule, run)
            # 执行成功 → 更新 run status='success'
            await run_repo.update(
                ctx,
                run.id,
                status="success",
                completed_at=datetime.now(timezone.utc),
                result_summary=result_summary,
            )
            await session.commit()
            logger.info(
                "定时任务执行成功 schedule=%s run=%s task_type=%s",
                schedule.id,
                run.id,
                schedule.task_type,
            )

        except Exception as exc:
            # 执行失败 → 更新 run status='failed'
            error_code = _map_execution_error(exc)
            error_message = str(exc)
            await run_repo.update(
                ctx,
                run.id,
                status="failed",
                completed_at=datetime.now(timezone.utc),
                error_code=error_code,
                error_message=error_message,
                # 重试次数 +1
                attempt=run.attempt + 1,
            )
            await session.commit()
            logger.exception(
                "定时任务执行失败 schedule=%s run=%s error_code=%s",
                schedule.id,
                run.id,
                error_code,
            )

            # ----------------------------------------------------------
            # 阶段 7：失败重试判断
            # ----------------------------------------------------------
            # 仅对可重试错误重试（超时/执行异常），配置类错误不重试
            # 重试次数上限取自 schedule.max_retries
            if error_code in RETRYABLE_ERROR_CODES and run.attempt < schedule.max_retries:
                logger.info(
                    "定时任务准备重试 run_id=%s attempt=%d/%d",
                    run.id,
                    run.attempt + 1,
                    schedule.max_retries,
                )
                # self.retry() 重新投递任务到队列，带指数退避
                # countdown 随重试次数递增，避免瞬时雪崩
                raise task.retry(
                    exc=exc,
                    countdown=30 * (run.attempt + 1),
                )


async def _dispatch_by_task_type(
    ctx: ProjectContext,
    session,  # AsyncSession
    schedule: Schedule,
    run: ScheduleRun,
) -> dict:
    """按 task_type 分发到具体业务执行器。

    支持的任务类型
    --------------
    - ``crawl_source``: 触发采集源爬取（调用 Task 19 的 run_crawl，占位 TODO）
    - ``tool_sync``: 工具数据同步（占位 TODO）
    - ``research_run``: 触发研究任务（复用 ResearchService）
    - ``reindex_knowledge``: 重建知识库向量索引（占位 TODO）
    - ``expire_knowledge``: 过期知识清理（占位 TODO）

    Args:
        ctx: 项目上下文（已恢复 project_id 与 project_code）。
        session: 异步数据库会话。
        schedule: 定时任务定义（含 config）。
        run: 运行记录。

    Returns:
        执行结果摘要字典，写入 ``run.result_summary``。

    Raises:
        NotImplementedError: 未实现的 task_type（占位）。
        Exception: 业务执行器抛出的异常，由上层捕获标记 failed。
    """
    # config 是任务配置，不同 task_type 携带不同字段
    config = schedule.config or {}
    task_type = schedule.task_type

    if task_type == "crawl_source":
        # CRAWL_SOURCE：触发采集源爬取
        # Task 19 实现完整采集流程，此处调用其 Celery 任务
        # 延迟导入避免循环依赖
        from app.workers.crawl_tasks import run_crawl

        crawl_source_id = config.get("crawlSourceId")
        if not crawl_source_id:
            raise ValueError("CRAWL_SOURCE 任务缺少 crawlSourceId 配置")

        # 同步调用 run_crawl（占位实现会抛 NotImplementedError）
        # TODO Task 19: run_crawl 实现后改为 await 或保持同步调用
        run_crawl.delay(ctx.project_id, crawl_source_id)
        return {
            "taskType": "CRAWL_SOURCE",
            "crawlSourceId": crawl_source_id,
            "queued": True,
        }

    if task_type == "tool_sync":
        # TOOL_SYNC：工具数据同步（占位 TODO）
        # TODO: 调用 ToolExecutor 或专用同步任务
        tool_code = config.get("toolCode")
        if not tool_code:
            raise ValueError("TOOL_SYNC 任务缺少 toolCode 配置")
        raise NotImplementedError("TOOL_SYNC 任务待实现")

    if task_type == "research_run":
        # RESEARCH_RUN：触发研究任务（复用 ResearchService）
        return await _execute_research_run(ctx, session, config, run)

    if task_type == "reindex_knowledge":
        # REINDEX_KNOWLEDGE：重建知识库向量索引（占位 TODO）
        # TODO: 调用向量重建任务
        kb_id = config.get("knowledgeBaseId")
        if not kb_id:
            raise ValueError("REINDEX_KNOWLEDGE 任务缺少 knowledgeBaseId 配置")
        raise NotImplementedError("REINDEX_KNOWLEDGE 任务待实现")

    if task_type == "expire_knowledge":
        # EXPIRE_KNOWLEDGE：过期知识清理（占位 TODO）
        # TODO: 调用过期清理任务
        kb_id = config.get("knowledgeBaseId")
        if not kb_id:
            raise ValueError("EXPIRE_KNOWLEDGE 任务缺少 knowledgeBaseId 配置")
        raise NotImplementedError("EXPIRE_KNOWLEDGE 任务待实现")

    # 未知 task_type（防御性处理）
    raise ValueError(f"未知的任务类型: {task_type}")


async def _execute_research_run(
    ctx: ProjectContext,
    session,  # AsyncSession
    config: dict,
    run: ScheduleRun,
) -> dict:
    """执行 RESEARCH_RUN 类型任务：复用 ResearchService。

    从 config 解析研究参数，调用 ``ResearchService.run`` 执行短链路研究。
    研究结果存入 ResearchTask（由 ResearchService 内部持久化），
    此处仅返回摘要写入 ``run.result_summary``。

    Args:
        ctx: 项目上下文。
        session: 异步数据库会话。
        config: 任务配置，含 question / strategy / knowledgeBaseIds / toolCodes。
        run: 运行记录（用于生成 request_id）。

    Returns:
        执行结果摘要字典。

    Raises:
        ValueError: 缺少必填的 question 配置。
        Exception: ResearchService.run 抛出的异常。
    """
    from app.modules.research.service import ResearchService

    question = config.get("question")
    if not question:
        raise ValueError("RESEARCH_RUN 任务缺少 question 配置")

    # 研究参数：从 config 读取，缺省值与 ResearchRunRequest 默认一致
    output_type = config.get("outputType", "narrative")
    strategy = config.get("strategy", "full")
    knowledge_base_ids_str = config.get("knowledgeBaseIds", [])
    tool_codes = config.get("toolCodes", [])

    # knowledge_base_ids 字符串列表转 UUID 列表（ResearchService 期望 UUID）
    kb_uuids: list[UUID] = []
    for kb_id_str in knowledge_base_ids_str:
        kb_uuids.append(UUID(kb_id_str))

    # request_id 用 run.id 标识，便于关联运行记录与研究任务
    request_id = f"sched_run_{run.id}"

    # 复用 ResearchService.run 执行短链路研究
    service = ResearchService()
    result = await service.run(
        ctx=ctx,
        question=question,
        output_type=output_type,
        strategy=strategy,
        knowledge_base_ids=kb_uuids,
        tool_codes=tool_codes,
        tool_inputs={},  # 调度场景不保留 tool_inputs，工具调用降级跳过
        context=config.get("context"),
        db=session,
        request_id=request_id,
    )

    # 返回摘要（完整结果已由 ResearchService 持久化到 ResearchTask/ResearchResult）
    return {
        "taskType": "RESEARCH_RUN",
        "researchTaskId": result.task_id,
        "requestId": request_id,
        "degraded": result.degraded,
        "degradedReasons": result.degraded_reasons,
        "confidence": result.confidence,
    }


# ---------------------------------------------------------------------------
# 辅助函数：状态标记与错误码映射
# ---------------------------------------------------------------------------
def _map_execution_error(exc: Exception) -> str:
    """将执行异常映射为错误码。

    Args:
        exc: 执行过程中抛出的异常。

    Returns:
        大写下划线错误码字符串。
    """
    # NotImplementedError 属于配置类错误（任务类型未实现），不重试
    if isinstance(exc, NotImplementedError):
        return ERROR_EXECUTION_FAILED
    # ValueError 通常为配置错误（缺少必填字段），不重试
    # 但统一归为 EXECUTION_FAILED，由 RETRYABLE_ERROR_CODES 判断是否重试
    # 注：当前 EXECUTION_FAILED 在可重试集合中，ValueError 也会被重试
    # 若需区分，可单独定义 CONFIG_ERROR 错误码
    return ERROR_EXECUTION_FAILED


async def _mark_failed(
    ctx: ProjectContext,
    session,  # AsyncSession
    run: ScheduleRun,
    error_code: str,
    error_message: str,
) -> None:
    """标记运行记录为 failed（带 project_id 过滤）。

    用于执行前校验失败（项目停用/任务停用/并发跳过）等场景。

    Args:
        ctx: 项目上下文（用于 project_id 过滤）。
        session: 异步数据库会话。
        run: 运行记录。
        error_code: 错误码。
        error_message: 错误信息。
    """
    try:
        run_repo = ScheduleRunRepository(session)
        await run_repo.update(
            ctx,
            run.id,
            status="failed",
            completed_at=datetime.now(timezone.utc),
            error_code=error_code,
            error_message=error_message,
        )
    except Exception:
        # 标记失败本身失败：仅记录日志，避免掩盖原始问题
        logger.exception(
            "标记运行记录 failed 失败 run_id=%s error_code=%s",
            run.id,
            error_code,
        )


async def _mark_failed_global(
    session,  # AsyncSession
    run: ScheduleRun,
    error_code: str,
    error_message: str,
) -> None:
    """标记运行记录为 failed（全局更新，无 project_id 过滤）。

    用于运行记录归属校验失败等无法构造合法 ctx 的场景，直接 UPDATE。

    Args:
        session: 异步数据库会话。
        run: 运行记录。
        error_code: 错误码。
        error_message: 错误信息。
    """
    try:
        from sqlalchemy import update as sa_update

        # 直接 UPDATE，不带 project_id 过滤（此场景下归属已不可信）
        stmt = (
            sa_update(ScheduleRun)
            .where(ScheduleRun.id == run.id)
            .values(
                status="failed",
                completed_at=datetime.now(timezone.utc),
                error_code=error_code,
                error_message=error_message,
            )
        )
        await session.execute(stmt)
    except Exception:
        logger.exception(
            "标记运行记录 failed 失败（全局）run_id=%s error_code=%s",
            run.id,
            error_code,
        )


async def _mark_timeout(run_id: str) -> None:
    """标记运行记录为 timeout（Celery 软超时触发）。

    ``SoftTimeLimitExceeded`` 抛出时，原事件循环已被 ``asyncio.run`` 关闭，
    需新建独立事件循环处理 timeout 标记。

    Args:
        run_id: 运行记录 ID。
    """
    from sqlalchemy import update as sa_update

    # 新建独立会话处理 timeout 标记
    async with AsyncSessionFactory() as session:
        try:
            # 全局 UPDATE：timeout 场景下 project_id 未知，直接按 run_id 更新
            stmt = (
                sa_update(ScheduleRun)
                .where(ScheduleRun.id == run_id)
                .values(
                    status="timeout",
                    completed_at=datetime.now(timezone.utc),
                    error_code=ERROR_TASK_TIMEOUT,
                )
            )
            await session.execute(stmt)
            await session.commit()
            logger.info("运行记录已标记为 timeout run_id=%s", run_id)
        except Exception:
            # 标记 timeout 失败：仅记录日志，由后续清理任务兜底
            logger.exception("标记运行记录 timeout 失败 run_id=%s", run_id)
