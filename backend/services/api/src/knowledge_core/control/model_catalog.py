from __future__ import annotations

import time
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import Field, HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.control.auth import require_admin, require_control_permission
from knowledge_core.control.permissions import Permission
from knowledge_core.infrastructure.database import get_session
from knowledge_core.infrastructure.model_catalog import (
    ModelCatalogEntry,
    catalog_entry_view,
)
from knowledge_core.infrastructure.model_registry_repository import ModelRegistryRepository
from knowledge_core.infrastructure.model_resolver import (
    ResolvedModelConfig,
    resolve_entry_config,
)
from knowledge_core.infrastructure.providers import (
    LocalHashEmbeddingProvider,
    OpenAICompatibleChatProvider,
    OpenAICompatibleEmbeddingProvider,
    SerperWebSearchProvider,
)
from knowledge_core.shared.errors import CoreError, NotFoundError
from knowledge_core.shared.response import success
from knowledge_core.shared.schema import ApiSchema, ApiSnakeCaseSchema, ApiSuccess

Capability = Literal["chat", "embedding", "web_search"]
_BINDABLE_CAPABILITIES: tuple[str, ...] = ("chat", "embedding", "web_search")
CAPABILITY_PROVIDERS: dict[str, set[str]] = {
    "chat": {"openai", "openai_compatible"},
    "embedding": {"local_hash", "openai", "openai_compatible"},
    "web_search": {"serper"},
}

router = APIRouter(
    prefix="/control/v1",
    tags=["模型目录与应用绑定"],
    dependencies=[
        Depends(require_admin),
        Depends(require_control_permission(Permission.MODEL_READ, Permission.MODEL_WRITE)),
    ],
)


class ModelCatalogEntryCreate(ApiSchema):
    code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=120)
    capability: Capability
    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(default="", max_length=160)
    base_url: HttpUrl
    api_key_env: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    enabled: bool = True
    note: str | None = Field(default=None, max_length=500)


class ModelCatalogEntryUpdate(ApiSchema):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    capability: Capability | None = None
    provider: str | None = Field(default=None, min_length=1, max_length=40)
    model: str | None = Field(default=None, max_length=160)
    base_url: HttpUrl | None = None
    api_key_env: str | None = Field(
        default=None, min_length=1, max_length=120, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"
    )
    enabled: bool | None = None
    note: str | None = Field(default=None, max_length=500)


class ModelBindingUpdate(ApiSchema):
    chat: str | None = Field(default=None, max_length=80)
    embedding: str | None = Field(default=None, max_length=80)
    web_search: str | None = Field(default=None, max_length=80)


class ModelCatalogEntryResponse(ApiSnakeCaseSchema):
    """模型目录公开视图；只暴露密钥是否已配置，绝不返回密钥本身。"""

    code: str
    name: str
    capability: Capability
    provider: str
    model: str
    base_url: str
    api_key_env: str
    enabled: bool
    note: str | None
    api_key_configured: bool


class ModelCatalogListResponse(ApiSnakeCaseSchema):
    """模型目录列表的稳定响应结构。"""

    items: list[ModelCatalogEntryResponse]
    total: int


class ModelCatalogTestResponse(ApiSnakeCaseSchema):
    """模型连通性探针的公开结果，不包含供应商原始错误或凭据。"""

    ok: bool
    message: str
    latency_ms: int


class ModelBindingResponse(ApiSnakeCaseSchema):
    """应用各能力到模型目录编码的绑定视图。"""

    chat: str | None = None
    embedding: str | None = None
    web_search: str | None = None


class ModelCatalogDeletionResponse(ApiSnakeCaseSchema):
    """删除成功后的最小确认信息。"""

    deleted: bool
    code: str


def _validate_provider(capability: str, provider: str) -> None:
    allowed = CAPABILITY_PROVIDERS.get(capability, set())
    if provider not in allowed:
        raise CoreError(
            f"能力 {capability} 不支持 Provider {provider}，可选：{'、'.join(sorted(allowed))}"
        )


def _binding_view(binding: dict[str, str]) -> dict[str, Any]:
    return {capability: binding.get(capability) for capability in _BINDABLE_CAPABILITIES}


@router.get(
    "/model-catalog",
    response_model=ApiSuccess[ModelCatalogListResponse],
)
async def list_model_catalog(
    session: Annotated[AsyncSession, Depends(get_session)],
    capability: Capability | None = None,
) -> dict:
    """控制面直接读取结构化注册表，不再把新配置写回旧 JSON。"""
    entries = await ModelRegistryRepository(session).list_entries(capability)
    return success(
        {
            "items": [catalog_entry_view(entry) for entry in entries],
            "total": len(entries),
        }
    )


