"""评测集与样本修订的持久化操作。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.infrastructure.models import (
    EvaluationCaseResult,
    EvaluationCaseRevision,
    EvaluationRun,
    EvaluationSet,
)


class EvaluationRepository:
    """所有查询均要求应用和环境上下文，防止样本跨环境复用。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_sets(self, application_id: UUID, environment_id: UUID) -> list[EvaluationSet]:
        result = await self.session.scalars(
            select(EvaluationSet)
            .where(
                EvaluationSet.application_id == application_id,
                EvaluationSet.environment_id == environment_id,
            )
            .order_by(EvaluationSet.created_at.desc())
        )
        return list(result)

    async def get_set(
        self, application_id: UUID, environment_id: UUID, set_id: UUID
    ) -> EvaluationSet | None:
        return await self.session.scalar(
            select(EvaluationSet).where(
                EvaluationSet.application_id == application_id,
                EvaluationSet.environment_id == environment_id,
                EvaluationSet.id == set_id,
            )
        )

    async def list_case_revisions(
        self, application_id: UUID, environment_id: UUID, set_id: UUID
    ) -> list[EvaluationCaseRevision]:
        result = await self.session.scalars(
            select(EvaluationCaseRevision)
            .where(
                EvaluationCaseRevision.application_id == application_id,
                EvaluationCaseRevision.environment_id == environment_id,
                EvaluationCaseRevision.evaluation_set_id == set_id,
            )
            .order_by(EvaluationCaseRevision.case_code, EvaluationCaseRevision.revision.desc())
        )
        return list(result)

    async def get_latest_case_revision(
        self, application_id: UUID, environment_id: UUID, set_id: UUID, case_code: str
    ) -> EvaluationCaseRevision | None:
        """读取同一案例当前最高修订，仅用于计算下一不可变版本号。"""
        return await self.session.scalar(
            select(EvaluationCaseRevision)
            .where(
                EvaluationCaseRevision.application_id == application_id,
                EvaluationCaseRevision.environment_id == environment_id,
                EvaluationCaseRevision.evaluation_set_id == set_id,
                EvaluationCaseRevision.case_code == case_code,
            )
            .order_by(EvaluationCaseRevision.revision.desc())
            .limit(1)
        )

    async def list_runs(
        self, application_id: UUID, environment_id: UUID, set_id: UUID
    ) -> list[EvaluationRun]:
        """按最近运行优先返回同一评测集的历史，结果始终受环境约束。"""
        result = await self.session.scalars(
            select(EvaluationRun)
            .where(
                EvaluationRun.application_id == application_id,
                EvaluationRun.environment_id == environment_id,
                EvaluationRun.evaluation_set_id == set_id,
            )
            .order_by(EvaluationRun.started_at.desc(), EvaluationRun.created_at.desc())
        )
        return list(result)

    async def get_run(
        self, application_id: UUID, environment_id: UUID, run_id: UUID
    ) -> EvaluationRun | None:
        """按租户边界读取一次运行，防止结果接口泄漏其他环境的运行标识。"""
        return await self.session.scalar(
            select(EvaluationRun).where(
                EvaluationRun.application_id == application_id,
                EvaluationRun.environment_id == environment_id,
                EvaluationRun.id == run_id,
            )
        )

    async def list_case_results(
        self, application_id: UUID, environment_id: UUID, run_id: UUID
    ) -> list[EvaluationCaseResult]:
        """读取单次运行的逐样本结果，保留创建顺序便于与执行日志对照。"""
        result = await self.session.scalars(
            select(EvaluationCaseResult)
            .where(
                EvaluationCaseResult.application_id == application_id,
                EvaluationCaseResult.environment_id == environment_id,
                EvaluationCaseResult.evaluation_run_id == run_id,
            )
            .order_by(EvaluationCaseResult.created_at.asc())
        )
        return list(result)

    async def has_passing_run(
        self,
        application_id: UUID,
        environment_id: UUID,
        answer_profile_revision_id: UUID,
        retrieval_profile_revision_id: UUID,
    ) -> bool:
        """发布只能消费同环境、同一对策略修订的已完成通过运行。"""
        return bool(
            await self.session.scalar(
                select(
                    exists().where(
                        EvaluationRun.application_id == application_id,
                        EvaluationRun.environment_id == environment_id,
                        EvaluationRun.answer_profile_revision_id == answer_profile_revision_id,
                        EvaluationRun.retrieval_profile_revision_id
                        == retrieval_profile_revision_id,
                        EvaluationRun.status == "succeeded",
                        EvaluationRun.gate_passed.is_(True),
                    )
                )
            )
        )
