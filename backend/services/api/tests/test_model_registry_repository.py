"""模型注册数据库读取仓储的优先级与隔离回归。"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from knowledge_core.config import settings
from knowledge_core.infrastructure.model_catalog import ModelCatalogEntry
from knowledge_core.infrastructure.model_registry_repository import ModelRegistryRepository
from knowledge_core.shared.errors import ConflictError


@pytest.mark.asyncio
async def test_registry_resolution_prefers_environment_then_application_then_platform() -> None:
    """同一能力必须优先采用最具体的绑定，且不同应用不能读取彼此的覆盖。"""
    schema = f"aik_model_registry_{uuid4().hex}"
    application_id = UUID("00000000-0000-0000-0000-000000000401")
    other_application_id = UUID("00000000-0000-0000-0000-000000000402")
    environment_id = UUID("00000000-0000-0000-0000-000000000403")
    other_environment_id = UUID("00000000-0000-0000-0000-000000000404")
    platform_entry_id = UUID("00000000-0000-0000-0000-000000000405")
    application_entry_id = UUID("00000000-0000-0000-0000-000000000406")
    environment_entry_id = UUID("00000000-0000-0000-0000-000000000407")
    other_entry_id = UUID("00000000-0000-0000-0000-000000000408")
    test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    try:
        async with test_engine.begin() as connection:
            await connection.execute(text(f"CREATE SCHEMA {schema}"))
            await connection.execute(text(f"SET search_path TO {schema}"))
            # 临时表只覆盖仓储查询字段；不会映射、变更或清理真实业务表。
            await connection.execute(
                text(
                    "CREATE TABLE model_catalog_records ("
                    "id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(), "
                    "updated_at timestamptz NOT NULL DEFAULT now(), "
                    "code varchar(80) NOT NULL UNIQUE, "
                    "name varchar(120) NOT NULL, "
                    "capability varchar(32) NOT NULL, provider varchar(64) NOT NULL, "
                    "model varchar(160) NOT NULL, base_url text NOT NULL, "
                    "api_key_env varchar(120) NOT NULL, "
                    "enabled boolean NOT NULL, note text, revision integer NOT NULL DEFAULT 1)"
                )
            )
            await connection.execute(
                text(
                    "CREATE TABLE application_model_binding_records ("
                    "application_id uuid NOT NULL, environment_id uuid, "
                    "catalog_entry_id uuid NOT NULL, "
                    "capability varchar(32) NOT NULL)"
                )
            )
            await connection.execute(
                text(
                    "CREATE TABLE platform_model_default_records ("
                    "capability varchar(32) NOT NULL, catalog_entry_id uuid NOT NULL)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO model_catalog_records "
                    "(id, code, name, capability, provider, model, base_url, "
                    "api_key_env, enabled, note) "
                    "VALUES (:id, :code, :name, 'chat', 'openai_compatible', 'test-model', "
                    "'https://example.invalid/v1', 'TEST_MODEL_KEY', true, NULL)"
                ),
                [
                    {"id": platform_entry_id, "code": "platform-chat", "name": "平台默认"},
                    {"id": application_entry_id, "code": "application-chat", "name": "应用默认"},
                    {"id": environment_entry_id, "code": "environment-chat", "name": "环境覆盖"},
                    {"id": other_entry_id, "code": "other-chat", "name": "其他应用"},
                ],
            )
            await connection.execute(
                text(
                    "INSERT INTO application_model_binding_records "
                    "(application_id, environment_id, catalog_entry_id, capability) "
                    "VALUES (:application_id, :environment_id, :catalog_entry_id, 'chat')"
                ),
                [
                    {
                        "application_id": application_id,
                        "environment_id": None,
                        "catalog_entry_id": application_entry_id,
                    },
                    {
                        "application_id": application_id,
                        "environment_id": environment_id,
                        "catalog_entry_id": environment_entry_id,
                    },
                    {
                        "application_id": other_application_id,
                        "environment_id": other_environment_id,
                        "catalog_entry_id": other_entry_id,
                    },
                ],
            )
            await connection.execute(
                text(
                    "INSERT INTO platform_model_default_records (capability, catalog_entry_id) "
                    "VALUES ('chat', :catalog_entry_id)"
                ),
                {"catalog_entry_id": platform_entry_id},
            )

        async with session_factory() as session:
            await session.execute(text(f"SET search_path TO {schema}"))
            repository = ModelRegistryRepository(session)
            environment_entry = await repository.resolve_entry(
                application_id, environment_id, "chat"
            )
            application_entry = await repository.resolve_entry(application_id, None, "chat")
            platform_entry = await repository.resolve_entry(
                UUID("00000000-0000-0000-0000-000000000409"), None, "chat"
            )

        assert environment_entry is not None and environment_entry.code == "environment-chat"
        assert application_entry is not None and application_entry.code == "application-chat"
        assert platform_entry is not None and platform_entry.code == "platform-chat"
    finally:
        # Schema 名只含 uuid 十六进制，回收范围严格局限于本测试创建的临时资源。
        async with test_engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await test_engine.dispose()


@pytest.mark.asyncio
async def test_registry_write_replaces_application_binding_and_protects_references() -> None:
    """目录和默认绑定写入必须原子生效，且被引用条目不能被控制面删除。"""
    schema = f"aik_model_registry_write_{uuid4().hex}"
    application_id = uuid4()
    environment_id = uuid4()
    test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    try:
        async with test_engine.begin() as connection:
            await connection.execute(text(f"CREATE SCHEMA {schema}"))
            await connection.execute(text(f"SET search_path TO {schema}"))
            # 临时 Schema 仅实现本仓储写入所需字段，不会触碰真实应用或目录数据。
            await connection.execute(
                text(
                    "CREATE TABLE applications ("
                    "id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(), "
                    "updated_at timestamptz NOT NULL DEFAULT now(), code varchar(64) NOT NULL, "
                    "name varchar(160) NOT NULL, description text, "
                    "application_type varchar(32) NOT NULL, status varchar(24) NOT NULL)"
                )
            )
            await connection.execute(
                text(
                    "CREATE TABLE model_catalog_records ("
                    "id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(), "
                    "updated_at timestamptz NOT NULL DEFAULT now(), "
                    "code varchar(80) NOT NULL UNIQUE, "
                    "name varchar(120) NOT NULL, capability varchar(32) NOT NULL, "
                    "provider varchar(64) NOT NULL, model varchar(160) NOT NULL, "
                    "base_url text NOT NULL, "
                    "api_key_env varchar(120) NOT NULL, enabled boolean NOT NULL, note text, "
                    "revision integer NOT NULL DEFAULT 1)"
                )
            )
            await connection.execute(
                text(
                    "CREATE TABLE application_model_binding_records ("
                    "id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(), "
                    "updated_at timestamptz NOT NULL DEFAULT now(), application_id uuid NOT NULL, "
                    "environment_id uuid, catalog_entry_id uuid NOT NULL, "
                    "capability varchar(32) NOT NULL, "
                    "revision integer NOT NULL DEFAULT 1)"
                )
            )
            await connection.execute(
                text(
                    "CREATE TABLE platform_model_default_records ("
                    "id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(), "
                    "updated_at timestamptz NOT NULL DEFAULT now(), "
                    "capability varchar(32) NOT NULL UNIQUE, catalog_entry_id uuid NOT NULL, "
                    "revision integer NOT NULL DEFAULT 1)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO applications (id, code, name, application_type, status) "
                    "VALUES (:id, 'registry-write', '注册表写入', 'general', 'active')"
                ),
                {"id": application_id},
            )
            await connection.execute(
                text(
                    "CREATE TABLE application_environments ("
                    "id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(), "
                    "updated_at timestamptz NOT NULL DEFAULT now(), application_id uuid NOT NULL, "
                    "code varchar(24) NOT NULL, name varchar(80) NOT NULL, "
                    "status varchar(24) NOT NULL)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO application_environments "
                    "(id, application_id, code, name, status) "
                    "VALUES (:id, :application_id, 'test', '测试环境', 'active')"
                ),
                {"id": environment_id, "application_id": application_id},
            )

        async with session_factory() as session:
            await session.execute(text(f"SET search_path TO {schema}"))
            repository = ModelRegistryRepository(session)
            created = await repository.create_entry(
                ModelCatalogEntry(
                    code="write-chat",
                    name="写入 Chat",
                    capability="chat",
                    provider="openai_compatible",
                    model="write-model",
                    base_url="https://example.invalid/v1",
                    api_key_env="WRITE_CHAT_KEY",
                    enabled=True,
                    note=None,
                )
            )
            binding = await repository.replace_application_binding(
                application_id, {"chat": created.code}
            )
            assert binding == {"chat": "write-chat"}
            environment_entry = await repository.create_entry(
                ModelCatalogEntry(
                    code="write-environment-chat",
                    name="环境 Chat",
                    capability="chat",
                    provider="openai_compatible",
                    model="environment-model",
                    base_url="https://example.invalid/v1",
                    api_key_env="WRITE_ENVIRONMENT_CHAT_KEY",
                    enabled=True,
                    note=None,
                )
            )
            environment_binding = await repository.replace_environment_binding(
                application_id, environment_id, {"chat": environment_entry.code}
            )
            assert environment_binding == {"chat": "write-environment-chat"}
            application_binding = await repository.read_application_binding(application_id)
            assert application_binding == {"chat": "write-chat"}
            resolved = await repository.resolve_entry(application_id, environment_id, "chat")
            assert resolved is not None and resolved.code == "write-environment-chat"
            with pytest.raises(ConflictError):
                await repository.delete_entry(created.code)
            with pytest.raises(ConflictError):
                await repository.delete_entry(environment_entry.code)
            cleared = await repository.replace_application_binding(application_id, {"chat": None})
            assert cleared == {}
            assert await repository.replace_environment_binding(
                application_id, environment_id, {"chat": None}
            ) == {}
            await repository.delete_entry(environment_entry.code)
            platform_defaults = await repository.replace_platform_defaults(
                {"chat": created.code}
            )
            assert platform_defaults == {"chat": "write-chat"}
            platform_entry = await repository.resolve_entry(uuid4(), None, "chat")
            assert platform_entry is not None and platform_entry.code == "write-chat"
            with pytest.raises(ConflictError):
                await repository.delete_entry(created.code)
            assert await repository.replace_platform_defaults({"chat": None}) == {}
            await repository.delete_entry(created.code)
            assert await repository.get_entry(created.code) is None
    finally:
        # Schema 名只由 uuid 生成，级联回收范围严格局限于本测试临时资源。
        async with test_engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await test_engine.dispose()
