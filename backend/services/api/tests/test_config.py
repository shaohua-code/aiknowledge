from __future__ import annotations

import pytest
from pydantic import ValidationError

from knowledge_core.config import PROJECT_ROOT, Settings


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


def test_root_environment_file_is_part_of_backend_configuration() -> None:
    environment_files = Settings.model_config["env_file"]

    assert PROJECT_ROOT / ".env" in environment_files


def test_docker_addresses_are_normalized_for_direct_local_start() -> None:
    configuration = Settings(
        _env_file=None,
        app_env="development",
        database_url="postgresql+asyncpg://user:secret@postgres:5432/platform",
        redis_url="redis://redis:6379/0",
        celery_broker_url="redis://redis:6379/1",
        celery_result_backend="redis://redis:6379/2",
        object_storage_path="/data/storage",
    )

    assert "@localhost:5433/" in configuration.database_url
    assert configuration.redis_url == "redis://localhost:6379/0"
    assert configuration.celery_broker_url == "redis://localhost:6379/1"
    assert configuration.celery_result_backend == "redis://localhost:6379/2"
