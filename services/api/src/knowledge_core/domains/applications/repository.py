from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.infrastructure.models import (
    Application,
    ApplicationApiKey,
    ApplicationEnvironment,
)


class ApplicationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_applications(self) -> list[Application]:
        result = await self.session.scalars(
            select(Application).order_by(Application.created_at.desc())
        )
        return list(result)

    async def get_application(self, application_id: UUID) -> Application | None:
        return await self.session.get(Application, application_id)

    async def get_application_by_code(self, code: str) -> Application | None:
        return await self.session.scalar(select(Application).where(Application.code == code))

    async def list_environments(self, application_id: UUID) -> list[ApplicationEnvironment]:
        result = await self.session.scalars(
            select(ApplicationEnvironment)
            .where(ApplicationEnvironment.application_id == application_id)
            .order_by(ApplicationEnvironment.code)
        )
        return list(result)

    async def get_environment(
        self, application_id: UUID, environment_id: UUID
    ) -> ApplicationEnvironment | None:
        return await self.session.scalar(
            select(ApplicationEnvironment).where(
                ApplicationEnvironment.id == environment_id,
                ApplicationEnvironment.application_id == application_id,
            )
        )

    async def create_application(self, **values: object) -> Application:
        application = Application(**values)
        self.session.add(application)
        await self.session.flush()
        return application

    async def create_environment(self, **values: object) -> ApplicationEnvironment:
        environment = ApplicationEnvironment(**values)
        self.session.add(environment)
        await self.session.flush()
        return environment

    async def create_api_key(self, **values: object) -> ApplicationApiKey:
        key = ApplicationApiKey(**values)
        self.session.add(key)
        await self.session.flush()
        return key

    async def list_api_keys(
        self, application_id: UUID, environment_id: UUID
    ) -> list[ApplicationApiKey]:
        result = await self.session.scalars(
            select(ApplicationApiKey)
            .where(
                ApplicationApiKey.application_id == application_id,
                ApplicationApiKey.environment_id == environment_id,
            )
            .order_by(ApplicationApiKey.created_at.desc())
        )
        return list(result)

    async def revoke_api_key(
        self, application_id: UUID, environment_id: UUID, key_id: UUID
    ) -> ApplicationApiKey | None:
        key = await self.session.scalar(
            select(ApplicationApiKey).where(
                ApplicationApiKey.id == key_id,
                ApplicationApiKey.application_id == application_id,
                ApplicationApiKey.environment_id == environment_id,
            )
        )
        if key:
            key.status = "revoked"
            key.updated_at = datetime.now(UTC)
        return key
