from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.infrastructure.models import (
    AuditEvent,
    EvidenceRecord,
    IngestionAttempt,
    IngestionRun,
    RequestTrace,
    UserFeedback,
)
from knowledge_core.shared.context import ApplicationContext


class OperationRepository:
    """运行轨迹、证据、反馈和审计事件的应用环境隔离持久化入口。"""

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
        answer_profile_revision_id: UUID | None = None,
        retrieval_profile_revision_id: UUID | None = None,
    ) -> RequestTrace:
        trace = RequestTrace(
            application_id=context.application_id,
            environment_id=context.environment_id,
            request_id=request_id,
            operation=operation,
            profile_code=profile_code,
            answer_profile_revision_id=answer_profile_revision_id,
            retrieval_profile_revision_id=retrieval_profile_revision_id,
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
        retrieval_ms: int | None = None,
        model_ms: int | None = None,
        web_ms: int | None = None,
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
        trace.retrieval_ms = retrieval_ms
        trace.model_ms = model_ms
        trace.web_ms = web_ms
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
        min_total_ms: int | None = None,
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
        # 慢请求过滤在数据库完成，避免控制台只拿到最新 100 条后误判历史慢请求不存在。
        if min_total_ms is not None:
            statement = statement.where(RequestTrace.total_ms >= min_total_ms)
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

    async def list_feedback(
        self, application_id: UUID, environment_id: UUID, *, limit: int = 100
    ) -> list[tuple[UserFeedback, str]]:
        """读取当前环境反馈及请求标识，避免管理端通过反馈跨环境关联轨迹。"""
        result = await self.session.execute(
            select(UserFeedback, RequestTrace.request_id)
            .join(
                RequestTrace,
                (RequestTrace.id == UserFeedback.trace_id)
                & (RequestTrace.application_id == UserFeedback.application_id)
                & (RequestTrace.environment_id == UserFeedback.environment_id),
            )
            .where(
                UserFeedback.application_id == application_id,
                UserFeedback.environment_id == environment_id,
            )
            .order_by(UserFeedback.created_at.desc())
            .limit(limit)
        )
        return list(result.tuples())

    async def list_ingestion_attempts(
        self,
        application_id: UUID,
        environment_id: UUID,
        *,
        run_id: UUID | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[tuple[IngestionAttempt, str]]:
        """按应用环境读取入库尝试，并从同一上下文带出请求标识。"""
        statement = (
            select(IngestionAttempt, IngestionRun.request_id)
            .join(
                IngestionRun,
                (IngestionRun.id == IngestionAttempt.run_id)
                & (IngestionRun.application_id == IngestionAttempt.application_id)
                & (IngestionRun.environment_id == IngestionAttempt.environment_id),
            )
            .where(
                IngestionAttempt.application_id == application_id,
                IngestionAttempt.environment_id == environment_id,
            )
        )
        if run_id is not None:
            statement = statement.where(IngestionAttempt.run_id == run_id)
        if status:
            statement = statement.where(IngestionAttempt.status == status)
        result = await self.session.execute(
            statement.order_by(IngestionAttempt.created_at.desc()).limit(limit)
        )
        return list(result.tuples())

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

    async def summary(
        self,
        application_id: UUID,
        environment_id: UUID,
        *,
        window_started_at: datetime,
        window_ended_at: datetime,
    ) -> dict:
        """按明确时间窗口统计轨迹，运行中的请求不参与成功率分母。"""
        # 所有指标共用应用、环境和时间范围，防止概览卡片互相使用不同数据集。
        filters = (
            RequestTrace.application_id == application_id,
            RequestTrace.environment_id == environment_id,
            RequestTrace.created_at >= window_started_at,
            RequestTrace.created_at <= window_ended_at,
        )
        total = int(
            await self.session.scalar(
                select(func.count(RequestTrace.id)).where(*filters)
            )
            or 0
        )
        completed = int(
            await self.session.scalar(
                select(func.count(RequestTrace.id)).where(
                    *filters,
                    RequestTrace.status.in_(("succeeded", "failed")),
                )
            )
            or 0
        )
        failed = int(
            await self.session.scalar(
                select(func.count(RequestTrace.id)).where(
                    *filters,
                    RequestTrace.status == "failed",
                )
            )
            or 0
        )
        running = int(
            await self.session.scalar(
                select(func.count(RequestTrace.id)).where(
                    *filters,
                    RequestTrace.status == "running",
                )
            )
            or 0
        )
        avg_ms = float(
            await self.session.scalar(
                select(func.coalesce(func.avg(RequestTrace.total_ms), 0)).where(
                    *filters,
                    RequestTrace.status == "succeeded",
                )
            )
            or 0
        )
        fallback = int(
            await self.session.scalar(
                select(func.count(RequestTrace.id)).where(
                    *filters,
                    RequestTrace.status == "succeeded",
                    RequestTrace.answer_mode == "MODEL_ONLY",
                )
            )
            or 0
        )
        return {
            "totalRequests": total,
            "completedRequests": completed,
            # 显式统计 running，未来新增 cancelled 等终态不能被“总数减完成数”误算。
            "runningRequests": running,
            "failedRequests": failed,
            # 没有已完成请求时不存在技术成功率，不能以 100% 或 0% 伪装为真实结果。
            "successRate": round((completed - failed) / completed, 4) if completed else None,
            "averageDurationMs": round(avg_ms) if completed else None,
            "modelFallbackRate": round(fallback / completed, 4) if completed else None,
            "windowStartedAt": window_started_at.isoformat(),
            "windowEndedAt": window_ended_at.isoformat(),
        }
