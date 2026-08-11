"""网页采集任务：URL 发现 → SSRF 校验 → 下载 → 清洗 → 去重 → 入库策略执行。

对应 Task 19.3 ~ 19.7。

任务签名说明
------------
``run_crawl`` 接受四个参数：
- ``project_id``：项目 ID，Worker 执行时再次校验归属
- ``project_code``：项目编码（与 API 链路的 X-Project-Code 一致，便于日志关联）
- ``crawl_source_id``：采集源 ID
- ``run_id``：运行记录 ID（已由 API 层创建，初始 status='pending'）

整体流程
--------
1. **二次校验项目归属**：查询项目状态与 CrawlSource 状态，防止状态变更后越权采集
2. **构造 ProjectContext**：Worker 场景无 API 鉴权，手动构造只含 project_id 的上下文
3. **调用 CrawlerService.run_crawl**：执行完整采集流程
4. **失败重试**：网络类错误重试，业务错误不重试

错误处理与重试策略
------------------
- **网络类错误**（httpx.ConnectError / TimeoutException / DNS 解析失败）：
  抛出 ``_RetryCrawlError``，由同步入口捕获后调用 ``self.retry()``，
  最多重试 2 次，间隔 60 秒。
- **业务错误**（采集源不存在/已暂停/配置无效）：
  标记 CrawlRun.status='failed' + error_code，不重试。
- **项目归属不一致**：error_code='PROJECT_SCOPE_MISMATCH'。
"""
from __future__ import annotations

import asyncio
import logging

from celery import Task

from app.core.project_context import ProjectContext
from app.db.repositories.crawler import CrawlRunRepository, CrawlSourceRepository
from app.db.session import AsyncSessionFactory
from app.modules.crawler.service import CrawlerService
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 错误码常量：统一标识失败原因，便于监控与降级判断
# ---------------------------------------------------------------------------
# 项目归属不一致：CrawlSource 不属于传入的 project_id
ERROR_PROJECT_SCOPE_MISMATCH = "PROJECT_SCOPE_MISMATCH"
# 采集源不存在：CrawlSource 查询返回 None（可能已被删除）
ERROR_CRAWL_SOURCE_NOT_FOUND = "CRAWL_SOURCE_NOT_FOUND"
# 项目已停用：Project.status != 'active'
ERROR_PROJECT_DISABLED = "PROJECT_DISABLED"
# 采集源已停用：CrawlSource.status != 'active'
ERROR_CRAWL_SOURCE_NOT_ACTIVE = "CRAWL_SOURCE_NOT_ACTIVE"


class _RetryCrawlError(Exception):
    """可重试错误包装异常。

    异步逻辑 ``_run_crawl_async`` 捕获网络类错误后抛出本异常，
    同步入口 ``run_crawl`` 捕获后调用 ``self.retry(exc=...)`` 触发 Celery 重试。

    为什么需要包装？
        ``self.retry`` 是 Celery Task 同步方法，不能在 asyncio 事件循环内直接调用
        （会阻塞事件循环）。通过包装异常，将重试决策上抛到同步层处理。
    """


# ---------------------------------------------------------------------------
# 同步入口：Celery 任务
# ---------------------------------------------------------------------------
@celery_app.task(
    name="app.workers.crawl_tasks.run_crawl",
    queue="crawler",
    bind=True,
    acks_late=True,
    time_limit=1800,  # 硬超时：30 分钟（采集任务长耗时）
    soft_time_limit=1500,  # 软超时：25 分钟（预留 5 分钟清理）
    max_retries=2,  # 最多重试 2 次
    default_retry_delay=60,  # 重试间隔 60 秒
)
def run_crawl(
    self: Task,
    project_id: str,
    project_code: str,
    crawl_source_id: str,
    run_id: str,
) -> None:
    """网页采集主任务：URL 发现 → SSRF 校验 → 下载 → 清洗 → 去重 → 入库。

    使用 ``asyncio.run()`` 在 Celery 同步任务中运行异步逻辑：
        Celery Worker 是同步执行模型，但数据库访问、httpx 请求都是异步 API。
        ``asyncio.run()`` 创建临时事件循环执行异步逻辑，结束后关闭循环。

    任务超时设计
    ------------
    - ``time_limit=1800``（30 分钟硬超时）：超过将被 Worker 强制终止（SIGKILL）。
      采集任务可能抓取数百个页面，每个页面 10s 超时，30 分钟足够覆盖。
    - ``soft_time_limit=1500``（25 分钟软超时）：超过将抛 SoftTimeLimitExceeded，
      业务代码可捕获做清理（标记 CrawlRun.status='failed'），优雅退出。
    - ``max_retries=2``：网络类错误最多重试 2 次（共 3 次执行机会）。
    - ``default_retry_delay=60``：重试间隔 60 秒，避免立即重试冲击目标站点。

    Args:
        self: Celery Task 实例（``bind=True`` 注入），用于 ``self.retry()``。
        project_id: 项目 ID，Worker 执行时再次校验归属。
        project_code: 项目编码，用于日志关联与 ProjectContext 构造。
        crawl_source_id: 采集源 ID。
        run_id: 运行记录 ID（已由 API 层创建，初始 status='pending'）。

    重试策略：
        - 网络类错误（连接失败/超时）：``self.retry()`` 最多 2 次，间隔 60 秒。
        - 业务错误（采集源不存在/已暂停/项目停用/配置无效）：标记 failed，不重试。
    """
    try:
        # 在独立事件循环中执行异步采集逻辑
        asyncio.run(
            _run_crawl_async(self, project_id, project_code, crawl_source_id, run_id)
        )
    except _RetryCrawlError as exc:
        # 可重试错误：交由 Celery 调度重试
        # exc=__cause__ 保留原始异常栈，便于排查
        logger.warning(
            "采集任务 run_id=%s 遇到可重试错误，准备重试（第 %d/%d 次）：%s",
            run_id,
            self.request.retries + 1,
            self.max_retries,
            exc.__cause__ or exc,
        )
        raise self.retry(exc=exc.__cause__ or exc)
    except Exception:
        # 其他未预期异常：记录日志，不再重试（业务错误已在异步层标记 failed）
        logger.exception("采集任务 run_id=%s 失败（不可重试）", run_id)


