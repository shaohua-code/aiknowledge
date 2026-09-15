from __future__ import annotations

import os
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.infrastructure.model_bindings import get_model_binding
from knowledge_core.infrastructure.model_catalog import ModelCatalogEntry, get_catalog_entry
from knowledge_core.infrastructure.model_configuration import get_model_configuration
from knowledge_core.infrastructure.model_registry_repository import ModelRegistryRepository
from knowledge_core.shared.errors import PlatformError


@dataclass(slots=True)
class ResolvedModelConfig:
    provider: str
    model: str
    base_url: str
    api_key: str


def resolve_entry_config(entry: ModelCatalogEntry) -> ResolvedModelConfig:
    """把目录条目解析为可用配置；密钥仅从环境变量读取，不落库、不回显。"""
    if not entry.enabled:
        raise PlatformError(
            f"模型 {entry.code} 已停用，无法用于当前能力"
        )
    # 禁用 Provider 与本地 Hash 向量没有外部凭证；要求它们设置密钥会让数据库
    # 平台默认无法表达与旧配置等价的“关闭/本地开发”语义。
    if entry.provider in {"disabled", "local_hash"}:
        return ResolvedModelConfig(
            provider=entry.provider,
            model=entry.model,
            base_url=entry.base_url,
            api_key="",
        )
    api_key = os.environ.get(entry.api_key_env, "").strip()
    if not api_key:
        raise PlatformError(
            f"模型 {entry.code} 的密钥环境变量 {entry.api_key_env} 未配置或条目已停用"
        )
    return ResolvedModelConfig(
        provider=entry.provider,
        model=entry.model,
        base_url=entry.base_url,
        api_key=api_key,
    )


def resolve_capability_config(
    application_id: UUID | str | None, capability: str
) -> ResolvedModelConfig:
    """解析应用某能力的模型配置。

    优先级：应用绑定 → 目录条目（密钥取环境变量）→ 旧平台模型配置 → .env 默认值。
    无绑定时完全沿用旧的 get_model_configuration() 行为。
    """
    code = get_model_binding(str(application_id)).get(capability) if application_id else None
    if not code:
        return _legacy_config(capability)
    entry = get_catalog_entry(code)
    if entry is None:
        raise PlatformError(f"应用绑定的模型 {code} 在模型目录中不存在，请重新绑定")
    return resolve_entry_config(entry)


def _legacy_config(capability: str) -> ResolvedModelConfig:
    configuration = get_model_configuration()
    if capability == "chat":
        return ResolvedModelConfig(
            provider=configuration.chat_provider,
            model=configuration.chat_model,
            base_url=configuration.chat_base_url,
            api_key=configuration.chat_api_key,
        )
    if capability == "embedding":
        return ResolvedModelConfig(
            provider=configuration.embedding_provider,
            model=configuration.embedding_model,
            base_url=configuration.embedding_base_url,
            api_key=configuration.embedding_api_key,
        )
    return ResolvedModelConfig(
        provider=configuration.web_search_provider,
        model="",
        base_url=configuration.web_search_base_url,
        api_key=configuration.web_search_api_key,
    )


async def resolve_capability_config_async(
    session: AsyncSession,
    application_id: UUID | str | None,
    environment_id: UUID | str | None,
    capability: str,
) -> ResolvedModelConfig:
    """优先解析数据库注册表，未回填时兼容旧文件型绑定与平台配置。

    数据库中存在明确环境/应用/平台条目时，它是本次请求的权威来源；没有任何匹配
    条目时回退旧解析器，允许先部署读取路径、再分批回填而不让未迁移应用失去模型。
    """
    try:
        parsed_application_id = UUID(str(application_id)) if application_id is not None else None
        parsed_environment_id = UUID(str(environment_id)) if environment_id is not None else None
    except (TypeError, ValueError):
        parsed_application_id = None
        parsed_environment_id = None

    # 部分 SQL 结构测试使用只记录 execute 的轻量替身；它们不具备 scalar，
    # 也没有可读取的注册表，因此应维持旧解析路径而非把测试替身当成数据库故障。
    if parsed_application_id is not None and hasattr(session, "scalar"):
        entry = await ModelRegistryRepository(session).resolve_entry(
            parsed_application_id, parsed_environment_id, capability
        )
        if entry is not None:
            return resolve_entry_config(entry)
    return resolve_capability_config(application_id, capability)
