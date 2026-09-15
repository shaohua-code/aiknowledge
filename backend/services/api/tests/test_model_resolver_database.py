"""数据库优先模型解析器的回退与隔离回归。"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from knowledge_core.infrastructure.model_catalog import ModelCatalogEntry
from knowledge_core.infrastructure.model_registry_repository import ModelRegistryRepository
from knowledge_core.infrastructure.model_resolver import (
    ResolvedModelConfig,
    resolve_capability_config_async,
    resolve_entry_config,
)


@pytest.mark.asyncio
async def test_async_resolver_prefers_database_entry(monkeypatch) -> None:
    """数据库已有环境/应用/平台条目时，运行期必须不再回头读取旧 JSON。"""
    entry = ModelCatalogEntry(
        code="database-chat",
        name="数据库 Chat",
        capability="chat",
        provider="openai_compatible",
        model="database-model",
        base_url="https://example.invalid/v1",
        api_key_env="DATABASE_CHAT_KEY",
        enabled=True,
        note=None,
    )

    async def resolve_entry(_self, _application_id, _environment_id, _capability):
        return entry

    monkeypatch.setattr(ModelRegistryRepository, "resolve_entry", resolve_entry)
    monkeypatch.setenv("DATABASE_CHAT_KEY", "database-secret")
    # 仓储方法已被替换，轻量会话只需声明生产 AsyncSession 使用的 scalar 能力，
    # 不会触发真实数据库操作。
    configuration = await resolve_capability_config_async(
        SimpleNamespace(scalar=None),
        UUID("00000000-0000-0000-0000-000000000501"),
        UUID("00000000-0000-0000-0000-000000000502"),
        "chat",
    )

    assert configuration == ResolvedModelConfig(
        provider="openai_compatible",
        model="database-model",
        base_url="https://example.invalid/v1",
        api_key="database-secret",
    )


@pytest.mark.asyncio
async def test_async_resolver_falls_back_when_registry_has_no_entry(monkeypatch) -> None:
    """尚未回填的应用必须继续解析旧绑定，避免发布新代码时丢失已有模型。"""

    async def resolve_entry(_self, _application_id, _environment_id, _capability):
        return None

    legacy_configuration = ResolvedModelConfig(
        provider="openai_compatible",
        model="legacy-model",
        base_url="https://legacy.example.invalid/v1",
        api_key="legacy-secret",
    )
    monkeypatch.setattr(ModelRegistryRepository, "resolve_entry", resolve_entry)
    monkeypatch.setattr(
        "knowledge_core.infrastructure.model_resolver.resolve_capability_config",
        lambda _application_id, _capability: legacy_configuration,
    )

    configuration = await resolve_capability_config_async(
        SimpleNamespace(scalar=None),
        UUID("00000000-0000-0000-0000-000000000503"),
        None,
        "chat",
    )

    assert configuration is legacy_configuration


def test_directory_config_allows_local_and_disabled_provider_without_secret() -> None:
    """数据库平台默认必须能等价表达本地向量和关闭能力，二者均不应要求密钥。"""
    local = ModelCatalogEntry(
        code="local-embedding",
        name="本地向量",
        capability="embedding",
        provider="local_hash",
        model="",
        base_url="http://local.invalid",
        api_key_env="UNUSED_LOCAL_KEY",
        enabled=True,
        note=None,
    )
    disabled = ModelCatalogEntry(
        code="disabled-chat",
        name="关闭对话",
        capability="chat",
        provider="disabled",
        model="",
        base_url="http://disabled.invalid",
        api_key_env="UNUSED_DISABLED_KEY",
        enabled=True,
        note=None,
    )

    assert resolve_entry_config(local).api_key == ""
    assert resolve_entry_config(disabled).provider == "disabled"
