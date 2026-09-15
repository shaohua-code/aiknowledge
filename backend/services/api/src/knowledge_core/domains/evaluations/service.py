"""使用候选策略修订执行固定评测样本并持久化门禁结论。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.domains.evaluations.gate import evaluate_deterministic_checks, summarize_run
from knowledge_core.domains.intelligence.repository import IntelligenceRepository
from knowledge_core.domains.intelligence.schemas import AnswerRequest
from knowledge_core.domains.intelligence.service import AnswerService
from knowledge_core.infrastructure.models import (
    EvaluationCaseResult,
    EvaluationCaseRevision,
    EvaluationRun,
)
from knowledge_core.shared.context import ApplicationContext


class EvaluationService:
    """评测运行不调用业务 HTTP 接口，直接复用回答服务并固定候选快照。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.intelligence = IntelligenceRepository(session)

    async def run(
        self,
        context: ApplicationContext,
        evaluation_set_id: UUID,
        answer_profile_revision_id: UUID,
    ) -> EvaluationRun:
        """完整执行当前集合的最新样本修订，失败结果同样写入运行历史。"""
        answer_profile = await self.intelligence.get_answer_profile_for_evaluation(
            context, answer_profile_revision_id
        )
        if answer_profile is None:
            raise ValueError("候选回答修订不存在或不可评测")
        retrieval_profile = await self.intelligence.get_retrieval_profile_for_evaluation(
            context, answer_profile.retrieval_revision_id
        )
        if retrieval_profile is None:
            raise ValueError("候选回答修订关联的检索修订不存在或不可评测")
        rows = list(
            await self.session.scalars(
                select(EvaluationCaseRevision)
                .where(
                    EvaluationCaseRevision.application_id == context.application_id,
                    EvaluationCaseRevision.environment_id == context.environment_id,
                    EvaluationCaseRevision.evaluation_set_id == evaluation_set_id,
                )
                .order_by(EvaluationCaseRevision.case_code, EvaluationCaseRevision.revision.desc())
            )
        )
        latest_by_code: dict[str, EvaluationCaseRevision] = {}
        for row in rows:
            latest_by_code.setdefault(row.case_code, row)
        run = EvaluationRun(
            application_id=context.application_id,
            environment_id=context.environment_id,
            evaluation_set_id=evaluation_set_id,
            answer_profile_revision_id=answer_profile.revision_id,
            retrieval_profile_revision_id=retrieval_profile.revision_id,
            status="running",
            started_at=datetime.now(UTC),
            summary={},
        )
        self.session.add(run)
        await self.session.commit()
        await self.session.refresh(run)
        result_summaries: list[dict[str, Any]] = []
        for case in latest_by_code.values():
            try:
                response = await AnswerService(self.session).answer(
                    context,
                    AnswerRequest(
                        profile=answer_profile.code, query=case.question, inputs=case.inputs
                    ),
                    candidate_answer_profile=answer_profile,
                    candidate_retrieval_profile=retrieval_profile,
                )
                citations = [
                    str(item.get("documentId"))
                    for item in response["knowledge"]["citations"]
                    if item.get("documentId")
                ]
                checks = evaluate_deterministic_checks(
                    expected_document_ids=case.expected_document_ids,
                    expected_answer_modes=case.expected_answer_modes,
                    expected_output_schema=case.expected_output_schema,
                    actual_answer_mode=response["answerMode"],
                    structured_output=response["structuredOutput"],
                    citation_document_ids=citations,
                )
                status = "succeeded"
                error_code = None
            except Exception as exc:  # noqa: BLE001
                checks = {"gatePassed": False, "technicalError": type(exc).__name__}
                status = "failed"
                error_code = type(exc).__name__
            result_summaries.append({"status": status, "checks": checks})
            self.session.add(
                EvaluationCaseResult(
                    application_id=context.application_id,
                    environment_id=context.environment_id,
                    evaluation_run_id=run.id,
                    case_revision_id=case.id,
                    status=status,
                    checks=checks,
                    error_code=error_code,
                )
            )
            await self.session.commit()
        summary = summarize_run(result_summaries)
        run = await self.session.get(EvaluationRun, run.id)
        assert run is not None
        run.status = "succeeded"
        run.gate_passed = summary["gatePassed"]
        run.summary = summary
        run.finished_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(run)
        return run
