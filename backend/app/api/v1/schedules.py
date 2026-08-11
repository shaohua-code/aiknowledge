"""定时任务调度管理接口（Task 18.1）。

对应 Task 18：/api/v1/schedules 及其暂停/恢复/运行子接口，
以及 /api/v1/schedule-runs/{runId} 运行记录详情接口。

设计要点（务必阅读）
--------------------
1. **跨项目隔离**
   所有查询通过 ``ScheduleRepository`` / ``ScheduleRunRepository`` 强制带
   ``project_id`` 过滤，跨项目查询返回 None，由端点统一抛 ``TaskNotFoundError``
   （404，不泄露资源是否存在）。

2. **Cron + 时区处理**
   用户配置的 cron 表达式是"本地时间语义"（如 ``0 9 * * 1-5`` 表示工作日 9 点），
   服务端按 ``timezone`` 转换为 UTC 后存储 ``next_run_at``。
   创建、编辑（改 cron/timezone）、恢复时重算 ``next_run_at``。

3. **taskType 大小写约定**
   - 对外 API：大写形式（CRAWL_SOURCE / TOOL_SYNC / ...）
   - 数据库存储：小写形式（crawl_source / tool_sync / ...）
   - 端点在写入前转小写，响应时转大写，保持对外契约一致

4. **手动触发（run 接口）**
   创建 ScheduleRun（planned_at=当前 UTC 时间），投递 ``execute_schedule_task``
   到 maintenance 队列。手动触发不受 cron 调度控制，立即执行一次。

5. **删除策略**
   仅允许删除 ``enabled=false`` 的任务（避免误删正在调度的任务），
   已停用的任务删除后历史运行记录保留（审计追溯）。

6. **Scope 校验**
   - 读操作（GET）：``schedules:read``
   - 写操作（POST/PATCH/DELETE/pause/resume/run）：``schedules:write``
   运行记录详情接口（GET /schedule-runs/{runId}）仅需 ``schedules:read``。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_scopes
from app.api.v1.schemas import (
    ScheduleCreateRequest,
    ScheduleUpdateRequest,
)
from app.core.exceptions import TaskNotFoundError, ValidationError
from app.core.project_context import ProjectContext
from app.core.response import ApiResponse, build_meta
from app.core.scopes import SCOPE_SCHEDULES_READ, SCOPE_SCHEDULES_WRITE
from app.db.repositories.schedule import (
    ScheduleRepository,
    ScheduleRunRepository,
)
from app.db.session import get_db
from app.modules.scheduler.cron_utils import compute_next_run, parse_cron

# 主路由：/api/v1/schedules
router = APIRouter(prefix="/schedules", tags=["调度"])

# 运行记录详情路由：/api/v1/schedule-runs/{runId}
# 单独注册到 api_router，不在 /schedules 前缀下
schedule_runs_router = APIRouter(prefix="/schedule-runs", tags=["调度"])


# ---------------------------------------------------------------------------
# 辅助函数：ORM 实例转响应字典（字段名转 camelCase，状态转大写）
# ---------------------------------------------------------------------------
def _schedule_to_dict(schedule) -> dict:
    """将 Schedule ORM 实例转为响应字典。

    字段命名转 camelCase（与前端约定一致），``task_type`` / ``concurrency_policy``
    保持原值（task_type 对外大写由写入时的小写→响应时的转换保证；
    此处直接取数据库存储值并转大写）。

    Args:
        schedule: Schedule ORM 实例。

    Returns:
        响应字典，字段命名 camelCase。
    """
    return {
        "id": schedule.id,
        "name": schedule.name,
        # task_type 数据库存储为小写，对外响应转大写
        "taskType": schedule.task_type.upper(),
        "cronExpression": schedule.cron_expression,
        "timezone": schedule.timezone,
        "config": schedule.config,
        "concurrencyPolicy": schedule.concurrency_policy,
        "timeoutSeconds": schedule.timeout_seconds,
        "maxRetries": schedule.max_retries,
        "enabled": schedule.enabled,
        "nextRunAt": schedule.next_run_at.isoformat() if schedule.next_run_at else None,
        "lastRunAt": schedule.last_run_at.isoformat() if schedule.last_run_at else None,
        "createdAt": schedule.created_at.isoformat() if schedule.created_at else None,
        "updatedAt": schedule.updated_at.isoformat() if schedule.updated_at else None,
    }


def _run_to_dict(run) -> dict:
    """将 ScheduleRun ORM 实例转为响应字典。

    ``status`` 数据库存储为小写，对外响应转大写。

    Args:
        run: ScheduleRun ORM 实例。

    Returns:
        响应字典，字段命名 camelCase。
    """
    return {
        "id": run.id,
        "scheduleId": run.schedule_id,
        "plannedAt": run.planned_at.isoformat() if run.planned_at else None,
        "startedAt": run.started_at.isoformat() if run.started_at else None,
        "completedAt": run.completed_at.isoformat() if run.completed_at else None,
        # status 数据库存储为小写，对外响应转大写
        "status": run.status.upper(),
        "attempt": run.attempt,
        "queueJobId": run.queue_job_id,
        "resultSummary": run.result_summary,
        "errorCode": run.error_code,
        "errorMessage": run.error_message,
        "createdAt": run.created_at.isoformat() if run.created_at else None,
    }


# ============================================================================
# 定时任务 CRUD 接口
# ============================================================================
@router.post("")
async def create_schedule(
    payload: ScheduleCreateRequest,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_SCHEDULES_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """创建定时任务（``POST /api/v1/schedules``）。

    流程
    ----
    1. 校验 cron 表达式格式（5 段：分 时 日 月 周）
    2. 试算 next_run_at（cron + timezone → UTC），校验时区有效性
    3. 写入 schedules 表，task_type 转小写存储
    4. 返回完整 Schedule

    Args:
        payload: 请求体，含 name / taskType / cronExpression / timezone / config 等。
        ctx: 项目上下文，由 ``require_scopes(SCOPE_SCHEDULES_WRITE)`` 注入。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含新建的 Schedule 完整信息。

    Raises:
        ValidationError: cron 表达式格式非法，或时区名称无效，或 next_run_at 无法计算。
    """
    # ------------------------------------------------------------------
    # 步骤 1：校验 cron 表达式格式
    # ------------------------------------------------------------------
    # parse_cron 返回 False 时表示格式非法，提前拦截避免写入无效表达式
    if not parse_cron(payload.cronExpression):
        raise ValidationError(
            f"非法的 cron 表达式: {payload.cronExpression}（需 5 段：分 时 日 月 周）",
            details={"field": "cronExpression", "value": payload.cronExpression},
        )

    # ------------------------------------------------------------------
    # 步骤 2：试算 next_run_at，顺带校验时区有效性
    # ------------------------------------------------------------------
    # compute_next_run 在时区无效时会抛 ValueError，这里捕获转为 ValidationError
    # 表达式非法也会抛 ValueError（parse_cron 已校验，此处为双保险）
    try:
        next_run_at = compute_next_run(payload.cronExpression, payload.timezone)
    except ValueError as exc:
        raise ValidationError(
            f"计算下次执行时间失败: {exc}",
            details={"cronExpression": payload.cronExpression, "timezone": payload.timezone},
        ) from exc

    # ------------------------------------------------------------------
    # 步骤 3：写入 schedules 表
    # ------------------------------------------------------------------
    # task_type 转小写存储（数据库约定小写，对外响应时转大写）
    repo = ScheduleRepository(db)
    schedule = await repo.create(
        ctx,
        name=payload.name,
        task_type=payload.taskType.lower(),  # 大写枚举 → 小写存储
        cron_expression=payload.cronExpression,
        timezone=payload.timezone,
        config=payload.config,
        concurrency_policy=payload.concurrencyPolicy,
        timeout_seconds=payload.timeoutSeconds,
        max_retries=payload.maxRetries,
        enabled=True,  # 新建默认启用
        next_run_at=next_run_at,  # 服务端计算的下次执行时间（UTC）
    )
    # 提交事务：保证 schedule 持久化，Celery Beat 可扫描到
    await db.commit()

    # ------------------------------------------------------------------
    # 步骤 4：返回完整 Schedule
    # ------------------------------------------------------------------
    return ApiResponse.success(
        data=_schedule_to_dict(schedule),
        meta=build_meta(ctx.project_code),
    )


@router.get("")
async def list_schedules(
    ctx: ProjectContext = Depends(require_scopes(SCOPE_SCHEDULES_READ)),
    enabled: bool | None = Query(default=None, description="按启用状态过滤，可空"),
    taskType: str | None = Query(default=None, description="按任务类型过滤（大写形式），可空"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """列出当前项目定时任务（``GET /api/v1/schedules``）。

    支持按 ``enabled`` 与 ``taskType`` 过滤。``taskType`` 参数为大写形式
    （与对外枚举一致），查询时转小写匹配数据库存储值。

    Args:
        ctx: 项目上下文。
        enabled: 按启用状态过滤，可空。True 仅返回启用的任务。
        taskType: 按任务类型过滤（大写形式），可空。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含：
        - ``items``：定时任务列表
        - ``total``：符合条件的任务总数
    """
    repo = ScheduleRepository(db)
    # 查询全部任务（含已停用），后续按 enabled/taskType 在内存过滤
    # ScheduleRepository.list 已带 project_id 过滤
    all_items = await repo.list(ctx)

    # 内存过滤：按 enabled 过滤
    if enabled is not None:
        all_items = [s for s in all_items if s.enabled == enabled]

    # 内存过滤：按 taskType 过滤（大写参数 → 小写比较）
    if taskType is not None:
        task_type_lower = taskType.lower()
        all_items = [s for s in all_items if s.task_type == task_type_lower]

    # 构造响应列表
    items = [_schedule_to_dict(s) for s in all_items]
    data = {
        "items": items,
        "total": len(items),
    }

    return ApiResponse.success(
        data=data,
        meta=build_meta(ctx.project_code),
    )


@router.get("/{schedule_id}")
async def get_schedule(
    schedule_id: str,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_SCHEDULES_READ)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取定时任务详情（``GET /api/v1/schedules/{scheduleId}``）。

    跨项目隔离
    ----------
    查询时强制带 project_id 过滤，跨项目查询返回 None，统一抛
    ``TaskNotFoundError``（404），不泄露资源是否存在。

    Args:
        schedule_id: 定时任务 ID（路径参数）。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含完整 Schedule 信息。

    Raises:
        TaskNotFoundError: 任务不存在或不属于当前项目（404）。
    """
    repo = ScheduleRepository(db)
    # 查询时强制带 project_id 过滤
    schedule = await repo.get_by_id(ctx, schedule_id)
    if schedule is None:
        # 跨项目查询统一返回 404，不泄露任务是否存在
        raise TaskNotFoundError(
            f"定时任务 {schedule_id} 不存在",
            details={"scheduleId": schedule_id},
        )

    return ApiResponse.success(
        data=_schedule_to_dict(schedule),
        meta=build_meta(ctx.project_code),
    )


