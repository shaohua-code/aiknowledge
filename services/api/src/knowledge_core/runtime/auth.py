from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.infrastructure.database import get_session
from knowledge_core.infrastructure.models import (
    Application,
    ApplicationApiKey,
    ApplicationEnvironment,
)
from knowledge_core.shared.context import ApplicationContext
from knowledge_core.shared.errors import AuthenticationError
from knowledge_core.shared.security import verify_secret


async def get_application_context(
    session: Annotated[AsyncSession, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> ApplicationContext:
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthenticationError("请求缺少 Bearer API Key")
    raw_key = authorization.removeprefix("Bearer ").strip()
    if not raw_key.startswith("aik_") or len(raw_key) < 24:
        raise AuthenticationError("API Key 格式无效")

    prefix = raw_key[:20]
    statement = (
        select(ApplicationApiKey, Application, ApplicationEnvironment)
        .join(Application, Application.id == ApplicationApiKey.application_id)
        .join(
            ApplicationEnvironment,
            (ApplicationEnvironment.id == ApplicationApiKey.environment_id)
            & (ApplicationEnvironment.application_id == ApplicationApiKey.application_id),
        )
        .where(
            ApplicationApiKey.key_prefix == prefix,
            ApplicationApiKey.status == "active",
            Application.status == "active",
            ApplicationEnvironment.status == "active",
        )
    )
    rows = (await session.execute(statement)).all()
    now = datetime.now(UTC)
    for key, application, environment in rows:
        if key.expires_at and key.expires_at <= now:
            continue
        if not verify_secret(raw_key, key.key_hash):
            continue
        key.last_used_at = now
        await session.commit()
        return ApplicationContext(
            application_id=application.id,
            environment_id=environment.id,
            application_code=application.code,
            environment_code=environment.code,
            api_key_id=key.id,
            scopes=frozenset(key.scopes),
        )
    raise AuthenticationError("API Key 无效、已过期或已被吊销")


def require_scopes(*scopes: str):
    async def dependency(
        context: Annotated[ApplicationContext, Depends(get_application_context)],
    ) -> ApplicationContext:
        context.require(*scopes)
        return context

    return dependency
