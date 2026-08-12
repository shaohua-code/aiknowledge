from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.control.auth import require_admin
from knowledge_core.domains.applications.repository import ApplicationRepository
from knowledge_core.domains.operations.repository import OperationRepository
from knowledge_core.infrastructure.database import get_session
from knowledge_core.shared.context import ApplicationContext
from knowledge_core.shared.errors import NotFoundError
from knowledge_core.shared.response import success

router = APIRouter(
    prefix="/control/v1/applications/{application_id}/environments/{environment_id}",
    tags=["运行与质量"],
    dependencies=[Depends(require_admin)],
)


async def _ensure_environment(
    session: AsyncSession, application_id: UUID, environment_id: UUID
) -> None:
    if not await ApplicationRepository(session).get_environment(application_id, environment_id):
        raise NotFoundError()


@router.get("/operations/summary")
async def get_summary(
    application_id: UUID,
    environment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _ensure_environment(session, application_id, environment_id)
    return success(await OperationRepository(session).summary(application_id, environment_id))


@router.get("/operations/traces")
async def list_traces(
    application_id: UUID,
    environment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    status: Annotated[str | None, Query()] = None,
    error_code: Annotated[str | None, Query(alias="errorCode")] = None,
    from_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_at: Annotated[datetime | None, Query(alias="to")] = None,
) -> dict:
    await _ensure_environment(session, application_id, environment_id)
    rows = await OperationRepository(session).list_traces(
        application_id,
        environment_id,
        status=status,
        error_code=error_code,
        from_at=from_at,
        to_at=to_at,
    )
    return success(
        [
            {
                "id": str(row.id),
                "requestId": row.request_id,
                "operation": row.operation,
                "profileCode": row.profile_code,
                "status": row.status,
                "answerMode": row.answer_mode,
                "confidence": row.confidence,
                "evidenceCount": row.evidence_count,
                "degraded": row.degraded,
                "degradedReasons": row.degraded_reasons,
                "totalMs": row.total_ms,
                "inputTokens": row.input_tokens,
                "outputTokens": row.output_tokens,
                "errorCode": row.error_code,
                "createdAt": row.created_at.isoformat(),
            }
            for row in rows
        ]
    )


@router.get("/operations/traces/{request_id}")
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
            "status": trace.status,
            "answerMode": trace.answer_mode,
            "confidence": trace.confidence,
            "degraded": trace.degraded,
            "degradedReasons": trace.degraded_reasons,
            "totalMs": trace.total_ms,
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


@router.get("/operations/audit-events")
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
