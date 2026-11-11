from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.control.auth import require_admin, require_control_permission
from knowledge_core.control.permissions import Permission
from knowledge_core.domains.applications.repository import ApplicationRepository
from knowledge_core.domains.operations.repository import OperationRepository
from knowledge_core.domains.operations.schemas import (
    AuditEventView,
    IngestionAttemptView,
    OperationFeedbackView,
    OperationSummaryResponse,
    OperationTraceDetailResponse,
    OperationTraceView,
)
from knowledge_core.infrastructure.database import get_session
from knowledge_core.shared.context import ApplicationContext
from knowledge_core.shared.errors import NotFoundError
from knowledge_core.shared.response import success
from knowledge_core.shared.schema import ApiSuccess

router = APIRouter(
    prefix="/control/v1/applications/{application_id}/environments/{environment_id}",
    tags=["运行与质量"],
    dependencies=[
        Depends(require_admin),
        Depends(
            require_control_permission(Permission.OPERATIONS_READ, Permission.OPERATIONS_EXPORT)
        ),
    ],
)


async def _ensure_environment(
    session: AsyncSession, application_id: UUID, environment_id: UUID
) -> None:
    if not await ApplicationRepository(session).get_environment(application_id, environment_id):
        raise NotFoundError()


@router.get("/operations/summary", response_model=ApiSuccess[OperationSummaryResponse])
async def get_summary(
    application_id: UUID,
    environment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    window_hours: Annotated[int, Query(alias="windowHours", ge=1, le=24 * 30)] = 24,
) -> dict:
    """返回当前应用环境最近固定窗口的可解释运行指标。"""
    await _ensure_environment(session, application_id, environment_id)
    window_ended_at = datetime.now(UTC)
    window_started_at = window_ended_at - timedelta(hours=window_hours)
    return success(
        await OperationRepository(session).summary(
            application_id,
            environment_id,
            window_started_at=window_started_at,
            window_ended_at=window_ended_at,
        )
    )


@router.get("/operations/traces", response_model=ApiSuccess[list[OperationTraceView]])
async def list_traces(
    application_id: UUID,
    environment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    status: Annotated[str | None, Query()] = None,
    error_code: Annotated[str | None, Query(alias="errorCode")] = None,
    from_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_at: Annotated[datetime | None, Query(alias="to")] = None,
    min_total_ms: Annotated[int | None, Query(alias="minTotalMs", ge=0)] = None,
) -> dict:
    """按状态、错误、时间与最小耗时读取轨迹，所有条件仍限定在当前环境。"""
    await _ensure_environment(session, application_id, environment_id)
    rows = await OperationRepository(session).list_traces(
        application_id,
        environment_id,
        status=status,
        error_code=error_code,
        from_at=from_at,
        to_at=to_at,
        min_total_ms=min_total_ms,
    )
    return success(
        [
            {
                "id": str(row.id),
                "requestId": row.request_id,
                "operation": row.operation,
                "profileCode": row.profile_code,
                "answerProfileRevisionId": str(row.answer_profile_revision_id)
                if row.answer_profile_revision_id
                else None,
                "retrievalProfileRevisionId": str(row.retrieval_profile_revision_id)
                if row.retrieval_profile_revision_id
                else None,
                "status": row.status,
                "answerMode": row.answer_mode,
                "confidence": row.confidence,
                "evidenceCount": row.evidence_count,
                "degraded": row.degraded,
                "degradedReasons": row.degraded_reasons,
                "totalMs": row.total_ms,
                "retrievalMs": row.retrieval_ms,
                "modelMs": row.model_ms,
                "webMs": row.web_ms,
                "inputTokens": row.input_tokens,
                "outputTokens": row.output_tokens,
                "errorCode": row.error_code,
                "createdAt": row.created_at.isoformat(),
            }
            for row in rows
        ]
    )


