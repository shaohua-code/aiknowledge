"""基础就绪与 Provider 配置状态的语义回归测试。"""

from __future__ import annotations

from knowledge_core.infrastructure.model_configuration import ModelConfiguration
from knowledge_core.main import _provider_configuration_checks


def test_provider_check_reports_configuration_without_claiming_connectivity(monkeypatch) -> None:
    """存在凭证只能说明已配置，外部连通性必须由实际探针另行验证。"""
    monkeypatch.setenv("READY_TEST_CHAT_KEY", "configured-secret")
    checks = _provider_configuration_checks(
        ModelConfiguration(
            chat_provider="openai_compatible",
            chat_model="example-chat",
            chat_base_url="https://example.invalid/v1",
            chat_api_key_env="READY_TEST_CHAT_KEY",
            embedding_provider="local_hash",
            embedding_model="local-hash-v1",
            embedding_base_url="https://example.invalid/v1",
            embedding_api_key_env="READY_TEST_EMBEDDING_KEY",
            web_search_provider="serper",
            web_search_base_url="https://example.invalid",
            web_search_api_key_env="READY_TEST_SEARCH_KEY",
        )
    )

    assert checks == {
        "chatProvider": {"status": "configured"},
        "embeddingProvider": {"status": "development"},
        "searchProvider": {"status": "unconfigured"},
    }
