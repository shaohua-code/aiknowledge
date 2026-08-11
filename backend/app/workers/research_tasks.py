"""异步研究任务：在 Celery Worker 中执行短链路研究。

对应 SubTask 16.1：将同步短链路研究（``POST /research/run``）异步化，
通过 Celery 队列调度执行，适用于以下场景：

1. **客户端容忍延迟**：异步任务可在 30s 内完成，无需 HTTP 长连接保持
2. **削峰填谷**：高峰期任务进入队列排队，避免瞬时高并发拖垮模型与外部接口
3. **可重试**：Worker 崩溃或网络抖动可通过 Celery 重试机制自动恢复
4. **结果查询**：客户端通过 ``GET /research/jobs/{jobId}`` 轮询任务状态与结果

异步执行流程
------------
1. API 层 ``POST /research/jobs`` 创建 ResearchTask（status='pending'），
   通过 ``run_research_async.delay()`` 投递到 Celery 队列
2. Worker 领取任务后调用 ``_run_research_async``，在独立事件循环中执行：
   a. 二次校验项目归属（防止任务参数被篡改后越权）
   b. 更新 task status='running'，记录 started_at
   c. 构造 ProjectContext（Worker 场景仅含 project_id/project_code）
   d. 调用 ``ResearchService.run()`` 复用同步短链路逻辑
   e. 成功 → status='success' / 'partial_success'（degraded=true 时）
   f. 失败 → status='failed'，记录 error_code
   g. 超时 → status='timeout'（Celery soft_time_limit 触发）
3. 客户端通过 ``GET /research/jobs/{jobId}`` 轮询状态，成功后获取完整结果

为什么必须传 Idempotency-Key？
----------------------------
异步任务一旦入队就无法撤回，若客户端因网络抖动重试，会创建多个相同任务，
浪费模型调用配额与外部接口额度。``Idempotency-Key`` 在 API 层做幂等校验：
- 相同 Key + 相同内容 → 返回原 job（不重复入队）
- 相同 Key + 不同内容 → 抛 ``IdempotencyConflictError``（409）
- 不存在 → 创建新任务并写入幂等记录

任务参数说明
------------
``run_research_async`` 接受三个参数：
- ``project_id``：项目 ID，Worker 执行时再次校验归属
- ``project_code``：项目编码，用于构造 ProjectContext 与默认提示词模板
- ``task_id``：研究任务 ID（ResearchTask.id），用于查询与更新任务状态

状态机流转
----------
pending → running → success / partial_success / failed / timeout

- pending：任务已入队，等待 Worker 领取
- running：Worker 已领取并开始执行
- success：研究成功完成（无降级）
- partial_success：研究降级完成（如联网搜索超时，但仍有内部证据 + 模型生成）
- failed：研究失败（如证据不足、模型异常），errorCode 含错误码
- timeout：任务整体超时（Celery soft_time_limit 触发）

跨项目任务隔离
--------------
1. Worker 接收 ``project_id`` 参数后，构造 ProjectContext 时仅含该 project_id
2. 查询 ResearchTask 时通过 ``ResearchTaskRepository.get_by_id`` 强制带 project_id 过滤
3. 若 task 不属于传入的 project_id（参数被篡改），查询返回 None，任务直接结束
   避免越权处理其他项目任务
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from celery import Task
from celery.exceptions import SoftTimeLimitExceeded

from app.core.project_context import ProjectContext
from app.db.repositories.research import ResearchTaskRepository
from app.db.session import AsyncSessionFactory
from app.modules.research.service import ResearchService
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 错误码常量：统一标识失败原因，便于监控与客户端降级判断
# ---------------------------------------------------------------------------
# 任务不存在或项目归属不一致：参数被篡改或任务已被删除
ERROR_TASK_NOT_FOUND = "TASK_NOT_FOUND"
# 证据不足：三路取证均无成果，无法支撑模型生成
ERROR_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
# 研究执行异常：未预期的内部错误
ERROR_RESEARCH_FAILED = "RESEARCH_FAILED"
# 任务超时：超过 Celery soft_time_limit（100s）
ERROR_TASK_TIMEOUT = "TASK_TIMEOUT"


# ---------------------------------------------------------------------------
# 同步入口：Celery 任务
# ---------------------------------------------------------------------------
@celery_app.task(
    name="app.workers.research_tasks.run_research_async",
    queue="online",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
    time_limit=120,
    soft_time_limit=100,
)
def run_research_async(
    self: Task,
    project_id: str,
    project_code: str,
    task_id: str,
) -> None:
    """异步研究任务入口：在 Celery Worker 中执行短链路研究。

    使用 ``asyncio.run()`` 在 Celery 同步任务中运行异步逻辑：
        Celery Worker 是同步执行模型（基于 prefork/gevent），但数据库访问、
        httpx 请求、模型调用都是异步 API。``asyncio.run()`` 创建临时事件循环
        执行异步逻辑，结束后关闭循环。每次任务调用创建独立循环，
        避免跨任务状态污染。

    任务参数
    --------
    - ``project_id``：项目 ID，Worker 执行时再次校验归属，避免越权
    - ``project_code``：项目编码，用于构造 ProjectContext 与默认提示词模板
    - ``task_id``：研究任务 ID，用于查询与更新任务状态

    超时与重试策略
    --------------
    - ``soft_time_limit=100``：任务执行 100 秒后抛 ``SoftTimeLimitExceeded``，
      业务代码捕获后标记 task status='timeout'，优雅退出
    - ``time_limit=120``：硬超时 120 秒，超过将被 Worker 强制终止（SIGKILL），
      此时 task 状态可能停留在 running（由后续清理任务兜底）
    - ``max_retries=2``：可重试错误最多重试 2 次，间隔 30 秒
    - ``acks_late=True``：任务执行完成才 ACK，Worker 崩溃时任务会被重投

    Args:
        self: Celery Task 实例（``bind=True`` 注入），用于 ``self.retry()``。
        project_id: 项目 ID。
        project_code: 项目编码。
        task_id: 研究任务 ID。
    """
    try:
        # 在独立事件循环中执行异步研究逻辑
        asyncio.run(_run_research_async(self, project_id, project_code, task_id))
    except SoftTimeLimitExceeded:
        # 软超时：任务执行超过 100s，标记 timeout 并优雅退出
        # 此时事件循环已被 asyncio.run 关闭，需新建循环处理 timeout 标记
        logger.warning("研究任务 %s 执行超时（soft_time_limit=100s）", task_id)
        asyncio.run(_mark_timeout(project_id, project_code, task_id))
    except Exception:
        # 其他未预期异常：记录日志，不再重试（业务错误已在异步层标记 failed）
        # 重试仅对网络类瞬时错误有意义，业务错误重试无意义
        logger.exception("研究任务 %s 执行失败（不可重试）", task_id)


# ---------------------------------------------------------------------------
# 异步主逻辑
# ---------------------------------------------------------------------------
async def _run_research_async(
    task: Task,
    project_id: str,
    project_code: str,
    task_id: str,
) -> None:
    """异步研究主逻辑：复用 ResearchService.run 完成短链路研究。

    本函数在 ``asyncio.run()`` 中执行，所有数据库与网络操作均为异步。
    任一阶段失败时通过 ``_mark_failed`` 标记 ResearchTask 状态为 failed。

    状态机流转：pending → running → success / partial_success / failed

    Args:
        task: Celery Task 实例，用于重试计数判断。
        project_id: 项目 ID。
        project_code: 项目编码。
        task_id: 研究任务 ID。

    Raises:
        Exception: 未预期异常上抛，由 ``run_research_async`` 捕获并记录日志。
    """
    # 构造 Worker 场景的项目上下文
    # project_code 用于 ResearchService 内部的默认提示词模板选择（按项目场景）
    # api_key_id 留空：Worker 场景无 API Key 上下文，UsageLog 写入时由 Service 兜底
    ctx = _build_ctx(project_id, project_code)

    # AsyncSessionFactory 创建独立会话，任务结束自动关闭
    async with AsyncSessionFactory() as session:
        task_repo = ResearchTaskRepository(session)

        # ------------------------------------------------------------------
        # 阶段 1：二次校验项目归属
        # ------------------------------------------------------------------
        # 为什么 Worker 要再次校验？防止任务参数被篡改后越权处理其他项目任务
        # 查询时强制带 project_id 过滤，task 不属于该项目则返回 None
        research_task = await task_repo.get_by_id(ctx, task_id)
        if research_task is None:
            # 任务不存在或项目归属不一致：直接结束，不抛异常避免无意义重试
            logger.warning(
                "研究任务 %s 不存在或不属于项目 %s，跳过执行",
                task_id,
                project_id,
            )
            return

        # ------------------------------------------------------------------
        # 阶段 2：更新任务状态为 running，记录开始时间
        # ------------------------------------------------------------------
        # 从 ResearchTask 中恢复研究所需参数（question / strategy / 等），
        # 保证 Worker 执行的是任务入队时的快照，而非重新解析请求
        started_at = datetime.now(timezone.utc)
        await task_repo.update(
            ctx,
            task_id,
            status="running",
            started_at=started_at,
        )
        # 提交状态变更：保证客户端轮询时能立即看到 RUNNING 状态
        await session.commit()

        # ------------------------------------------------------------------
        # 阶段 3：复用同步短链路逻辑执行研究
        # ------------------------------------------------------------------
        # ResearchService.run 内部已处理：
        # - 并行取证（asyncio.gather 三路证据源）
        # - 证据合并（EvidenceMerger）
        # - 一次模型生成（asyncio.wait_for 超时 10s）
        # - 持久化（ResearchEvidence / ResearchResult / ResearchTask 更新）
        # - UsageLog 记录
        # 因此本函数无需重复处理结果持久化，仅需捕获异常做兜底标记
        service = ResearchService()
        try:
            # 将 knowledge_base_ids 从字符串列表转为 UUID 列表
            # ResearchTask.knowledge_base_ids 存储为字符串数组，Service 期望 UUID 列表
            from uuid import UUID
            kb_uuids: list[UUID] = []
            for kb_id_str in (research_task.knowledge_base_ids or []):
                kb_uuids.append(UUID(kb_id_str))

            # 调用 ResearchService.run 复用同步短链路逻辑
            # request_id 使用任务记录中的 request_id，保证 UsageLog 与 ResearchTask 关联
            result = await service.run(
                ctx=ctx,
                question=research_task.question,
                output_type=research_task.output_type,
                strategy=research_task.strategy,
                knowledge_base_ids=kb_uuids,
                tool_codes=research_task.requested_tools or [],
                tool_inputs={},  # 异步场景不保留 tool_inputs，工具调用降级跳过
                context=research_task.input_context,
                db=session,
                request_id=research_task.request_id,
            )

            # ResearchService.run 已完成状态更新（success / partial_success）
            # 此处仅记录日志，便于运维监控
            if result.degraded:
                logger.info(
                    "研究任务 %s 降级完成（reasons=%s）",
                    task_id,
                    result.degraded_reasons,
                )
            else:
                logger.info("研究任务 %s 成功完成", task_id)

        except Exception as exc:
            # 研究执行异常：标记 task failed 并记录 error_code
            # 异常可能是 InsufficientEvidenceError / ModelTimeoutError / 其他未预期错误
            # ResearchService 内部已对可降级场景做兜底，此处异常均为不可降级错误
            logger.exception("研究任务 %s 执行异常：%s", task_id, exc)

            # 根据异常类型映射错误码，便于客户端区分失败原因
            error_code = _map_error_code(exc)
            await _mark_failed(ctx, task_repo, task_id, error_code, str(exc))


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
def _build_ctx(project_id: str, project_code: str) -> ProjectContext:
    """构造 Worker 场景的项目上下文。

    Worker 无 API 鉴权链路，手动构造 ProjectContext。``project_code`` 用于
    ResearchService 内部的默认提示词模板选择（按项目场景匹配）。

    Args:
        project_id: 项目 ID。
        project_code: 项目编码。

    Returns:
        ProjectContext 实例，environment='worker' 标识 Worker 场景。
    """
    return ProjectContext(
        project_id=project_id,
        project_code=project_code,
        environment="worker",
    )


def _map_error_code(exc: Exception) -> str:
    """将异常类型映射为错误码，便于客户端区分失败原因。

    Args:
        exc: 研究执行过程中抛出的异常。

    Returns:
        大写下划线错误码字符串。
    """
    # 延迟导入避免循环依赖
    from app.core.exceptions import InsufficientEvidenceError

    # 证据不足：三路取证均无成果
    if isinstance(exc, InsufficientEvidenceError):
        return ERROR_INSUFFICIENT_EVIDENCE
    # 其他未预期异常：统一标记为研究失败
    return ERROR_RESEARCH_FAILED


async def _mark_failed(
    ctx: ProjectContext,
    task_repo: ResearchTaskRepository,
    task_id: str,
    error_code: str,
    error_message: str,
) -> None:
    """标记研究任务为 failed，记录错误码与完成时间。

    在研究执行异常时调用，保证任务状态最终一致：
    - status='failed'
    - completed_at=当前时间
    - error_code=错误码

    Args:
        ctx: 项目上下文。
        task_repo: ResearchTaskRepository 实例。
        task_id: 研究任务 ID。
        error_code: 错误码（大写下划线）。
        error_message: 错误消息（仅用于日志，不持久化）。
    """
    try:
        await task_repo.update(
            ctx,
            task_id,
            status="failed",
            completed_at=datetime.now(timezone.utc),
            error_code=error_code,
        )
        # 提交事务：保证 failed 状态对客户端可见
        # 此处独立提交，避免上层 session 已关闭导致状态丢失
        await task_repo.session.commit()
    except Exception:
        # 标记失败本身失败：仅记录日志，避免掩盖原始异常
        logger.exception(
            "标记研究任务 %s 为 failed 失败（error_code=%s）",
            task_id,
            error_code,
        )


async def _mark_timeout(
    project_id: str,
    project_code: str,
    task_id: str,
) -> None:
    """标记研究任务为 timeout（Celery 软超时触发）。

    ``SoftTimeLimitExceeded`` 抛出时，原事件循环已被 ``asyncio.run`` 关闭，
    需新建独立事件循环处理 timeout 标记。本函数由 ``run_research_async``
    通过 ``asyncio.run(_mark_timeout(...))`` 调用。

    Args:
        project_id: 项目 ID。
        project_code: 项目编码。
        task_id: 研究任务 ID。
    """
    ctx = _build_ctx(project_id, project_code)
    # 新建独立会话处理 timeout 标记，避免原会话状态污染
    async with AsyncSessionFactory() as session:
        task_repo = ResearchTaskRepository(session)
        try:
            await task_repo.update(
                ctx,
                task_id,
                status="timeout",
                completed_at=datetime.now(timezone.utc),
                error_code=ERROR_TASK_TIMEOUT,
            )
            await session.commit()
            logger.info("研究任务 %s 已标记为 timeout", task_id)
        except Exception:
            # 标记 timeout 失败：仅记录日志，由后续清理任务兜底
            logger.exception(
                "标记研究任务 %s 为 timeout 失败",
                task_id,
            )
