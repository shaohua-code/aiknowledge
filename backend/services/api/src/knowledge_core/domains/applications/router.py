from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.control.auth import require_admin
from knowledge_core.domains.applications.repository import ApplicationRepository
from knowledge_core.domains.applications.schemas import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyView,
    ApplicationCreate,
    ApplicationUpdate,
    ApplicationView,
    EnvironmentUpdate,
    EnvironmentView,
)
from knowledge_core.domains.applications.service import ApplicationService
from knowledge_core.infrastructure.database import get_session
from knowledge_core.shared.errors import NotFoundError
from knowledge_core.shared.response import success

router = APIRouter(
    prefix="/control/v1/applications",
    tags=["应用与环境"],
    dependencies=[Depends(require_admin)],
)


async def _view(repository: ApplicationRepository, app) -> dict:
    environments = await repository.list_environments(app.id)
    data = ApplicationView.model_validate(app).model_dump(by_alias=True)
    data["environments"] = [
        EnvironmentView.model_validate(item).model_dump(by_alias=True) for item in environments
    ]
    return data


@router.get("")
async def list_applications(session: Annotated[AsyncSession, Depends(get_session)]) -> dict:
    repository = ApplicationRepository(session)
    applications = await repository.list_applications()
    return success([await _view(repository, app) for app in applications])


@router.post("", status_code=201)
async def create_application(
    payload: ApplicationCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    service = ApplicationService(session)
    app = await service.create(payload)
    return success(await _view(service.repository, app))


@router.get("/{application_id}")
async def get_application(
    application_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    repository = ApplicationRepository(session)
    app = await repository.get_application(application_id)
    if not app:
        raise NotFoundError()
    return success(await _view(repository, app))


@router.patch("/{application_id}")
async def update_application(
    application_id: UUID,
    payload: ApplicationUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    service = ApplicationService(session)
    app = await service.update(application_id, payload)
    return success(await _view(service.repository, app))


@router.patch("/{application_id}/environments/{environment_id}")
async def update_environment(
    application_id: UUID,
    environment_id: UUID,
    payload: EnvironmentUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    repository = ApplicationRepository(session)
    environment = await repository.get_environment(application_id, environment_id)
    if not environment:
        raise NotFoundError()
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(environment, key, value)
    await session.commit()
    await session.refresh(environment)
    return success(EnvironmentView.model_validate(environment).model_dump(by_alias=True))


@router.get("/{application_id}/environments/{environment_id}/api-keys")
async def list_api_keys(
    application_id: UUID,
    environment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    repository = ApplicationRepository(session)
    if not await repository.get_environment(application_id, environment_id):
        raise NotFoundError()
    keys = await repository.list_api_keys(application_id, environment_id)
    return success([ApiKeyView.model_validate(key).model_dump(by_alias=True) for key in keys])


@router.post("/{application_id}/environments/{environment_id}/api-keys", status_code=201)
async def create_api_key(
    application_id: UUID,
    environment_id: UUID,
    payload: ApiKeyCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    key, raw = await ApplicationService(session).create_api_key(
        application_id, environment_id, payload
    )
    data = ApiKeyCreated.model_validate(key).model_dump(by_alias=True)
    data["secret"] = raw
    return success(data)


@router.delete("/{application_id}/environments/{environment_id}/api-keys/{key_id}")
async def revoke_api_key(
    application_id: UUID,
    environment_id: UUID,
    key_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    repository = ApplicationRepository(session)
    key = await repository.revoke_api_key(application_id, environment_id, key_id)
    if not key:
        raise NotFoundError()
    await session.commit()
    return success({"revoked": True, "id": str(key.id)})