@router.post(
    "/model-catalog",
    response_model=ApiSuccess[ModelCatalogEntryResponse],
)
async def create_model_catalog_entry(
    payload: ModelCatalogEntryCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    values = payload.model_dump(mode="json")
    _validate_provider(values["capability"], values["provider"])
    entry = await ModelRegistryRepository(session).create_entry(ModelCatalogEntry(**values))
    await session.commit()
    return success(catalog_entry_view(entry))


@router.put(
    "/model-catalog/{code}",
    response_model=ApiSuccess[ModelCatalogEntryResponse],
)
async def update_model_catalog_entry(
    code: str,
    payload: ModelCatalogEntryUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    repository = ModelRegistryRepository(session)
    existing = await repository.get_entry(code)
    if existing is None:
        raise NotFoundError(f"模型目录中不存在编码为 {code} 的模型")
    values = payload.model_dump(mode="json", exclude_unset=True)
    _validate_provider(
        str(values.get("capability") or existing.capability),
        str(values.get("provider") or existing.provider),
    )
    entry = await repository.update_entry(code, values)
    await session.commit()
    return success(catalog_entry_view(entry))


@router.delete(
    "/model-catalog/{code}",
    response_model=ApiSuccess[ModelCatalogDeletionResponse],
)
async def delete_model_catalog_entry(
    code: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    # 被应用绑定或平台默认引用时，仓储会以 ConflictError 拒绝删除。
    await ModelRegistryRepository(session).delete_entry(code)
    await session.commit()
    return success({"deleted": True, "code": code})


async def _exercise_entry(entry: ModelCatalogEntry, configuration: ResolvedModelConfig) -> None:
    """对目录条目发起一次真实的最小调用，验证密钥与连通性。"""
    if entry.capability == "chat":
        # 平台 chat provider 强制 response_format=json_object，阿里云 compatible-mode
        # 要求消息中必须出现 "json" 字样，否则直接 400，因此探针消息必须带 JSON 语义
        await OpenAICompatibleChatProvider(configuration).generate(
            [{"role": "user", "content": '请用 JSON 格式回复 {"ok": true}'}]
        )
    elif entry.capability == "embedding":
        provider = (
            LocalHashEmbeddingProvider()
            if configuration.provider == "local_hash"
            else OpenAICompatibleEmbeddingProvider(configuration)
        )
        await provider.embed(["测试"])
    else:
        await SerperWebSearchProvider(configuration).search("测试", limit=1)


@router.post(
    "/model-catalog/{code}/test",
    response_model=ApiSuccess[ModelCatalogTestResponse],
)
async def test_model_catalog_entry(
    code: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    entry = await ModelRegistryRepository(session).get_entry(code)
    if entry is None:
        raise NotFoundError(f"模型目录中不存在编码为 {code} 的模型")
    started = time.perf_counter()
    try:
        configuration = resolve_entry_config(entry)
        await _exercise_entry(entry, configuration)
        ok, message = True, "调用成功，模型可用"
    except CoreError as exc:
        ok, message = False, exc.message
    except Exception as exc:  # noqa: BLE001
        ok, message = False, f"调用失败：{exc}"
    return success(
        {
            "ok": ok,
            "message": message,
            "latency_ms": round((time.perf_counter() - started) * 1000),
        }
    )


@router.get(
    "/applications/{application_id}/model-binding",
    response_model=ApiSuccess[ModelBindingResponse],
)
async def read_model_binding(
    application_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    binding = await ModelRegistryRepository(session).read_application_binding(application_id)
    return success(_binding_view(binding))


@router.put(
    "/applications/{application_id}/model-binding",
    response_model=ApiSuccess[ModelBindingResponse],
)
async def update_model_binding(
    application_id: UUID,
    payload: ModelBindingUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    values = payload.model_dump()
    # 仓储在一次事务内校验目录能力、启用状态和应用归属；空值表示解除绑定。
    binding = await ModelRegistryRepository(session).replace_application_binding(
        application_id, values
    )
    await session.commit()
    return success(_binding_view(binding))


@router.get(
    "/applications/{application_id}/environments/{environment_id}/model-binding",
    response_model=ApiSuccess[ModelBindingResponse],
)
async def read_environment_model_binding(
    application_id: UUID,
    environment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """返回当前环境自身的覆盖，不把应用默认伪装成环境级显式配置。"""
    binding = await ModelRegistryRepository(session).read_environment_binding(
        application_id, environment_id
    )
    return success(_binding_view(binding))


@router.put(
    "/applications/{application_id}/environments/{environment_id}/model-binding",
    response_model=ApiSuccess[ModelBindingResponse],
)
async def update_environment_model_binding(
    application_id: UUID,
    environment_id: UUID,
    payload: ModelBindingUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """写入环境覆盖；空值只解除该环境的覆盖以恢复应用默认解析。"""
    binding = await ModelRegistryRepository(session).replace_environment_binding(
        application_id, environment_id, payload.model_dump()
    )
    await session.commit()
    return success(_binding_view(binding))


@router.get(
    "/platform/model-defaults",
    response_model=ApiSuccess[ModelBindingResponse],
)
async def read_platform_model_defaults(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """读取数据库平台默认绑定，空字段表示该能力仍依赖兼容配置回退。"""
    binding = await ModelRegistryRepository(session).read_platform_defaults()
    return success(_binding_view(binding))


@router.put(
    "/platform/model-defaults",
    response_model=ApiSuccess[ModelBindingResponse],
)
async def update_platform_model_defaults(
    payload: ModelBindingUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """将启用目录条目设为平台默认；空值只解除对应能力的数据库默认。"""
    binding = await ModelRegistryRepository(session).replace_platform_defaults(
        payload.model_dump()
    )
    await session.commit()
    return success(_binding_view(binding))
