from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.domains.applications.repository import ApplicationRepository
from knowledge_core.domains.applications.schemas import (
    ApiKeyCreate,
    ApplicationCreate,
    ApplicationUpdate,
)
from knowledge_core.shared.errors import ConflictError, NotFoundError
from knowledge_core.shared.security import generate_api_key, hash_secret

DEFAULT_ENVIRONMENTS = (
    ("development", "开发环境"),
    ("testing", "测试环境"),
    ("production", "生产环境"),
)


class ApplicationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = ApplicationRepository(session)

    async def create(self, payload: ApplicationCreate):
        if await self.repository.get_application_by_code(payload.code):
            raise ConflictError("应用编码已存在")
        try:
            app = await self.repository.create_application(**payload.model_dump())
            for code, name in DEFAULT_ENVIRONMENTS:
                await self.repository.create_environment(
                    application_id=app.id,
                    code=code,
                    name=name,
                    status="active",
                )
            await self.session.commit()
            await self.session.refresh(app)
            return app
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("应用或环境编码冲突") from exc

    async def update(self, application_id: UUID, payload: ApplicationUpdate):
        app = await self.repository.get_application(application_id)
        if not app:
            raise NotFoundError()
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(app, key, value)
        await self.session.commit()
        await self.session.refresh(app)
        return app

    async def create_api_key(
        self, application_id: UUID, environment_id: UUID, payload: ApiKeyCreate
    ):
        environment = await self.repository.get_environment(application_id, environment_id)
        if not environment:
            raise NotFoundError()
        raw, prefix = generate_api_key(environment.code)
        key = await self.repository.create_api_key(
            application_id=application_id,
            environment_id=environment_id,
            name=payload.name,
            key_prefix=prefix,
            key_hash=hash_secret(raw),
            scopes=sorted(set(payload.scopes)),
            status="active",
            expires_at=payload.expires_at,
        )
        await self.session.commit()
        await self.session.refresh(key)
        return key, raw