# ---------------------------------------------------------------------------
# 异步主逻辑
# ---------------------------------------------------------------------------
async def _run_crawl_async(
    task: Task,
    project_id: str,
    project_code: str,
    crawl_source_id: str,
    run_id: str,
) -> None:
    """采集任务异步主逻辑：串联校验、采集、入库各阶段。

    本函数在 ``asyncio.run()`` 中执行，所有数据库与网络操作均为异步。
    业务错误（采集源不存在/已暂停）通过标记 CrawlRun.status='failed' 处理；
    网络错误抛 ``_RetryCrawlError`` 触发重试。

    Args:
        task: Celery Task 实例，用于重试计数判断。
        project_id: 项目 ID。
        project_code: 项目编码。
        crawl_source_id: 采集源 ID。
        run_id: 运行记录 ID。

    Raises:
        _RetryCrawlError: 网络类错误，需上层重试。
    """
    # 构造 Worker 场景的项目上下文：含 project_id 与 project_code
    # Repository 仅依赖 ctx.project_id 做项目过滤，project_code 用于日志关联
    ctx = _build_ctx(project_id, project_code)

    # AsyncSessionFactory 创建独立会话，任务结束自动关闭
    async with AsyncSessionFactory() as session:
        # 初始化各 Repository：共享同一会话，保证事务一致性
        source_repo = CrawlSourceRepository(session)
        run_repo = CrawlRunRepository(session)

        try:
            # ----------------------------------------------------------
            # 阶段 1：二次校验项目归属与状态
            # ----------------------------------------------------------
            # 为什么 Worker 要再次校验？
            #   防止调度器或任务参数被篡改后越权采集其他项目数据。
            #   API 层创建 CrawlRun 时已校验，但任务入队到执行存在时间差，
            #   期间项目可能被停用、采集源可能被删除或暂停。
            try:
                await _verify_project_scope(
                    ctx, source_repo, project_id, crawl_source_id, run_id
                )
            except ValueError as exc:
                # 业务错误：项目/采集源状态不符，标记 failed 不重试
                error_code = str(exc)
                await _mark_run_failed(ctx, run_repo, run_id, error_code)
                await session.commit()
                logger.warning(
                    "采集任务 run_id=%s 校验失败：%s",
                    run_id,
                    error_code,
                )
                return  # 不抛异常，不触发重试

            # ----------------------------------------------------------
            # 阶段 2：调用 CrawlerService 执行采集
            # ----------------------------------------------------------
            service = CrawlerService()
            try:
                result = await service.run_crawl(
                    ctx=ctx,
                    crawl_source_id=crawl_source_id,
                    run_id=run_id,
                    db=session,
                )
                logger.info(
                    "采集任务完成 run_id=%s source_id=%s status=%s "
                    "discovered=%d success=%d duplicate=%d failed=%d imported=%d",
                    result.run_id,
                    result.source_id,
                    result.status,
                    result.discovered_count,
                    result.success_count,
                    result.duplicate_count,
                    result.failed_count,
                    result.imported_count,
                )
            except Exception as exc:
                # 采集过程中的异常：标记运行失败
                # 区分网络类错误（可重试）与业务错误（不重试）
                if _is_network_error(exc):
                    # 网络错误：抛 _RetryCrawlError 触发重试
                    logger.warning(
                        "采集任务 run_id=%s 网络错误，将重试：%s",
                        run_id,
                        exc,
                    )
                    # 标记运行失败（重试时创建新 run_id 或复用由业务决定，此处仅记录）
                    await _mark_run_failed(ctx, run_repo, run_id, "NETWORK_ERROR")
                    await session.commit()
                    raise _RetryCrawlError() from exc
                else:
                    # 业务错误：标记 failed 不重试
                    logger.exception(
                        "采集任务 run_id=%s 业务错误，不重试",
                        run_id,
                    )
                    await _mark_run_failed(
                        ctx, run_repo, run_id, "CRAWL_BUSINESS_ERROR"
                    )
                    await session.commit()
        except _RetryCrawlError:
            # 重新抛出到同步层处理
            raise
        except Exception:
            # 兜底：未预期异常标记 failed
            logger.exception(
                "采集任务 run_id=%s 未预期异常",
                run_id,
            )
            try:
                await _mark_run_failed(ctx, run_repo, run_id, "INTERNAL_ERROR")
                await session.commit()
            except Exception:
                # 标记失败也失败：仅记录日志
                logger.exception(
                    "采集任务 run_id=%s 标记失败时也异常",
                    run_id,
                )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
