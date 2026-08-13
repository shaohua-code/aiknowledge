from __future__ import annotations

from knowledge_core.config import settings
from knowledge_core.infrastructure.model_configuration import (
    get_model_configuration,
    model_configuration_view,
    save_model_configuration,
)


def test_model_configuration_persists_and_never_exposes_secrets(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "object_storage_path", str(tmp_path))

    saved = save_model_configuration(
        {
            "chat_provider": "openai_compatible",
            "chat_model": "test-chat",
            "chat_base_url": "https://models.example.test/v1",
            "chat_api_key": "secret-chat-key",
        }
    )

    assert saved.chat_model == "test-chat"
    assert get_model_configuration().chat_api_key == "secret-chat-key"
    view = model_configuration_view(saved)
    assert view["chat_api_key_configured"] is True
    assert "chat_api_key" not in view
    assert "secret-chat-key" not in str(view)


def test_blank_api_key_keeps_existing_secret(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "object_storage_path", str(tmp_path))
    save_model_configuration({"chat_api_key": "existing-secret"})
    saved = save_model_configuration({"chat_model": "new-model", "chat_api_key": ""})

    assert saved.chat_model == "new-model"
    assert saved.chat_api_key == "existing-secret"

