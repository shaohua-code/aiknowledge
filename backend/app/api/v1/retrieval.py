"""混合检索接口（Task 10.4 / Task 23）。

对应 ``POST /api/v1/retrieval/search``：执行混合检索（全文 + 向量 + RRF 合并），
仅返回知识片段，不调用聊天模型。

设计要点
--------
1. **纯检索**：本接口不调用聊天模型，仅返回命中片段与分数，便于业务方做
   自定义上下文拼接、重排、引用展示。研究链路（含生成）请使用 ``/research/run``。
2. **性能目标**：P95 ≤ 800ms。通过 HNSW + GIN 索引、并行检索、候选池控制
   （Top 30）、Embedding 单例等手段保证。
3. **安全隔离**：检索 SQL 在 WHERE 子句前置 ``project_id`` 过滤，杜绝跨项目召回。
   详见 ``HybridSearcher._vector_search`` / ``_fulltext_search`` 的注释。
4. **检索日志**：每次检索记录 ``retrieval_logs``（查询、命中数、耗时、分数），
   供性能分析与优化使用。日志写入失败不应阻塞响应，故日志 commit 失败时
   仅记录日志不抛出。
5. **Scope 校验**：需要 ``retrieval:read`` Scope，由 ``require_scopes`` 依赖校验。
6. **项目级限流**（Task 23.1）：每 API Key 每分钟 60 次，超限返回 429，
   响应头注入 ``X-RateLimit-*`` 信息。
7. **幂等支持**（Task 23.2）：可选 ``Idempotency-Key`` 头，传入则做幂等校验，
   相同 Key + 相同请求体重放原响应。
8. **可观察性**（Task 23.4）：每次检索写入 ``usage_logs``，记录耗时与命中数，
   失败时也写入（error_code 填充）。
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_request_id, require_scopes
from app.api.v1.schemas import RetrievalSearchRequest
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
from app.core.redactor import redact_dict, truncate_query_for_log
from app.core.response import ApiResponse, build_meta
from app.core.scopes import SCOPE_RETRIEVAL_READ
from app.db.repositories.audit import UsageLogRepository
from app.db.repositories.research import RetrievalLogRepository
from app.db.session import get_db
from app.modules.retrieval.hybrid_searcher import HybridSearcher

# 端点分组常量：用于限流计数 Key
ENDPOINT_GROUP = "retrieval_search"

# 应用日志器：记录限流降级、UsageLog 写入失败等
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/retrieval", tags=["检索"])


@router.post("/search")
async def retrieval_search(
    payload: RetrievalSearchRequest,
    request: Request,
    response: Response,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_RETRIEVAL_READ)),
    request_id: str = Depends(get_request_id),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        description="可选幂等键，传入则做幂等校验（相同 Key + 相同内容重放原响应）",
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """纯检索接口：执行混合检索，返回知识片段，不调用聊天模型。

    性能目标：P95 ≤ 800ms。

    流程
    ----
    1. **项目级限流**（Task 23.1）：每 Key 每分钟 60 次，超限抛 429
    2. **幂等校验**（Task 23.2）：可选 Idempotency-Key，命中则重放原响应
    3. 调用 ``HybridSearcher.search`` 执行混合检索
       - 生成查询 Embedding（复用 Provider 单例）
       - 并行执行全文检索（GIN 索引）与向量检索（HNSW 索引）
       - RRF 合并两路结果（k=60）
       - 按文档去重，截断到 ``topK``
    4. 记录检索日志到 ``retrieval_logs``（命中数、耗时、分数）
    5. 记录 UsageLog（Task 23.4，可观察性）
    6. 注入限流响应头 ``X-RateLimit-*``
    7. 返回标准 ``ApiResponse.success`` 响应

    Args:
        payload: 请求体，包含 ``query`` / ``knowledgeBaseIds`` / ``topK``。
        request: FastAPI Request 对象（用于日志脱敏，不直接读取 body）。
        response: FastAPI Response 对象，用于注入限流响应头。
        ctx: 项目上下文，由 ``require_scopes(SCOPE_RETRIEVAL_READ)`` 注入，
            同时校验 Scope 并解析项目。
        request_id: 请求 ID，用于关联 UsageLog。
        idempotency_key: 可选幂等键，传入则做幂等校验。
        db: 异步数据库会话，由 ``get_db`` 注入。

    Returns:
        标准响应字典，``data`` 字段包含：
        - ``query``：原查询文本（回显）
        - ``hits``：命中片段列表，按 ``score`` 降序
        - ``totalHits``：命中总数
        - ``elapsedMs``：检索耗时（毫秒）
    """
    # ------------------------------------------------------------------
    # 步骤 1：项目级限流（Scope 校验之后、业务逻辑之前）
    # ------------------------------------------------------------------
    # api_key_id 为空时（Worker 场景不会到这里）跳过限流
    # check_rate_limit 超限抛 RateLimitedError，由全局异常处理器转为 429
    await check_rate_limit(ctx.api_key_id or "", ENDPOINT_GROUP)

    # 注入限流响应头：供客户端感知剩余配额并主动退避
    # 即使后续逻辑抛异常，FastAPI 仍会使用已设置的 response.headers
    rate_info = await get_rate_limit_info(ctx.api_key_id or "", ENDPOINT_GROUP)
    apply_rate_limit_headers(response.headers, rate_info)

    # ------------------------------------------------------------------
    # 步骤 2：幂等校验（可选 Idempotency-Key）
    # ------------------------------------------------------------------
    # 计算请求体哈希：用于幂等校验时判断"内容是否相同"
    # request_hash 排除 request_id（每次不同），仅基于业务字段
    request_body = payload.model_dump(exclude_none=False)
    request_hash = compute_request_hash(request_body)

    # check_idempotency:
    #   - None：未提供 Key 或首次请求 → 继续处理
    #   - dict：重放原响应 → 直接返回（不再处理）
    #   - 抛 IdempotencyConflictError：相同 Key + 不同内容 → 409
    cached_response = await check_idempotency(idempotency_key, request_hash)
    if cached_response is not None:
        # 命中幂等记录：直接返回原响应（已含完整 ApiResponse 结构）
        # 限流已计数，幂等重放不额外消耗业务逻辑
        return cached_response

    # ------------------------------------------------------------------
    # 步骤 3：执行混合检索
    # ------------------------------------------------------------------
    # 记录开始时间，用于耗时统计与检索日志
    start_time = time.time()

    # 检索异常时的 error_code，用于 UsageLog 记录
    error_code: str | None = None
    results: list[dict] = []

    try:
        # 构造 HybridSearcher 并执行检索
        searcher = HybridSearcher(db)
        results = await searcher.search(
            ctx=ctx,
            query=payload.query,
            knowledge_base_ids=payload.knowledgeBaseIds,
            top_k=payload.topK,
        )
    except Exception as exc:
        # 检索失败：记录错误码，UsageLog 写入 error_code 后继续返回
        # 不在此处 re-raise，统一在 finally 中写 UsageLog，再由上层处理
        error_code = type(exc).__name__.upper()
        logger.warning(
            "检索执行失败（request_id=%s, error=%s）：%s",
            request_id,
            error_code,
            exc,
        )
        # 计算已发生耗时（用于 UsageLog）
        elapsed_ms = int((time.time() - start_time) * 1000)
        # 写入 UsageLog（失败场景，error_code 填充）
        await _write_usage_log(
            db=db,
            ctx=ctx,
            request_id=request_id,
            elapsed_ms=elapsed_ms,
            evidence_count=0,
            error_code=error_code,
        )
        # 重新抛出，由全局异常处理器兜底
        raise

    # 计算耗时：time.time() 秒级精度足够（检索耗时通常百毫秒级）
    elapsed_ms = int((time.time() - start_time) * 1000)

    # ------------------------------------------------------------------
    # 步骤 4：记录检索日志（retrieval_logs）
    # ------------------------------------------------------------------
    # 日志写入失败不应阻塞响应（检索已成功，仅日志失败），故 try/except 兜底
    try:
        log_repo = RetrievalLogRepository(db)
        await log_repo.create(
            ctx,
            # 脱敏后的查询文本：仅记录长度与截断内容，避免泄露敏感业务问题
            query=truncate_query_for_log(payload.query),
            knowledge_base_ids=payload.knowledgeBaseIds,
            hit_count=len(results),
            timing_ms=elapsed_ms,
            scores=[r["merged_score"] for r in results],
        )
        await db.commit()
    except Exception:
        # 日志写入失败：回滚日志相关变更，不影响已完成的检索结果返回
        # 注意：检索本身是只读的，回滚不会影响检索结果
        await db.rollback()

    # ------------------------------------------------------------------
    # 步骤 5：构造响应数据
    # ------------------------------------------------------------------
    # hits 列表字段命名采用 camelCase（与前端约定一致）
    response_data = ApiResponse.success(
        data={
            "query": payload.query,
            "hits": [
                {
                    "chunkId": str(r["chunk_id"]),
                    "documentId": str(r["document_id"]),
                    "content": r["content"],
                    "pageNumber": r["page_number"],
                    # merged_score 是 RRF 合并分数，范围 (0, 2/(k+1)]，
                    # 业务方可用于排序展示与阈值过滤
                    "score": r["merged_score"],
                    "metadata": r["metadata"],
                }
                for r in results
            ],
            "totalHits": len(results),
            "elapsedMs": elapsed_ms,
        },
        meta=build_meta(ctx.project_code),
    )

    # ------------------------------------------------------------------
    # 步骤 6：写入 UsageLog（Task 23.4 可观察性）
    # ------------------------------------------------------------------
    # 检索成功：error_code=None，evidence_count=命中数
    await _write_usage_log(
        db=db,
        ctx=ctx,
        request_id=request_id,
        elapsed_ms=elapsed_ms,
        evidence_count=len(results),
        error_code=None,
    )

    # ------------------------------------------------------------------
    # 步骤 7：写入幂等记录（仅成功响应缓存，失败不缓存）
    # ------------------------------------------------------------------
    if idempotency_key is not None:
        try:
            await set_idempotency_record(idempotency_key, request_hash, response_data)
        except Exception:
            # 幂等记录写入失败：仅记录日志，不影响响应
            # 后续重试可能重复执行业务逻辑，但 content_hash 兜底去重
            logger.warning(
                "幂等记录写入失败（idempotency_key=%s）",
                idempotency_key,
                exc_info=True,
            )

    return response_data


# ---------------------------------------------------------------------------
# 辅助函数：写入 UsageLog（Task 23.4）
# ---------------------------------------------------------------------------
async def _write_usage_log(
    db: AsyncSession,
    ctx: ProjectContext,
    request_id: str,
    elapsed_ms: int,
    evidence_count: int,
    error_code: str | None,
) -> None:
    """写入检索接口的 UsageLog 记录。

    检索接口无外部模型调用，仅记录：
        - endpoint='/api/v1/retrieval/search'
        - method='POST'
        - internal_retrieval_ms=elapsed_ms（检索本身就是内部检索）
        - total_ms=elapsed_ms
        - evidence_count=命中片段数
        - degraded=False（检索接口无降级概念）
        - error_code=失败时填充错误码

    UsageLog 写入失败不阻塞响应（try/except + warning 日志）。

    Args:
        db: 异步数据库会话。
        ctx: 项目上下文。
        request_id: 请求 ID。
        elapsed_ms: 检索耗时（毫秒）。
        evidence_count: 命中片段数。
        error_code: 错误码，成功时为 None。
    """
    try:
        usage_repo = UsageLogRepository(db)
        await usage_repo.create(
            ctx,
            request_id=request_id,
            api_key_id=ctx.api_key_id or "",
            endpoint="/api/v1/retrieval/search",
            method="POST",
            # 检索接口本身就是内部检索，耗时直接记到 internal_retrieval_ms
            internal_retrieval_ms=elapsed_ms,
            external_parallel_ms=None,
            generation_ms=None,
            total_ms=elapsed_ms,
            evidence_count=evidence_count,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            estimated_cost=None,
            degraded=False,
            degraded_reasons=[],
            error_code=error_code,
        )
        # 独立提交：避免与 retrieval_logs 的事务相互影响
        await db.commit()
    except Exception:
        # UsageLog 写入失败：回滚避免会话状态污染，不影响响应
        logger.warning(
            "UsageLog 写入失败，已忽略（request_id=%s, endpoint=/retrieval/search）",
            request_id,
            exc_info=True,
        )
        await db.rollback()
