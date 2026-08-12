from __future__ import annotations

import pytest
from pydantic import ValidationError

from knowledge_core.config import Settings


def test_production_rejects_example_credentials() -> None:
    with pytest.raises(ValidationError, match="ADMIN_PASSWORD"):
        Settings(
            _env_file=None,
            app_env="production",
            admin_password="replace-with-password",
            session_secret="a-random-session-secret-with-more-than-32-characters",
            database_url="postgresql+asyncpg://user:secret@db/platform",
            session_cookie_secure=True,
            embedding_provider="openai",
            chat_provider="openai",
            chat_model="chat-model",
            chat_api_key="secret",
        )


def test_vector_dimension_requires_database_migration() -> None:
    with pytest.raises(ValidationError, match="数据库迁移"):
        Settings(_env_file=None, embedding_dimension=3072)
