"""智能研究接口（Task 15 / Task 16 / Task 23）。

对应 Task 15：``POST /api/v1/research/run`` 短链路研究接口（同步）。
对应 Task 16：异步研究任务接口与反馈接口。

短链路研究设计
--------------
1. **并行取证 + 一次模型生成**
   - 三路证据源（内部检索 / 联网搜索 / 工具调用）用 ``asyncio.gather`` 并行调度
   - 仅在证据整理完成后发起 1 次聊天模型调用（短链路一次生成原则）
   - 整体硬超时 15 秒（``settings.research_hard_timeout_seconds``）

2. **降级策略**
   - 单路取证失败不阻塞其他路，记录降级原因
   - 模型超时 / 失败返回已整理证据 + degraded=true
   - 整体硬超时返回部分成果 + degraded=true

3. **Scope 校验**
   - 需要 ``research:run`` Scope，由 ``require_scopes`` 依赖校验
   - 这是平台最重的接口（含模型生成 + 外部调用），应严格管控

4. **跨项目隔离**
   - HybridSearcher 在 SQL 层通过 ``project_id`` 前置过滤
   - ToolExecutor 校验 ProjectTool 白名单（项目级）+ ToolDefinition.applicable_projects（全局）
   - WebResearchService 按项目 SourcePolicy 过滤域名

5. **幂等性**
   - ``request_id`` 由 ``get_request_id`` 依赖从 X-Request-Id 头获取或生成
   - ``ResearchTask.request_id`` 全局 UNIQUE，重复调用由数据库约束拦截
   - 异步任务接口（Task 16）通过 ``Idempotency-Key`` 头做幂等校验
   - 同步接口（Task 23.2）支持可选 ``Idempotency-Key`` 头做幂等校验

异步任务接口（Task 16）
----------------------
- ``POST /research/jobs``：提交异步研究任务，返回 jobId 与 statusUrl
- ``GET /research/jobs/{jobId}``：查询任务状态与结果
- ``GET /research/jobs``：列出当前项目的任务

反馈接口（Task 16）
------------------
- ``POST /research/{requestId}/feedback``：提交对研究结论的反馈

Task 23 增强
------------
- **项目级限流**（23.1）：``/research/run`` 与 ``/research/jobs`` 共用 ``research_run``
  分组计数，每 Key 每分钟 60 次，超限返回 429 + Retry-After
- **幂等扩展**（23.2）：``/research/run`` 支持可选 ``Idempotency-Key``
- **可观察性**（23.4）：``/research/run`` 失败场景补写 UsageLog（error_code 填充），
  成功场景由 ResearchService 内部写入
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_request_id, require_scopes
from app.api.v1.schemas import (
    FeedbackRequest,
    ResearchRunRequest,
)
from app.core.exceptions import (
    KnowledgeHubError,
    TaskNotFoundError,
    ValidationError,
)
from app.core.idempotency import (
    check_idempotency,
    compute_request_hash,
    set_idempotency_record,
)
from app.core.project_context import ProjectContext
from app.core.rate_limiter import (
    apply_rate_limit_headers,
    check_rate_limit,
    get_rate_limit_info,
)
from app.core.redactor import truncate_query_for_log
from app.core.response import ApiResponse, build_meta
from app.core.scopes import SCOPE_FEEDBACK_WRITE, SCOPE_RESEARCH_RUN, SCOPE_TASKS_READ
from app.db.repositories.audit import FeedbackRepository, UsageLogRepository
from app.db.repositories.research import (
    ResearchEvidenceRepository,
    ResearchResultRepository,
    ResearchTaskRepository,
)
from app.db.session import get_db
from app.modules.research.service import ResearchService
from app.workers.research_tasks import run_research_async

# 端点分组常量：限流计数 Key，/research/run 与 /research/jobs 共用此分组
ENDPOINT_GROUP = "research_run"

# 应用日志器：记录限流降级、UsageLog 写入失败等
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/research", tags=["研究"])


@router.post("/run")
async def research_run(
    payload: ResearchRunRequest,
    request: Request,
    response: Response,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_RESEARCH_RUN)),
    request_id: str = Depends(get_request_id),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        description="可选幂等键，传入则做幂等校验（相同 Key + 相同内容重放原响应）",
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """短链路研究：并行取证 + 一次模型生成。

    整体硬超时 15 秒（``settings.research_hard_timeout_seconds``），
    超时返回降级结果（已整理证据 + degraded=true）。

    流程
    ----
    1. **项目级限流**（Task 23.1）：每 Key 每分钟 60 次，超限抛 429
    2. **幂等校验**（Task 23.2）：可选 Idempotency-Key，命中则重放原响应
    3. 创建 ResearchTask 记录（status='running'）
    4. 并行取证（asyncio.gather）：
       - 内部检索：HybridSearcher.search（strategy 不含 knowledge 时跳过）
       - 联网搜索：WebResearchService.search_and_extract（strategy 不含 web 时跳过）
       - 工具调用：ToolExecutor.execute（strategy 不含 tools 时跳过，最多 3 个工具）
       每个分支用 try/except 包裹，失败记录降级原因，不阻塞其他分支
    5. 合并证据：EvidenceMerger.merge 统一格式、去重、评分、截断到 8 条
    6. 证据校验：为空时抛 InsufficientEvidenceError，标记 task failed
    7. 加载提示词：PromptService.get_active，无启用版本用默认模板
    8. 构造模型输入：system_prompt + evidence_rules + prohibitions +
       risk_template + user_message（问题 + 证据列表 + output_schema + context）
    9. 一次模型生成（asyncio.wait_for 超时 10s）：
       - 超时 → 标记 degraded，返回已整理证据与失败状态
       - 解析 JSON 响应（response_format=json_object）
    10. 构造结果：answer / conclusions / suggested_actions / confidence /
       uncertainties / risk_notice / timing
    11. 持久化：批量写 ResearchEvidence、写 ResearchResult、更新 ResearchTask
    12. 记录 UsageLog：token 用量、费用、耗时（成功由 Service 写，失败由接口层补写）

    Args:
        payload: 请求体，包含 question / outputType / strategy /
            knowledgeBaseIds / toolCodes / toolInputs / context。
        request: FastAPI Request 对象（用于日志脱敏，不直接读取 body）。
        response: FastAPI Response 对象，用于注入限流响应头。
        ctx: 项目上下文，由 ``require_scopes(SCOPE_RESEARCH_RUN)`` 注入，
            同时校验 Scope 并解析项目。
        request_id: 请求 ID，由 ``get_request_id`` 依赖从 X-Request-Id 头获取或生成，
            用于关联 ResearchTask 与 UsageLog。
        idempotency_key: 可选幂等键，传入则做幂等校验。
        db: 异步数据库会话，由 ``get_db`` 注入。

    Returns:
        标准响应字典，``data`` 字段包含：
        - ``taskId``：研究任务 ID
        - ``requestId``：对外请求 ID
        - ``answer``：最终回答文本
        - ``conclusions``：结论数组
        - ``suggestedActions``：建议行动数组
        - ``evidence``：证据列表（已合并、去重、评分、截断）
        - ``confidence``：置信度（0~1）
        - ``uncertainties``：不确定性数组
        - ``riskNotice``：风险提示文本
        - ``timing``：各阶段耗时（internalRetrievalMs / externalParallelMs /
          generationMs / totalMs）
        - ``degraded``：是否降级
        - ``degradedReasons``：降级原因列表
    """
    # ------------------------------------------------------------------
    # 步骤 1：项目级限流（Scope 校验之后、业务逻辑之前）
    # ------------------------------------------------------------------
    # /research/run 与 /research/jobs 共用 research_run 分组计数
    await check_rate_limit(ctx.api_key_id or "", ENDPOINT_GROUP)
    rate_info = await get_rate_limit_info(ctx.api_key_id or "", ENDPOINT_GROUP)
    apply_rate_limit_headers(response.headers, rate_info)

    # ------------------------------------------------------------------
    # 步骤 2：幂等校验（可选 Idempotency-Key）
    # ------------------------------------------------------------------
    # 计算请求体哈希：排除 request_id（每次不同），仅基于业务字段
    request_body = payload.model_dump(exclude_none=False)
    request_hash = compute_request_hash(request_body)

    # check_idempotency:
    #   - None：未提供 Key 或首次请求 → 继续处理
    #   - dict：重放原响应 → 直接返回
    #   - 抛 IdempotencyConflictError：相同 Key + 不同内容 → 409
    cached_response = await check_idempotency(idempotency_key, request_hash)
    if cached_response is not None:
        # 命中幂等记录：直接返回原响应
        return cached_response

    # ------------------------------------------------------------------
    # 步骤 3：执行研究（捕获失败场景补写 UsageLog）
    # ------------------------------------------------------------------
    # 记录开始时间：失败时用于 UsageLog 的 total_ms
    import time as _time
    start_time = _time.time()

    # 将 knowledgeBaseIds 字符串列表转为 UUID 列表
    # UUID 解析失败会抛 ValueError，由全局异常处理器转为 INTERNAL_ERROR
    kb_uuids: list[UUID] = []
    for kb_id_str in payload.knowledgeBaseIds:
        kb_uuids.append(UUID(kb_id_str))

    # 构造研究服务并执行
    # ResearchService.run 内部已写入 UsageLog（成功场景）
    # 失败场景（抛异常）由本接口层补写 UsageLog（error_code 填充）
    service = ResearchService()
    try:
        result = await service.run(
            ctx=ctx,
            question=payload.question,
            output_type=payload.outputType,
            strategy=payload.strategy,
            knowledge_base_ids=kb_uuids,
            tool_codes=payload.toolCodes,
            tool_inputs=payload.toolInputs,
            context=payload.context,
            db=db,
            request_id=request_id,
        )
    except KnowledgeHubError as exc:
        # 业务异常（如 InsufficientEvidenceError）：补写 UsageLog
        # Service 内部已标记 task failed，但未写 UsageLog
        elapsed_ms = int((_time.time() - start_time) * 1000)
        await _write_research_usage_log(
            db=db,
            ctx=ctx,
            request_id=request_id,
            timing={},
            evidence_count=0,
            degraded=True,
            degraded_reasons=[exc.code.lower()],
            error_code=exc.code,
            elapsed_ms=elapsed_ms,
        )
        # 重新抛出，由全局异常处理器转为标准错误响应
        raise
    except Exception as exc:
        # 未预期异常：补写 UsageLog 后重新抛出
        elapsed_ms = int((_time.time() - start_time) * 1000)
        error_code = type(exc).__name__.upper()
        await _write_research_usage_log(
            db=db,
            ctx=ctx,
            request_id=request_id,
            timing={},
            evidence_count=0,
            degraded=True,
            degraded_reasons=["internal_error"],
            error_code=error_code,
            elapsed_ms=elapsed_ms,
        )
        raise

    # ------------------------------------------------------------------
    # 步骤 4：构造响应数据
    # ------------------------------------------------------------------
    # 字段命名采用 camelCase（与前端约定一致）
    response_data = ApiResponse.success(
        data={
            "taskId": result.task_id,
            "requestId": request_id,
            "answer": result.answer,
            "conclusions": result.conclusions,
            "suggestedActions": result.suggested_actions,
            # 证据列表字段转为 camelCase，便于前端解析
            "evidence": [
                {
                    "type": ev.get("type"),
                    "title": ev.get("title"),
                    "snippet": ev.get("snippet"),
                    "sourceUrl": ev.get("source_url"),
                    "publishedAt": ev.get("published_at"),
                    "dataAsOf": ev.get("data_as_of"),
                    "score": ev.get("score"),
                }
                for ev in result.evidence
            ],
            "confidence": result.confidence,
            "uncertainties": result.uncertainties,
            "riskNotice": result.risk_notice,
            # timing 字段已为 camelCase（构造时即为 internalRetrievalMs 等）
            "timing": result.timing,
            "degraded": result.degraded,
            "degradedReasons": result.degraded_reasons,
        },
        meta=build_meta(ctx.project_code),
    )

    # ------------------------------------------------------------------
    # 步骤 5：写入幂等记录（仅成功响应缓存，失败不缓存）
    # ------------------------------------------------------------------
    if idempotency_key is not None:
        try:
            await set_idempotency_record(idempotency_key, request_hash, response_data)
        except Exception:
            # 幂等记录写入失败：仅记录日志，不影响响应
            logger.warning(
                "幂等记录写入失败（idempotency_key=%s）",
                idempotency_key,
                exc_info=True,
            )

    return response_data


# ---------------------------------------------------------------------------
# 辅助函数：写入研究接口的 UsageLog（Task 23.4，失败场景补写）
# ---------------------------------------------------------------------------
async def _write_research_usage_log(
    db: AsyncSession,
    ctx: ProjectContext,
    request_id: str,
    timing: dict,
    evidence_count: int,
    degraded: bool,
    degraded_reasons: list[str],
    error_code: str | None,
    elapsed_ms: int,
) -> None:
    """写入研究接口的 UsageLog 记录（主要用于失败场景补写）。

    成功场景的 UsageLog 由 ``ResearchService.run`` 内部写入（含 token 用量与费用），
    失败场景（抛异常）Service 内部不会执行到 UsageLog 写入逻辑，
    由本接口层在捕获异常后补写，保证每次调用都有 usage_logs 记录。

    Args:
        db: 异步数据库会话。
        ctx: 项目上下文。
        request_id: 请求 ID。
        timing: 各阶段耗时字典（失败场景可能为空）。
        evidence_count: 证据数（失败场景可能为 0）。
        degraded: 是否降级。
        degraded_reasons: 降级原因列表。
        error_code: 错误码，成功时为 None。
        elapsed_ms: 接口层总耗时（毫秒），用于失败场景的 total_ms。
    """
    try:
        usage_repo = UsageLogRepository(db)
        await usage_repo.create(
            ctx,
            request_id=request_id,
            api_key_id=ctx.api_key_id or "",
            endpoint="/api/v1/research/run",
            method="POST",
            # 失败场景 timing 可能为空，用接口层总耗时兜底
            internal_retrieval_ms=timing.get("internalRetrievalMs") if timing else None,
            external_parallel_ms=timing.get("externalParallelMs") if timing else None,
            generation_ms=timing.get("generationMs") if timing else None,
            total_ms=timing.get("totalMs", elapsed_ms) if timing else elapsed_ms,
            evidence_count=evidence_count,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            estimated_cost=None,
            degraded=degraded,
            degraded_reasons=degraded_reasons,
            error_code=error_code,
        )
        await db.commit()
    except Exception:
        # UsageLog 写入失败：不阻塞响应，仅记录日志
        logger.warning(
            "UsageLog 写入失败，已忽略（request_id=%s, endpoint=/research/run）",
            request_id,
            exc_info=True,
        )
        await db.rollback()


# ============================================================================
# 异步研究任务接口（Task 16.2）
# ============================================================================
@router.post("/jobs")
async def submit_research_job(
    payload: ResearchRunRequest,
    request: Request,
    response: Response,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_RESEARCH_RUN)),
    request_id: str = Depends(get_request_id),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        description="幂等键，客户端生成的唯一标识，相同 Key + 相同内容返回原 job",
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """提交异步研究任务：创建 ResearchTask 并投递到 Celery 队列。

    适用场景
    --------
    1. **客户端容忍延迟**：异步任务可在 30s 内完成，无需 HTTP 长连接
    2. **削峰填谷**：高峰期任务进入队列排队，避免瞬时高并发拖垮模型与外部接口
    3. **可重试**：Worker 崩溃或网络抖动可通过 Celery 重试机制自动恢复

    为什么必须传 Idempotency-Key？
    ------------------------------
    异步任务一旦入队就无法撤回，若客户端因网络抖动重试，会创建多个相同任务，
    浪费模型调用配额与外部接口额度。``Idempotency-Key`` 在 API 层做幂等校验：
    - 相同 Key + 相同内容 → 返回原 job（不重复入队）
    - 相同 Key + 不同内容 → 抛 ``IdempotencyConflictError``（409）
    - 不存在 → 创建新任务并写入幂等记录

    流程
    ----
    1. **项目级限流**（Task 23.1）：与 /research/run 共用 research_run 分组计数
    2. 校验 ``Idempotency-Key`` 头存在，未传返回 ``VALIDATION_ERROR``
    3. 幂等校验：相同 Key + 相同内容 → 返回原 job；相同 Key + 不同内容 → 409
    4. 创建 ResearchTask（status='pending'）
    5. 触发 ``run_research_async.delay(project_id, project_code, task_id)``
    6. 返回 ``{ jobId, status: "PENDING", statusUrl }``
    7. 写入幂等记录（24h TTL）

    Args:
        payload: 请求体，同 ``ResearchRunRequest``。
        request: FastAPI Request 对象（未直接使用，签名保留以备扩展）。
        response: FastAPI Response 对象，用于注入限流响应头。
        ctx: 项目上下文，由 ``require_scopes(SCOPE_RESEARCH_RUN)`` 注入。
        request_id: 请求 ID，用于关联 ResearchTask 与 UsageLog。
        idempotency_key: 幂等键，客户端生成的唯一标识。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含：
        - ``jobId``：异步研究任务 ID（即 ResearchTask.id）
        - ``status``：初始状态，固定为 ``PENDING``
        - ``statusUrl``：任务状态查询地址

    Raises:
        ValidationError: 未传 Idempotency-Key 头。
        IdempotencyConflictError: 相同 Key 但请求内容不同。
        RateLimitedError: 超过每分钟限流配额。
    """
    # ------------------------------------------------------------------
    # 步骤 1：项目级限流（与 /research/run 共用 research_run 分组计数）
    # ------------------------------------------------------------------
    await check_rate_limit(ctx.api_key_id or "", ENDPOINT_GROUP)
    rate_info = await get_rate_limit_info(ctx.api_key_id or "", ENDPOINT_GROUP)
    apply_rate_limit_headers(response.headers, rate_info)

    # ------------------------------------------------------------------
    # 步骤 2：校验 Idempotency-Key 头必填
    # ------------------------------------------------------------------
    # 异步任务无法撤回，必须通过幂等键防止重复入队
    if not idempotency_key:
        raise ValidationError(
            "异步研究任务必须传 Idempotency-Key 头，防止重复入队",
            details={"missing_header": "Idempotency-Key"},
        )

    # ------------------------------------------------------------------
    # 步骤 3：构造请求体字典并计算 request_hash
    # ------------------------------------------------------------------
    # request_hash 用于幂等校验时判断"内容是否相同"
    # 排除 request_id（每次请求不同，不应参与内容判等）
    request_body = payload.model_dump(exclude_none=False)
    request_hash = compute_request_hash(request_body)

    # ------------------------------------------------------------------
    # 步骤 4：幂等校验
    # ------------------------------------------------------------------
    # check_idempotency 返回 None 表示首次请求或未提供 Key（此处 Key 已校验非空）
    # 返回 dict 表示重放原响应（相同 Key + 相同内容）
    # 抛 IdempotencyConflictError 表示相同 Key + 不同内容
    cached_response = await check_idempotency(idempotency_key, request_hash)
    if cached_response is not None:
        # 命中幂等记录：直接返回原响应，不创建新任务
        # 注意：cached_response 已是完整 ApiResponse.success 结构，直接返回
        return cached_response

    # ------------------------------------------------------------------
    # 步骤 5：创建 ResearchTask（status='pending'）
    # ------------------------------------------------------------------
    # 解析 strategy 决定是否启用各证据源（与同步接口一致）
    use_web = "web" in payload.strategy or payload.strategy in (
        "knowledge_web",
        "full",
    )

    # knowledge_base_ids 转为字符串列表存储（数据库 ARRAY(UUID) 接受字符串）
    kb_ids_str = [str(kb_id) for kb_id in payload.knowledgeBaseIds]
    # 工具调用列表截断到前 3 个（与同步接口一致，避免 token 与延迟爆炸）
    # 导入常量避免魔法数字
    from app.modules.research.service import MAX_TOOLS_PER_RUN
    limited_tool_codes = list(payload.toolCodes)[:MAX_TOOLS_PER_RUN]

    task_repo = ResearchTaskRepository(db)
    task = await task_repo.create(
        ctx,
        request_id=request_id,
        question=payload.question,
        output_type=payload.outputType,
        strategy=payload.strategy,
        status="pending",  # 初始状态：已入队，等待 Worker 领取
        input_context=payload.context,
        knowledge_base_ids=kb_ids_str,
        requested_tools=limited_tool_codes,
        use_web=use_web,
        # started_at 留空：Worker 领取后才填充
        degraded=False,
        degraded_reasons=[],
    )
    # 提交事务：保证 task 已持久化，Worker 可通过 task_id 查询到
    await db.commit()

    # ------------------------------------------------------------------
    # 步骤 6：触发 Celery 异步任务
    # ------------------------------------------------------------------
    # delay() 将任务序列化后投递到 online 队列，立即返回
    # Worker 通过 -Q online 参数消费队列，领取任务后调用 run_research_async
    run_research_async.delay(ctx.project_id, ctx.project_code, task.id)

    # ------------------------------------------------------------------
    # 步骤 7：构造响应并写入幂等记录
    # ------------------------------------------------------------------
    status_url = f"/api/v1/research/jobs/{task.id}"
    response_data = {
        "jobId": task.id,
        "status": "PENDING",
        "statusUrl": status_url,
    }
    # 注意：此处变量名用 response_payload 而非 response，避免覆盖 Response 参数
    response_payload = ApiResponse.success(
        data=response_data,
        meta=build_meta(ctx.project_code),
    )

    # 写入幂等记录：相同 Key + 相同内容后续重试可直接重放此响应
    # TTL 24h，覆盖客户端重试窗口
    await set_idempotency_record(idempotency_key, request_hash, response_payload)

    return response_payload


@router.get("/jobs/{job_id}")
async def get_research_job_status(
    job_id: str,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_TASKS_READ)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """查询异步研究任务状态。

    返回任务当前状态、关键时间戳与最终结果。``result`` 仅在
    ``status=SUCCESS`` 或 ``status=PARTIAL_SUCCESS`` 时填充。

    跨项目隔离
    ----------
    查询时通过 ``ResearchTaskRepository.get_by_id`` 强制带 project_id 过滤，
    若 task 不属于当前项目返回 None，统一抛 ``TASK_NOT_FOUND``（404）。
    不区分"不存在"与"属于其他项目"，避免泄露跨项目任务是否存在。

    状态机流转
    ----------
    PENDING → RUNNING → SUCCESS / PARTIAL_SUCCESS / FAILED / TIMEOUT

    Args:
        job_id: 研究任务 ID（路径参数）。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含：
        - ``jobId``：任务 ID
        - ``status``：任务状态（大写形式）
        - ``question``：用户问题原文
        - ``startedAt``：任务开始时间，PENDING 时为 None
        - ``completedAt``：任务完成时间，未完成时为 None
        - ``totalDurationMs``：总耗时（毫秒），未完成时为 None
        - ``degraded``：是否降级
        - ``degradedReasons``：降级原因列表
        - ``errorCode``：失败错误码，成功时为 None
        - ``result``：完整研究结果，仅成功/部分成功时填充

    Raises:
        TaskNotFoundError: 任务不存在或不属于当前项目（404）。
    """
    task_repo = ResearchTaskRepository(db)
    # 查询时强制带 project_id 过滤，跨项目查询返回 None
    task = await task_repo.get_by_id(ctx, job_id)
    if task is None:
        # 跨项目查询统一返回 404，不泄露任务是否存在
        raise TaskNotFoundError(
            f"研究任务 {job_id} 不存在",
            details={"jobId": job_id},
        )

    # 将内部小写状态转为对外大写形式
    status_upper = task.status.upper()

    # 构造基础响应数据（不含 result）
    data: dict = {
        "jobId": task.id,
        "status": status_upper,
        "question": task.question,
        "startedAt": task.started_at.isoformat() if task.started_at else None,
        "completedAt": task.completed_at.isoformat() if task.completed_at else None,
        "totalDurationMs": task.total_duration_ms,
        "degraded": task.degraded,
        "degradedReasons": task.degraded_reasons or [],
        "errorCode": task.error_code,
        "result": None,  # 默认 None，成功/部分成功时填充
    }

    # ------------------------------------------------------------------
    # 成功/部分成功时加载完整结果（ResearchResult + ResearchEvidence）
    # ------------------------------------------------------------------
    # 为什么 PARTIAL_SUCCESS 也要返回 result？
    #   降级场景下证据已收集但模型生成可能失败，客户端仍可基于证据做决策，
    #   因此需返回 result（含 evidence 字段）让客户端能利用已收集的证据。
    if task.status in ("success", "partial_success"):
        result_repo = ResearchResultRepository(db)
        evidence_repo = ResearchEvidenceRepository(db)

        # 加载研究结果（每任务至多一条）
        result = await result_repo.get_by_task(ctx, task.id)
        # 加载证据列表（按 score 降序）
        evidences = await evidence_repo.list_by_task(ctx, task.id)

        # 构造 result 字段：含 answer / conclusions / evidence 等
        data["result"] = {
            "answer": result.answer if result else "",
            "conclusions": result.conclusions if result else [],
            "suggestedActions": result.suggested_actions if result else [],
            # 证据列表字段转为 camelCase，便于前端解析
            "evidence": [
                {
                    "type": ev.evidence_type,
                    "title": ev.title,
                    "snippet": ev.snippet,
                    "sourceUrl": ev.source_url,
                    "publishedAt": ev.published_at.isoformat() if ev.published_at else None,
                    "dataAsOf": ev.data_as_of.isoformat() if ev.data_as_of else None,
                    "score": ev.score,
                }
                for ev in evidences
            ],
            "confidence": result.confidence if result else 0.0,
            "uncertainties": result.uncertainties if result else [],
            "riskNotice": result.risk_notice if result else "",
            "timing": result.timing if result else {},
        }

    return ApiResponse.success(
        data=data,
        meta=build_meta(ctx.project_code),
    )


@router.get("/jobs")
async def list_research_jobs(
    ctx: ProjectContext = Depends(require_scopes(SCOPE_TASKS_READ)),
    status: str | None = Query(
        default=None,
        description="按状态过滤（小写形式，如 pending/running/success/failed）",
    ),
    offset: int = Query(default=0, ge=0, description="分页偏移量"),
    limit: int = Query(default=20, ge=1, le=100, description="每页条数，默认 20"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """列出当前项目的异步研究任务。

    返回任务列表与总数，按创建时间倒序。``status`` 参数为小写形式
    （与数据库存储一致），如 ``pending`` / ``running`` / ``success`` 等。

    跨项目隔离
    ----------
    查询时强制带 project_id 过滤，仅返回当前项目的任务。

    Args:
        ctx: 项目上下文。
        status: 按状态过滤（小写形式），可空。
        offset: 分页偏移量，默认 0。
        limit: 每页条数，默认 20，最大 100。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含：
        - ``items``：任务列表（不含完整 result）
        - ``total``：符合条件的任务总数

        ``meta`` 字段包含分页信息：
        - ``pagination``：``{ offset, limit, total }``
    """
    task_repo = ResearchTaskRepository(db)

    # 按状态过滤查询（status 为空时查询全部）
    # ResearchTaskRepository.list 支持 status 参数，传入 None 表示不过滤
    items, total = await task_repo.list(ctx, offset=offset, limit=limit, status=status)

    # 构造列表项：不含完整 result，仅含状态摘要
    # 状态转为大写形式，与详情接口一致
    data = {
        "items": [
            {
                "jobId": item.id,
                "status": item.status.upper(),
                "question": item.question,
                "degraded": item.degraded,
                "startedAt": item.started_at.isoformat() if item.started_at else None,
                "completedAt": item.completed_at.isoformat() if item.completed_at else None,
                "totalDurationMs": item.total_duration_ms,
                "createdAt": item.created_at.isoformat() if item.created_at else None,
            }
            for item in items
        ],
        "total": total,
    }

    # 分页 meta：便于客户端展示"共 N 条，第 X-Y 条"
    meta = build_meta(ctx.project_code)
    meta["pagination"] = {
        "offset": offset,
        "limit": limit,
        "total": total,
    }

    return ApiResponse.success(data=data, meta=meta)


# ============================================================================
# 反馈接口（Task 16.3）
# ============================================================================
@router.post("/{request_id}/feedback")
async def submit_feedback(
    request_id: str,
    payload: FeedbackRequest,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_FEEDBACK_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """提交对研究结论的反馈。

    反馈的价值
    ----------
    1. **系统优化**：识别质量差的研究结论，反向优化提示词与证据评分策略
       - 低分反馈集中于某类问题时，定位提示词缺陷
       - 低分反馈集中于某降级原因时，定位系统瓶颈
    2. **效果评估**：结合 ``businessResultId`` 关联业务侧落地结果，
       评估研究结论对业务决策的实际贡献（如基于研究结论的买入操作是否盈利）
    3. **数据飞轮**：高分反馈的研究结论可作为Few-shot 示例，
       提升后续相似问题的生成质量

    upsert 语义
    -----------
    同一 ``request_id`` 多次提交反馈会更新原记录，而非创建新记录。
    - 首次提交：创建 Feedback 记录
    - 后续提交：更新原记录的 rating / accepted / comment / business_result_id

    为什么采用 upsert 而非拒绝重复？
        用户可能先提交"部分有用"，看完详细证据后改为"有用"。
        拒绝重复会迫使用户先删除再创建，体验差且易丢失原始反馈时间。
        upsert 保留首次反馈的 created_at，仅更新内容，便于追溯反馈演变。

    流程
    ----
    1. 查询 ResearchTask（按 request_id + project_id 过滤），不存在返回 404
    2. 检查是否已有反馈（同一 request_id），已有则更新，无则创建
    3. 返回 ``{ feedbackId, requestId, rating, accepted }``

    Args:
        request_id: 研究任务的对外请求 ID（路径参数）。
        payload: 反馈请求体，含 rating / accepted / comment / businessResultId。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含：
        - ``feedbackId``：反馈记录 ID
        - ``requestId``：关联的研究任务对外请求 ID
        - ``rating``：评分
        - ``accepted``：是否采纳

    Raises:
        TaskNotFoundError: 研究任务不存在或不属于当前项目（404）。
    """
    task_repo = ResearchTaskRepository(db)
    feedback_repo = FeedbackRepository(db)

    # ------------------------------------------------------------------
    # 步骤 1：校验研究任务存在且属于当前项目
    # ------------------------------------------------------------------
    # 按 request_id + project_id 双重过滤，跨项目查询返回 None
    research_task = await task_repo.get_by_request_id(ctx, request_id)
    if research_task is None:
        # 研究任务不存在或不属于当前项目：统一返回 404
        raise TaskNotFoundError(
            f"研究任务 {request_id} 不存在",
            details={"requestId": request_id},
        )

    # ------------------------------------------------------------------
    # 步骤 2：upsert 反馈记录
    # ------------------------------------------------------------------
    # 查询是否已有反馈（同一 request_id + project_id）
    existing_feedback = await feedback_repo.get_by_request_id(ctx, request_id)

    if existing_feedback is not None:
        # 已有反馈：更新原记录（upsert 语义）
        # 保留 created_at，仅更新内容字段
        await feedback_repo.update(
            ctx,
            existing_feedback.id,
            rating=payload.rating,
            accepted=payload.accepted,
            comment=payload.comment,
            business_result_id=payload.businessResultId,
        )
        feedback_id = existing_feedback.id
    else:
        # 无反馈：创建新记录
        new_feedback = await feedback_repo.create(
            ctx,
            request_id=request_id,
            rating=payload.rating,
            accepted=payload.accepted,
            comment=payload.comment,
            business_result_id=payload.businessResultId,
        )
        feedback_id = new_feedback.id

    # 提交事务：保证反馈记录持久化
    await db.commit()

    # ------------------------------------------------------------------
    # 步骤 3：构造响应
    # ------------------------------------------------------------------
    data = {
        "feedbackId": feedback_id,
        "requestId": request_id,
        "rating": payload.rating,
        "accepted": payload.accepted,
    }

    return ApiResponse.success(
        data=data,
        meta=build_meta(ctx.project_code),
    )