@router.patch("/{schedule_id}")
async def update_schedule(
    schedule_id: str,
    payload: ScheduleUpdateRequest,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_SCHEDULES_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """编辑定时任务（``PATCH /api/v1/schedules/{scheduleId}``）。

    仅允许修改 name / cronExpression / timezone / config / concurrencyPolicy /
    timeoutSeconds / maxRetries。``taskType`` 创建后不可改。

    改 ``cronExpression`` 或 ``timezone`` 时，服务端重算 ``next_run_at``，
    保证下次触发点与新表达式一致。

    Args:
        schedule_id: 定时任务 ID。
        payload: 编辑请求体，全部字段可选（PATCH 语义）。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含更新后的 Schedule。

    Raises:
        TaskNotFoundError: 任务不存在或不属于当前项目。
        ValidationError: cron 表达式非法或时区无效。
    """
    repo = ScheduleRepository(db)
    # 先查询任务（带 project_id 过滤），不存在则 404
    schedule = await repo.get_by_id(ctx, schedule_id)
    if schedule is None:
        raise TaskNotFoundError(
            f"定时任务 {schedule_id} 不存在",
            details={"scheduleId": schedule_id},
        )

    # ------------------------------------------------------------------
    # 构造更新字段字典，仅包含传入的字段（PATCH 语义）
    # ------------------------------------------------------------------
    update_fields: dict = {}

    if payload.name is not None:
        update_fields["name"] = payload.name

    # cron / timezone 变更需重算 next_run_at，统一处理
    new_cron = payload.cronExpression if payload.cronExpression is not None else schedule.cron_expression
    new_tz = payload.timezone if payload.timezone is not None else schedule.timezone

    if payload.cronExpression is not None:
        # 校验新 cron 表达式格式
        if not parse_cron(payload.cronExpression):
            raise ValidationError(
                f"非法的 cron 表达式: {payload.cronExpression}",
                details={"field": "cronExpression", "value": payload.cronExpression},
            )
        update_fields["cron_expression"] = payload.cronExpression

    if payload.timezone is not None:
        update_fields["timezone"] = payload.timezone

    # cron 或 timezone 任一变更 → 重算 next_run_at
    if payload.cronExpression is not None or payload.timezone is not None:
        try:
            new_next_run = compute_next_run(new_cron, new_tz)
        except ValueError as exc:
            raise ValidationError(
                f"计算下次执行时间失败: {exc}",
                details={"cronExpression": new_cron, "timezone": new_tz},
            ) from exc
        update_fields["next_run_at"] = new_next_run

    if payload.config is not None:
        update_fields["config"] = payload.config

    if payload.concurrencyPolicy is not None:
        update_fields["concurrency_policy"] = payload.concurrencyPolicy

    if payload.timeoutSeconds is not None:
        update_fields["timeout_seconds"] = payload.timeoutSeconds

    if payload.maxRetries is not None:
        update_fields["max_retries"] = payload.maxRetries

    # ------------------------------------------------------------------
    # 执行更新（update 方法带 project_id 过滤，双重保险）
    # ------------------------------------------------------------------
    if update_fields:
        updated = await repo.update(ctx, schedule_id, **update_fields)
        await db.commit()
        schedule = updated if updated is not None else schedule

    return ApiResponse.success(
        data=_schedule_to_dict(schedule),
        meta=build_meta(ctx.project_code),
    )


@router.delete("/{schedule_id}")
async def delete_schedule(
    schedule_id: str,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_SCHEDULES_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """删除定时任务（``DELETE /api/v1/schedules/{scheduleId}``）。

    仅允许删除 ``enabled=false`` 的任务（避免误删正在调度的任务）。
    删除后历史运行记录保留（审计追溯）。

    Args:
        schedule_id: 定时任务 ID。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含 ``deleted: true``。

    Raises:
        TaskNotFoundError: 任务不存在或不属于当前项目。
        ValidationError: 任务仍处于启用状态，需先暂停再删除。
    """
    repo = ScheduleRepository(db)
    schedule = await repo.get_by_id(ctx, schedule_id)
    if schedule is None:
        raise TaskNotFoundError(
            f"定时任务 {schedule_id} 不存在",
            details={"scheduleId": schedule_id},
        )

    # 仅允许删除已停用的任务，避免误删正在调度的任务
    if schedule.enabled:
        raise ValidationError(
            "任务仍处于启用状态，请先暂停（POST /schedules/{id}/pause）再删除",
            details={"scheduleId": schedule_id, "enabled": True},
        )

    # 执行删除（带 project_id 过滤）
    deleted = await repo.delete(ctx, schedule_id)
    await db.commit()

    return ApiResponse.success(
        data={"deleted": deleted, "scheduleId": schedule_id},
        meta=build_meta(ctx.project_code),
    )


# ============================================================================
# 暂停 / 恢复 / 手动触发接口
# ============================================================================
@router.post("/{schedule_id}/pause")
async def pause_schedule(
    schedule_id: str,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_SCHEDULES_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """暂停定时任务（``POST /api/v1/schedules/{scheduleId}/pause``）。

    将 ``enabled`` 置为 false，Celery Beat 不再扫描此任务。
    ``next_run_at`` 保留不动，恢复时重算。

    Args:
        schedule_id: 定时任务 ID。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含更新后的 Schedule。

    Raises:
        TaskNotFoundError: 任务不存在或不属于当前项目。
    """
    repo = ScheduleRepository(db)
    schedule = await repo.get_by_id(ctx, schedule_id)
    if schedule is None:
        raise TaskNotFoundError(
            f"定时任务 {schedule_id} 不存在",
            details={"scheduleId": schedule_id},
        )

    # set_enabled 仅修改 enabled 字段（带 project_id 过滤）
    updated = await repo.set_enabled(ctx, schedule_id, False)
    await db.commit()
    schedule = updated if updated is not None else schedule

    return ApiResponse.success(
        data=_schedule_to_dict(schedule),
        meta=build_meta(ctx.project_code),
    )


@router.post("/{schedule_id}/resume")
async def resume_schedule(
    schedule_id: str,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_SCHEDULES_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """恢复定时任务（``POST /api/v1/schedules/{scheduleId}/resume``）。

    将 ``enabled`` 置为 true，并重算 ``next_run_at``（以当前时间为基准），
    保证恢复后立即进入调度。

    为什么要重算 next_run_at？
        暂停期间 cron 触发点已过，若不重算，next_run_at 可能是过去的时间，
        恢复后立即被 Beat 扫描到期，造成"恢复即触发"的非预期行为。
        重算保证 next_run_at 是未来时间，按正常 cron 周期触发。

    Args:
        schedule_id: 定时任务 ID。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含更新后的 Schedule。

    Raises:
        TaskNotFoundError: 任务不存在或不属于当前项目。
        ValidationError: cron 表达式或时区无效（防御性校验）。
    """
    repo = ScheduleRepository(db)
    schedule = await repo.get_by_id(ctx, schedule_id)
    if schedule is None:
        raise TaskNotFoundError(
            f"定时任务 {schedule_id} 不存在",
            details={"scheduleId": schedule_id},
        )

    # 重算 next_run_at（以当前时间为基准，保证是未来时间）
    try:
        new_next_run = compute_next_run(schedule.cron_expression, schedule.timezone)
    except ValueError as exc:
        raise ValidationError(
            f"计算下次执行时间失败: {exc}",
            details={"cronExpression": schedule.cron_expression, "timezone": schedule.timezone},
        ) from exc

    # 同时更新 enabled=true 与 next_run_at
    updated = await repo.update(
        ctx,
        schedule_id,
        enabled=True,
        next_run_at=new_next_run,
    )
    await db.commit()
    schedule = updated if updated is not None else schedule

    return ApiResponse.success(
        data=_schedule_to_dict(schedule),
        meta=build_meta(ctx.project_code),
    )


@router.post("/{schedule_id}/run")
async def trigger_schedule_run(
    schedule_id: str,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_SCHEDULES_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """手动触发定时任务（``POST /api/v1/schedules/{scheduleId}/run``）。

    立即创建 ScheduleRun（planned_at=当前 UTC 时间）并投递
    ``execute_schedule_task`` 到 maintenance 队列。手动触发不受 cron 调度控制，
    也不更新 ``next_run_at`` / ``last_run_at``（避免干扰正常调度周期）。

    与自动调度的区别
    ----------------
    - 自动调度：Celery Beat 每分钟扫描到期任务，planned_at=cron 推算的下次时间
    - 手动触发：planned_at=当前时间，立即入队执行

    幂等性
    ------
    手动触发不使用幂等键约束（planned_at=当前时间每次不同），
    允许同一任务被多次手动触发。若需防重复，客户端应自行加锁。

    Args:
        schedule_id: 定时任务 ID。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含：
        - ``runId``：新建的运行记录 ID
        - ``queueJobId``：Celery 任务 ID
        - ``status``：固定为 ``PENDING``

    Raises:
        TaskNotFoundError: 任务不存在或不属于当前项目。
        ValidationError: 任务已停用，需先恢复再手动触发。
    """
    repo = ScheduleRepository(db)
    schedule = await repo.get_by_id(ctx, schedule_id)
    if schedule is None:
        raise TaskNotFoundError(
            f"定时任务 {schedule_id} 不存在",
            details={"scheduleId": schedule_id},
        )

    # 防御性校验：停用的任务不允许手动触发，避免误操作已下线的调度
    if not schedule.enabled:
        raise ValidationError(
            "任务已停用，请先恢复（POST /schedules/{id}/resume）再手动触发",
            details={"scheduleId": schedule_id, "enabled": False},
        )

    # ------------------------------------------------------------------
    # 创建运行记录（planned_at=当前 UTC 时间）
    # ------------------------------------------------------------------
    # 手动触发使用当前时间作为 planned_at，与自动调度的 cron 推算时间区分
    run_repo = ScheduleRunRepository(db)
    now = datetime.now(timezone.utc)
    run = await run_repo.create(ctx, schedule_id, now)
    # create 在唯一约束冲突时返回 None；手动触发 planned_at=now 每次不同，
    # 理论上不会冲突，此处防御性处理
    if run is None:
        raise ValidationError(
            "手动触发失败：运行记录已存在（极端竞态），请稍后重试",
            details={"scheduleId": schedule_id},
        )

    # ------------------------------------------------------------------
    # 投递执行任务到 maintenance 队列
    # ------------------------------------------------------------------
    # 延迟导入避免循环依赖（schedule_tasks 导入 cron_utils 等）
    from app.workers.schedule_tasks import execute_schedule_task

    # delay() 将任务序列化后投递到 maintenance 队列，立即返回
    async_result = execute_schedule_task.delay(run.id)

    # 回写 queue_job_id（Celery 任务 ID），便于后续追踪
    await run_repo.update(ctx, run.id, queue_job_id=async_result.id)
    await db.commit()

    return ApiResponse.success(
        data={
            "runId": run.id,
            "queueJobId": async_result.id,
            "status": "PENDING",
        },
        meta=build_meta(ctx.project_code),
    )


# ============================================================================
# 运行记录查询接口
# ============================================================================
@router.get("/{schedule_id}/runs")
async def list_schedule_runs(
    schedule_id: str,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_SCHEDULES_READ)),
    offset: int = Query(default=0, ge=0, description="分页偏移量"),
    limit: int = Query(default=20, ge=1, le=100, description="每页条数，默认 20"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """列出定时任务的运行记录（``GET /api/v1/schedules/{scheduleId}/runs``）。

    先校验 schedule 归属当前项目（防止跨项目枚举运行记录），
    再分页查询关联的 ScheduleRun。

    Args:
        schedule_id: 定时任务 ID。
        ctx: 项目上下文。
        offset: 分页偏移量。
        limit: 每页条数。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含：
        - ``items``：运行记录列表
        - ``total``：符合条件的运行记录总数
        ``meta`` 字段包含分页信息。

    Raises:
        TaskNotFoundError: 定时任务不存在或不属于当前项目。
    """
    # 先校验 schedule 归属，防止跨项目枚举运行记录
    schedule_repo = ScheduleRepository(db)
    schedule = await schedule_repo.get_by_id(ctx, schedule_id)
    if schedule is None:
        raise TaskNotFoundError(
            f"定时任务 {schedule_id} 不存在",
            details={"scheduleId": schedule_id},
        )

    # 分页查询运行记录（list_by_schedule 已带 project_id + schedule_id 过滤）
    run_repo = ScheduleRunRepository(db)
    items, total = await run_repo.list_by_schedule(ctx, schedule_id, offset, limit)

    data = {
        "items": [_run_to_dict(r) for r in items],
        "total": total,
    }
    meta = build_meta(ctx.project_code)
    meta["pagination"] = {
        "offset": offset,
        "limit": limit,
        "total": total,
    }

    return ApiResponse.success(data=data, meta=meta)


@schedule_runs_router.get("/{run_id}")
async def get_schedule_run(
    run_id: str,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_SCHEDULES_READ)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """运行记录详情（``GET /api/v1/schedule-runs/{runId}``）。

    跨项目隔离
    ----------
    查询时强制带 project_id 过滤，跨项目查询返回 None，统一抛
    ``TaskNotFoundError``（404）。

    Args:
        run_id: 运行记录 ID（路径参数）。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含完整运行记录信息。

    Raises:
        TaskNotFoundError: 运行记录不存在或不属于当前项目（404）。
    """
    run_repo = ScheduleRunRepository(db)
    # 查询时强制带 project_id 过滤
    run = await run_repo.get_by_id(ctx, run_id)
    if run is None:
        # 跨项目查询统一返回 404，不泄露运行记录是否存在
        raise TaskNotFoundError(
            f"运行记录 {run_id} 不存在",
            details={"runId": run_id},
        )

    return ApiResponse.success(
        data=_run_to_dict(run),
        meta=build_meta(ctx.project_code),
    )
