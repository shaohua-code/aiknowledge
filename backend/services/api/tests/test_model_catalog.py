from __future__ import annotations

import pytest

from knowledge_core.config import settings
from knowledge_core.infrastructure.model_bindings import (
    get_model_binding,
    list_binding_references,
    save_model_binding,
)
from knowledge_core.infrastructure.model_catalog import (
    ModelCatalogEntry,
    catalog_entry_view,
    create_catalog_entry,
    delete_catalog_entry,
    get_catalog_entry,
    list_catalog_entries,
    update_catalog_entry,
)
from knowledge_core.infrastructure.model_configuration import save_model_configuration
from knowledge_core.infrastructure.model_resolver import resolve_capability_config
from knowledge_core.infrastructure.providers import get_chat_provider
from knowledge_core.shared.errors import ConflictError, PlatformError

APPLICATION_ID = "00000000-0000-0000-0000-000000000001"


def _entry(code: str = "demo-chat", **overrides) -> ModelCatalogEntry:
    values = {
        "code": code,
        "name": "演示模型",
        "capability": "chat",
        "provider": "openai_compatible",
        "model": "catalog-chat-model",
        "base_url": "https://models.example.test/v1",
        "api_key_env": "DEMO_CATALOG_CHAT_KEY",
    }
    values.update(overrides)
    return ModelCatalogEntry(**values)


def test_catalog_crud_never_exposes_secrets(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "object_storage_path", str(tmp_path))

    # 未配置环境变量时只报告未配置，不出现任何密钥内容
    monkeypatch.delenv("DEMO_CATALOG_CHAT_KEY", raising=False)
    entry = create_catalog_entry(_entry())
    view = catalog_entry_view(entry)
    assert view["api_key_configured"] is False
    assert "api_key" not in str(view) or "api_key_env" in str(view)

    # 配置环境变量后视图只带 configured 标记，密钥本身绝不回显
    monkeypatch.setenv("DEMO_CATALOG_CHAT_KEY", "super-secret-value")
    view = catalog_entry_view(get_catalog_entry("demo-chat"))
    assert view["api_key_configured"] is True
    assert "super-secret-value" not in str(view)

    # 列表支持按 capability 过滤
    create_catalog_entry(_entry(code="demo-embedding", capability="embedding"))
    assert {item.code for item in list_catalog_entries()} == {"demo-chat", "demo-embedding"}
    assert [item.code for item in list_catalog_entries("chat")] == ["demo-chat"]

    # 更新与删除
    updated = update_catalog_entry("demo-chat", {"note": "更新备注", "enabled": False})
    assert updated.note == "更新备注"
    assert updated.enabled is False
    delete_catalog_entry("demo-embedding")
    assert get_catalog_entry("demo-embedding") is None

    # code 唯一校验
    with pytest.raises(ConflictError):
        create_catalog_entry(_entry())


def test_binding_overrides_legacy_configuration(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "object_storage_path", str(tmp_path))
    save_model_configuration(
        {
            "chat_provider": "openai_compatible",
            "chat_model": "legacy-chat",
            "chat_api_key_env": "LEGACY_TEST_CHAT_KEY",
        }
    )
    monkeypatch.setenv("LEGACY_TEST_CHAT_KEY", "legacy-key")
    create_catalog_entry(_entry())
    monkeypatch.setenv("DEMO_CATALOG_CHAT_KEY", "catalog-secret")
    save_model_binding(APPLICATION_ID, {"chat": "demo-chat"})

    resolved = resolve_capability_config(APPLICATION_ID, "chat")
    assert resolved.model == "catalog-chat-model"
    assert resolved.api_key == "catalog-secret"
    assert resolved.base_url == "https://models.example.test/v1"

    provider = get_chat_provider(APPLICATION_ID)
    assert provider.configuration.model == "catalog-chat-model"
    assert provider.available is True


def test_resolve_without_binding_falls_back_to_legacy(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "object_storage_path", str(tmp_path))
    save_model_configuration(
        {
            "chat_provider": "openai_compatible",
            "chat_model": "legacy-chat",
            "chat_api_key_env": "LEGACY_TEST_CHAT_KEY",
        }
    )
    monkeypatch.setenv("LEGACY_TEST_CHAT_KEY", "legacy-key")

    resolved = resolve_capability_config(APPLICATION_ID, "chat")
    assert resolved.model == "legacy-chat"
    assert resolved.api_key == "legacy-key"


def test_resolve_bound_entry_without_key_raises_platform_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "object_storage_path", str(tmp_path))
    create_catalog_entry(_entry())
    monkeypatch.delenv("DEMO_CATALOG_CHAT_KEY", raising=False)
    save_model_binding(APPLICATION_ID, {"chat": "demo-chat"})

    with pytest.raises(PlatformError) as excinfo:
        resolve_capability_config(APPLICATION_ID, "chat")
    assert excinfo.value.http_status == 502
    assert "DEMO_CATALOG_CHAT_KEY" in excinfo.value.message
    assert "未配置或条目已停用" in excinfo.value.message


def test_delete_referenced_entry_is_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "object_storage_path", str(tmp_path))
    create_catalog_entry(_entry())
    save_model_binding(APPLICATION_ID, {"chat": "demo-chat"})

    assert list_binding_references("demo-chat") == [APPLICATION_ID]
    with pytest.raises(ConflictError):
        delete_catalog_entry("demo-chat")
    assert get_catalog_entry("demo-chat") is not None

    # 解除绑定后可以删除
    save_model_binding(APPLICATION_ID, {"chat": None})
    assert get_model_binding(APPLICATION_ID) == {}
    delete_catalog_entry("demo-chat")
    assert get_catalog_entry("demo-chat") is None
