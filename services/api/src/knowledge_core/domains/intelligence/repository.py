from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.infrastructure.models import AnswerProfile, RetrievalProfile
from knowledge_core.shared.context import ApplicationContext


class IntelligenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_retrieval_profiles(
        self, application_id: UUID, environment_id: UUID
    ) -> list[RetrievalProfile]:
        result = await self.session.scalars(
            select(RetrievalProfile)
            .where(
                RetrievalProfile.application_id == application_id,
                RetrievalProfile.environment_id == environment_id,
            )
            .order_by(RetrievalProfile.created_at.desc())
        )
        return list(result)

    async def list_answer_profiles(
        self, application_id: UUID, environment_id: UUID
    ) -> list[AnswerProfile]:
        result = await self.session.scalars(
            select(AnswerProfile)
            .where(
                AnswerProfile.application_id == application_id,
                AnswerProfile.environment_id == environment_id,
            )
            .order_by(AnswerProfile.created_at.desc())
        )
        return list(result)

    async def get_retrieval_profile_by_code(
        self, context: ApplicationContext, code: str
    ) -> RetrievalProfile | None:
        return await self.session.scalar(
            select(RetrievalProfile).where(
                RetrievalProfile.application_id == context.application_id,
                RetrievalProfile.environment_id == context.environment_id,
                RetrievalProfile.code == code,
                RetrievalProfile.status == "active",
            )
        )

    async def get_answer_profile_by_code(
        self, context: ApplicationContext, code: str
    ) -> AnswerProfile | None:
        return await self.session.scalar(
            select(AnswerProfile).where(
                AnswerProfile.application_id == context.application_id,
                AnswerProfile.environment_id == context.environment_id,
                AnswerProfile.code == code,
                AnswerProfile.status == "active",
            )
        )

    async def get_retrieval_profile(
        self, application_id: UUID, environment_id: UUID, profile_id: UUID
    ) -> RetrievalProfile | None:
        return await self.session.scalar(
            select(RetrievalProfile).where(
                RetrievalProfile.application_id == application_id,
                RetrievalProfile.environment_id == environment_id,
                RetrievalProfile.id == profile_id,
            )
        )

    async def create_retrieval_profile(self, **values: object) -> RetrievalProfile:
        row = RetrievalProfile(**values)
        self.session.add(row)
        await self.session.flush()
        return row

    async def create_answer_profile(self, **values: object) -> AnswerProfile:
        row = AnswerProfile(**values)
        self.session.add(row)
        await self.session.flush()
        return row
