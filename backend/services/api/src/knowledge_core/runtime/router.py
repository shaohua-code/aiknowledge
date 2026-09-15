from __future__ import annotations

import hashlib
import time
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.domains.intelligence.repository import IntelligenceRepository
from knowledge_core.domains.intelligence.retrieval import RetrievalService
from knowledge_core.domains.intelligence.schemas import (
    AnswerRequest,
    AnswerResponse,
    FeedbackAcceptedResponse,
    FeedbackRequest,
    RetrieveRequest,
    RetrieveResponse,
)
from knowledge_core.domains.intelligence.service import AnswerService
from knowledge_core.domains.operations.repository import OperationRepository
from knowledge_core.infrastructure.database import get_session
from knowledge_core.runtime.auth import require_scopes
from knowledge_core.runtime.rate_limit import check_rate_limit
from knowledge_core.shared.context import ApplicationContext
from knowledge_core.shared.errors import NotFoundError
from knowledge_core.shared.request_id import get_request_id
from knowledge_core.shared.response import success
from knowledge_core.shared.schema import ApiSuccess

router = APIRouter(prefix="/runtime/v1", tags=["业务运行面"])


@router.post("/retrieve", response_model=ApiSuccess[RetrieveResponse])
async def retrieve(
    payload: RetrieveRequest,
    context: Annotated[ApplicationContext, Depends(require_scopes("knowledge:read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await check_rate_limit(context, "retrieve")
    started = time.perf_counter()
    retrieval = RetrievalService(session)
    # 先解析发布修订，确保新建草稿无法通过运行面绕过发布流程。
    profile = await IntelligenceRepository(session).get_published_retrieval_profile_by_code(
        context, payload.profile
    )
    if profile is None:
        raise NotFoundError("检索策略不存在、未启用或尚未发布")
    operations = OperationRepository(session)
    trace = await operations.create_trace(
        context,
        request_id=get_request_id(),
        operation="retrieve",
        profile_code=profile.code,
        query_digest=hashlib.sha256(payload.query.encode("utf-8")).hexdigest(),
        retrieval_profile_revision_id=profile.revision_id,
    )
    try:
        hits = await retrieval.search(context, profile, payload.query, top_k=payload.top_k)
        elapsed = round((time.perf_counter() - started) * 1000)
        await operations.replace_evidence(context, trace, hits)
        await operations.finish_trace(
            trace,
            status="succeeded",
            evidence_count=len(hits),
            total_ms=elapsed,
            retrieval_ms=elapsed,
        )
        await session.commit()
        return success(
            {
                "query": payload.query,
                "hits": [
                    {
                        "chunkId": str(hit.chunk_id),
                        "documentId": str(hit.document_id),
                        "title": hit.title,
                        "content": hit.content,
                        "score": hit.score,
                        "vectorScore": hit.vector_score,
                        "lexicalScore": hit.lexical_score,
                        "citation": hit.citation,
                    }
                    for hit in hits
                ],
                "totalHits": len(hits),
                "elapsedMs": elapsed,
            }
        )
    except Exception as exc:
        await session.rollback()
        failed_trace = await session.get(type(trace), trace.id)
        if failed_trace:
            await operations.finish_trace(
                failed_trace,
                status="failed",
                error_code=type(exc).__name__,
                total_ms=round((time.perf_counter() - started) * 1000),
                retrieval_ms=round((time.perf_counter() - started) * 1000),
            )
            await session.commit()
        raise


@router.post(
    "/answer",
    response_model=ApiSuccess[AnswerResponse],
    response_model_exclude_none=True,
)
async def answer(
    payload: AnswerRequest,
    context: Annotated[ApplicationContext, Depends(require_scopes("answer:run"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await check_rate_limit(context, "answer")
    return success(await AnswerService(session).answer(context, payload))


@router.post("/feedback", response_model=ApiSuccess[FeedbackAcceptedResponse])
async def submit_feedback(
    payload: FeedbackRequest,
    context: Annotated[ApplicationContext, Depends(require_scopes("feedback:write"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    operations = OperationRepository(session)
    trace = await operations.get_trace_by_request_id(context, payload.request_id)
    if not trace:
        raise NotFoundError("请求记录不存在或不属于当前应用环境")
    row = await operations.upsert_feedback(
        context,
        trace,
        rating=payload.rating,
        reason_code=payload.reason_code,
        comment=payload.comment,
    )
    await session.commit()
    return success({"feedbackId": str(row.id), "accepted": True})
