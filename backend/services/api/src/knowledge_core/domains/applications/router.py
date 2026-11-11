from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.control.auth import (
    require_admin,
    require_control_permission,
    require_permission,
)
from knowledge_core.control.permissions import Permission
from knowledge_core.domains.applications.repository import ApplicationRepository
from knowledge_core.domains.applications.schemas import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyRevocationResponse,
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
from knowledge_core.shared.schema import ApiSuccess

router = APIRouter(
    prefix="/control/v1/applications",
    tags=["应用与环境"],
    dependencies=[
        Depends(require_admin),
        Depends(
            require_control_permission(
                Permission.APPLICATION_READ,
                Permission.APPLICATION_WRITE,
                delete_permission=Permission.API_KEY_REVOKE,
            )
        ),
    ],
)


async def _view(repository: ApplicationRepository, app) -> dict:
    """组装应用和环境的完整读取视图，保证公开 Schema 的必填字段实际存在。"""
    environments = await repository.list_environments(app.id)
    view = ApplicationView(
        id=app.id,
        code=app.code,
        name=app.name,
        description=app.description,
        application_type=app.application_type,
        status=app.status,
        created_at=app.created_at,
        updated_at=app.updated_at,
        environments=[EnvironmentView.model_validate(item) for item in environments],
    )
    return view.model_dump(by_alias=True)


@router.get("", response_model=ApiSuccess[list[ApplicationView]])
async def list_applications(session: Annotated[AsyncSession, Depends(get_session)]) -> dict:
    repository = ApplicationRepository(session)
    applications = await repository.list_applications()
    return success([await _view(repository, app) for app in applications])


@router.post("", status_code=201, response_model=ApiSuccess[ApplicationView])
async def create_application(
    payload: ApplicationCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    service = ApplicationService(session)
    app = await service.create(payload)
    return success(await _view(service.repository, app))


@router.get("/{application_id}", response_model=ApiSuccess[ApplicationView])
async def get_application(
    application_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    repository = ApplicationRepository(session)
    app = await repository.get_application(application_id)
    if not app:
        raise NotFoundError()
    return success(await _view(repository, app))


@router.patch("/{application_id}", response_model=ApiSuccess[ApplicationView])
async def update_application(
    application_id: UUID,
    payload: ApplicationUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    service = ApplicationService(session)
    app = await service.update(application_id, payload)
    return success(await _view(service.repository, app))


@router.patch(
    "/{application_id}/environments/{environment_id}",
    response_model=ApiSuccess[EnvironmentView],
)
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


@router.get(
    "/{application_id}/environments/{environment_id}/api-keys",
    response_model=ApiSuccess[list[ApiKeyView]],
)
async def list_api_keys(
    application_id: UUID,
    environment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[object, Depends(require_permission(Permission.API_KEY_READ))],
) -> dict:
    repository = ApplicationRepository(session)
    if not await repository.get_environment(application_id, environment_id):
        raise NotFoundError()
    keys = await repository.list_api_keys(application_id, environment_id)
    return success([ApiKeyView.model_validate(key).model_dump(by_alias=True) for key in keys])


@router.post(
    "/{application_id}/environments/{environment_id}/api-keys",
    status_code=201,
    response_model=ApiSuccess[ApiKeyCreated],
)
async def create_api_key(
    application_id: UUID,
    environment_id: UUID,
    payload: ApiKeyCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[object, Depends(require_permission(Permission.API_KEY_WRITE))],
) -> dict:
    key, raw = await ApplicationService(session).create_api_key(
        application_id, environment_id, payload
    )
    # 先按不含 secret 的视图模型序列化 ORM 行，再补一次性明文 secret 组装完整响应
    # （直接 model_validate(ORM) 会因 ORM 无 secret 属性而抛 ValidationError）
    data = ApiKeyCreated(**ApiKeyView.model_validate(key).model_dump(), secret=raw).model_dump(
        by_alias=True
    )
    return success(data)


@router.delete(
    "/{application_id}/environments/{environment_id}/api-keys/{key_id}",
    response_model=ApiSuccess[ApiKeyRevocationResponse],
)
async def revoke_api_key(
    application_id: UUID,
    environment_id: UUID,
    key_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[object, Depends(require_permission(Permission.API_KEY_REVOKE))],
) -> dict:
    repository = ApplicationRepository(session)
    key = await repository.revoke_api_key(application_id, environment_id, key_id)
    if not key:
        raise NotFoundError()
    await session.commit()
    return success({"revoked": True, "id": str(key.id)})
