"""Alembic 迁移环境配置。

对应 SubTask 4.5：初始化 Alembic，支持 offline 与 online 迁移。

设计要点
--------
1. 导入 ``app.db.models`` 的 ``Base.metadata`` 作为 ``target_metadata``，
   供 ``alembic revision --autogenerate`` 比对模型与数据库差异。
2. 数据库 URL 从 ``app.core.config.settings.database_url`` 读取，
   并将异步驱动 ``asyncpg`` 替换为同步驱动 ``psycopg2``，
   因为 Alembic 默认使用同步连接（避免 async 复杂性）。
3. 同时支持 offline（生成 SQL 脚本）与 online（直接执行）两种模式。
"""
from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# 导入应用配置与所有模型，确保 Base.metadata 包含全部表定义
from app.core.config import settings
from app.db.models import Base  # noqa: F401  # 导入触发模型注册

# Alembic 配置对象（由 alembic.ini 加载）
config = context.config

# 配置日志（alembic.ini 中 [loggers] 段定义）
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _get_sync_url() -> str:
    """将异步 database_url 转换为同步 URL。

    Alembic 默认使用同步连接，需将 ``postgresql+asyncpg://`` 替换为
    ``postgresql+psycopg2://``（或纯 ``postgresql://``）。

    Returns:
        同步数据库 URL 字符串。
    """
    url = settings.database_url
    # 异步 asyncpg → 同步 psycopg2
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    return url


# 设置 Alembic 使用的数据库 URL（覆盖 alembic.ini 中的占位值）
config.set_main_option("sqlalchemy.url", _get_sync_url())

# 目标元数据：所有 ORM 模型的表定义集合
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：生成 SQL 脚本而不连接数据库。

    适用于 CI/CD 场景，将生成的 SQL 交由 DBA 审核后执行。
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # 比较类型与服务器默认值，确保 autogenerate 准确
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：直接连接数据库执行迁移。

    适用于本地开发与部署场景。
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


# 根据 alembic 命令行参数选择模式
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
