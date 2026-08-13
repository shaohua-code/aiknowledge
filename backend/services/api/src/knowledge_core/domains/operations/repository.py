from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.infrastructure.models import (
    AuditEvent,
    EvidenceRecord,
    RequestTrace,
    UserFeedback,
)
from knowledge_core.shared.context import ApplicationContext


class OperationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_trace(
        self,
        context: ApplicationContext,
        *,
        request_id: str,
        operation: str,
        profile_code: str,
        query_digest: str,
    ) -> RequestTrace:
        trace = RequestTrace(
            application_id=context.application_id,
            environment_id=context.environment_id,
            request_id=request_id,
            operation=operation,
            profile_code=profile_code,
            query_digest=query_digest,
            status="running",
        )
        self.session.add(trace)
        await self.session.flush()
        return trace

    async def finish_trace(
        self,
        trace: RequestTrace,
        *,
        status: str,
        answer_mode: str | None = None,
        confidence: float | None = None,
        evidence_count: int = 0,
        degraded: bool = False,
        degraded_reasons: list[str] | None = None,
        total_ms: int | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        error_code: str | None = None,
    ) -> None:
        trace.status = status
        trace.answer_mode = answer_mode
        trace.confidence = confidence
        trace.evidence_count = evidence_count
        trace.degraded = degraded
        trace.degraded_reasons = degraded_reasons or []
        trace.total_ms = total_ms
        trace.input_tokens = input_tokens
        trace.output_tokens = output_tokens
        trace.error_code = error_code
        trace.updated_at = datetime.now(UTC)

    async def replace_evidence(
        self, context: ApplicationContext, trace: RequestTrace, hits: list
    ) -> None:
        self.session.add_all(
            [
                EvidenceRecord(
                    application_id=context.application_id,
                    environment_id=context.environment_id,
                    trace_id=trace.id,
                    source_type="knowledge" if hit.document_id else "web",
                    source_id=hit.document_id,
                    title=hit.title,
                    excerpt=hit.content[:1500],
                    score=hit.score,
                    citation=hit.citation,
                )
                for hit in hits
            ]
        )

    async def list_traces(
        self,
        application_id: UUID,
        environment_id: UUID,
        *,
        status: str | None = None,
        error_code: str | None = None,
        from_at: datetime | None = None,
        to_at: datetime | None = None,
        limit: int = 100,
    ) -> list[RequestTrace]:
        statement = select(RequestTrace).where(
            RequestTrace.application_id == application_id,
            RequestTrace.environment_id == environment_id,
        )
        if status:
            statement = statement.where(RequestTrace.status == status)
        if error_code:
            statement = statement.where(RequestTrace.error_code == error_code)
        if from_at:
            statement = statement.where(RequestTrace.created_at >= from_at)
        if to_at:
            statement = statement.where(RequestTrace.created_at <= to_at)
        result = await self.session.scalars(
            statement.order_by(RequestTrace.created_at.desc()).limit(limit)
        )
        return list(result)

    async def list_evidence(self, context: ApplicationContext, trace_id: UUID):
        result = await self.session.scalars(
            select(EvidenceRecord)
            .where(
                EvidenceRecord.application_id == context.application_id,
                EvidenceRecord.environment_id == context.environment_id,
                EvidenceRecord.trace_id == trace_id,
            )
            .order_by(EvidenceRecord.score.desc())
        )
        return list(result)

    async def list_audit_events(
        self, application_id: UUID, environment_id: UUID, *, limit: int = 100
    ) -> list[AuditEvent]:
        result = await self.session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.application_id == application_id,
                AuditEvent.environment_id == environment_id,
            )
            .order_by(AuditEvent.created_at.desc())
            .limit(limit)
        )
        return list(result)

    async def get_trace_by_request_id(
        self, context: ApplicationContext, request_id: str
    ) -> RequestTrace | None:
        return await self.session.scalar(
            select(RequestTrace).where(
                RequestTrace.application_id == context.application_id,
                RequestTrace.environment_id == context.environment_id,
                RequestTrace.request_id == request_id,
            )
        )

    async def upsert_feedback(
        self,
        context: ApplicationContext,
        trace: RequestTrace,
        *,
        rating: int,
        reason_code: str | None,
        comment: str | None,
    ) -> UserFeedback:
        row = await self.session.scalar(
            select(UserFeedback).where(
                UserFeedback.application_id == context.application_id,
                UserFeedback.environment_id == context.environment_id,
                UserFeedback.trace_id == trace.id,
            )
        )
        if row:
            row.rating = rating
            row.reason_code = reason_code
            row.comment = comment
            return row
        row = UserFeedback(
            application_id=context.application_id,
            environment_id=context.environment_id,
            trace_id=trace.id,
            rating=rating,
            reason_code=reason_code,
            comment=comment,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def summary(self, application_id: UUID, environment_id: UUID) -> dict:
        total = int(
            await self.session.scalar(
                select(func.count(RequestTrace.id)).where(
                    RequestTrace.application_id == application_id,
                    RequestTrace.environment_id == environment_id,
                )
            )
            or 0
        )
        failed = int(
            await self.session.scalar(
                select(func.count(RequestTrace.id)).where(
                    RequestTrace.application_id == application_id,
                    RequestTrace.environment_id == environment_id,
                    RequestTrace.status == "failed",
                )
            )
            or 0
        )
        avg_ms = float(
            await self.session.scalar(
                select(func.coalesce(func.avg(RequestTrace.total_ms), 0)).where(
                    RequestTrace.application_id == application_id,
                    RequestTrace.environment_id == environment_id,
                    RequestTrace.status == "succeeded",
                )
            )
            or 0
        )
        fallback = int(
            await self.session.scalar(
                select(func.count(RequestTrace.id)).where(
                    RequestTrace.application_id == application_id,
                    RequestTrace.environment_id == environment_id,
                    RequestTrace.answer_mode == "MODEL_ONLY",
                )
            )
            or 0
        )
        return {
            "totalRequests": total,
            "failedRequests": failed,
            "successRate": round((total - failed) / total, 4) if total else 1.0,
            "averageDurationMs": round(avg_ms),
            "modelFallbackRate": round(fallback / total, 4) if total else 0.0,
        }
