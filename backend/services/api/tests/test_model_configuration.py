from __future__ import annotations

import pytest

from knowledge_core.config import settings
from knowledge_core.infrastructure.model_configuration import (
    get_model_configuration,
    model_configuration_view,
    save_model_configuration,
)
from knowledge_core.shared.errors import ConflictError


def test_model_configuration_persists_references_and_never_exposes_secrets(
    tmp_path, monkeypatch
) -> None:
    """平台配置只持久化密钥环境变量名，公开视图也不泄漏部署凭证。"""
    monkeypatch.setattr(settings, "object_storage_path", str(tmp_path))
    monkeypatch.setenv("TEST_CHAT_KEY", "secret-chat-key")

    saved = save_model_configuration(
        {
            "chat_provider": "openai_compatible",
            "chat_model": "test-chat",
            "chat_base_url": "https://models.example.test/v1",
            "chat_api_key_env": "TEST_CHAT_KEY",
        }
    )

    assert saved.chat_model == "test-chat"
    assert get_model_configuration().chat_api_key == "secret-chat-key"
    stored = (tmp_path / ".platform" / "model-configuration.json").read_text(encoding="utf-8")
    assert "TEST_CHAT_KEY" in stored
    assert "secret-chat-key" not in stored
    view = model_configuration_view(saved)
    assert view["chat_api_key_configured"] is True
    assert "chat_api_key" not in view
    assert "secret-chat-key" not in str(view)


def test_legacy_inline_credentials_remain_readable_but_block_overwrite(
    tmp_path, monkeypatch
) -> None:
    """旧文件可继续读取以避免中断服务，但任何写入必须先完成显式凭证迁移。"""
    monkeypatch.setattr(settings, "object_storage_path", str(tmp_path))
    path = tmp_path / ".platform" / "model-configuration.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"chat_api_key":"existing-secret"}', encoding="utf-8")

    legacy = get_model_configuration()
    assert legacy.chat_api_key == "existing-secret"
    assert model_configuration_view(legacy)["legacy_inline_credentials_detected"] is True
    with pytest.raises(ConflictError):
        save_model_configuration({"chat_model": "new-model"})