async def _verify_project_scope(
    ctx: ProjectContext,
    source_repo: CrawlSourceRepository,
    project_id: str,
    crawl_source_id: str,
    run_id: str,
) -> None:
    """二次校验项目归属与采集源状态。

    校验内容：
        1. CrawlSource 存在且属于传入的 project_id
        2. CrawlSource.status == 'active'（仅 active 状态可执行采集）

    为什么需要二次校验？
        API 层创建 CrawlRun 时已校验，但任务入队到执行存在时间差（可能数分钟），
        期间采集源可能被删除或暂停。Worker 必须再次校验，避免对已停用的采集源
        执行采集。

    Args:
        ctx: 项目上下文。
        source_repo: 采集源 Repository。
        project_id: 项目 ID。
        crawl_source_id: 采集源 ID。
        run_id: 运行记录 ID（仅用于日志）。

    Raises:
        ValueError: 校验失败，错误信息为错误码字符串。
    """
    # 查询采集源（带 project_id 过滤）
    source = await source_repo.get_by_id(ctx, crawl_source_id)
    if source is None:
        # 采集源不存在或不属于当前项目
        logger.warning(
            "采集任务 run_id=%s 采集源 %s 不存在或不属于项目 %s",
            run_id,
            crawl_source_id,
            project_id,
        )
        raise ValueError(ERROR_CRAWL_SOURCE_NOT_FOUND)

    # 校验采集源状态：仅 active 状态可执行采集
    if source.status != "active":
        logger.warning(
            "采集任务 run_id=%s 采集源 %s 状态为 %s，非 active",
            run_id,
            crawl_source_id,
            source.status,
        )
        raise ValueError(ERROR_CRAWL_SOURCE_NOT_ACTIVE)


def _is_network_error(exc: Exception) -> bool:
    """判断异常是否为网络类错误（可重试）。

    网络类错误特征：
        - 连接失败（ConnectionError）
        - 超时（TimeoutException）
        - DNS 解析失败（socket.gaierror）

    Args:
        exc: 异常实例。

    Returns:
        True 表示网络错误，可重试；False 表示业务错误，不重试。
    """
    import httpx
    import socket

    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    if isinstance(exc, socket.gaierror):
        # DNS 解析失败
        return True
    if isinstance(exc, OSError) and "network" in str(exc).lower():
        # 网络不可达等 OS 错误
        return True
    return False


async def _mark_run_failed(
    ctx: ProjectContext,
    run_repo: CrawlRunRepository,
    run_id: str,
    error_code: str,
) -> None:
    """标记采集运行记录为 failed。

    Args:
        ctx: 项目上下文。
        run_repo: 运行记录 Repository。
        run_id: 运行记录 ID。
        error_code: 错误码。
    """
    from datetime import datetime, timezone

    await run_repo.update(
        ctx,
        run_id,
        status="failed",
        error_code=error_code,
        completed_at=datetime.now(timezone.utc),
    )


def _build_ctx(project_id: str, project_code: str) -> ProjectContext:
    """构造 Worker 场景的项目上下文。

    Worker 无 API 鉴权链路，手动构造含 ``project_id`` 与 ``project_code`` 的
    ProjectContext。Repository 仅依赖 project_id 做项目过滤，
    project_code 用于日志关联与 ProjectContext 完整性。

    Args:
        project_id: 项目 ID。
        project_code: 项目编码。

    Returns:
        ProjectContext 实例。
    """
    return ProjectContext(
        project_id=project_id,
        project_code=project_code,
        environment="worker",
    )