@router.get(
    "/operations/traces/{request_id}", response_model=ApiSuccess[OperationTraceDetailResponse]
)
async def get_trace(
    application_id: UUID,
    environment_id: UUID,
    request_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _ensure_environment(session, application_id, environment_id)
    repository = OperationRepository(session)
    context = ApplicationContext(
        application_id=application_id,
        environment_id=environment_id,
        application_code="control",
        environment_code="control",
        api_key_id=UUID(int=0),
        scopes=frozenset(),
    )
    trace = await repository.get_trace_by_request_id(context, request_id)
    if not trace:
        raise NotFoundError("运行轨迹不存在")
    evidence = await repository.list_evidence(context, trace.id)
    return success(
        {
            "requestId": trace.request_id,
            "operation": trace.operation,
            "profileCode": trace.profile_code,
            "answerProfileRevisionId": str(trace.answer_profile_revision_id)
            if trace.answer_profile_revision_id
            else None,
            "retrievalProfileRevisionId": str(trace.retrieval_profile_revision_id)
            if trace.retrieval_profile_revision_id
            else None,
            "status": trace.status,
            "answerMode": trace.answer_mode,
            "confidence": trace.confidence,
            "degraded": trace.degraded,
            "degradedReasons": trace.degraded_reasons,
            "totalMs": trace.total_ms,
            "retrievalMs": trace.retrieval_ms,
            "modelMs": trace.model_ms,
            "webMs": trace.web_ms,
            "inputTokens": trace.input_tokens,
            "outputTokens": trace.output_tokens,
            "errorCode": trace.error_code,
            "createdAt": trace.created_at.isoformat(),
            "evidence": [
                {
                    "sourceType": item.source_type,
                    "title": item.title,
                    "excerpt": item.excerpt,
                    "score": item.score,
                    "citation": item.citation,
                }
                for item in evidence
            ],
        }
    )


@router.get("/operations/audit-events", response_model=ApiSuccess[list[AuditEventView]])
async def list_audit_events(
    application_id: UUID,
    environment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _ensure_environment(session, application_id, environment_id)
    rows = await OperationRepository(session).list_audit_events(application_id, environment_id)
    return success(
        [
            {
                "id": str(row.id),
                "actor": row.actor,
                "action": row.action,
                "resourceType": row.resource_type,
                "resourceId": str(row.resource_id),
                "requestId": row.request_id,
                "details": row.details,
                "createdAt": row.created_at.isoformat(),
            }
            for row in rows
        ]
    )


@router.get("/operations/feedback", response_model=ApiSuccess[list[OperationFeedbackView]])
async def list_feedback(
    application_id: UUID,
    environment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """管理员查看当前环境的用户反馈，用请求标识回到运行轨迹定位原因。"""
    await _ensure_environment(session, application_id, environment_id)
    rows = await OperationRepository(session).list_feedback(application_id, environment_id)
    return success(
        [
            {
                "id": str(feedback.id),
                "requestId": request_id,
                "rating": feedback.rating,
                "reasonCode": feedback.reason_code,
                "comment": feedback.comment,
                "createdAt": feedback.created_at.isoformat(),
            }
            for feedback, request_id in rows
        ]
    )


@router.get(
    "/operations/ingestion-attempts",
    response_model=ApiSuccess[list[IngestionAttemptView]],
)
async def list_ingestion_attempts(
    application_id: UUID,
    environment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    run_id: Annotated[UUID | None, Query(alias="runId")] = None,
    status: Annotated[str | None, Query()] = None,
) -> dict:
    """显示当前环境入库任务的每次 Worker 尝试，支持回到请求与错误定位。"""
    await _ensure_environment(session, application_id, environment_id)
    rows = await OperationRepository(session).list_ingestion_attempts(
        application_id,
        environment_id,
        run_id=run_id,
        status=status,
    )
    return success(
        [
            {
                "id": str(attempt.id),
                "runId": str(attempt.run_id),
                "requestId": request_id,
                "attemptNumber": attempt.attempt_number,
                "status": attempt.status,
                "stage": attempt.stage,
                "errorCode": attempt.error_code,
                "errorMessage": attempt.error_message,
                "startedAt": attempt.started_at.isoformat(),
                "completedAt": attempt.completed_at.isoformat()
                if attempt.completed_at
                else None,
                "createdAt": attempt.created_at.isoformat(),
            }
            for attempt, request_id in rows
        ]
    )
