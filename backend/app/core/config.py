"""应用配置：使用 Pydantic v2 Settings 从环境变量与 .env 文件读取配置。

对应 SubTask 2.3：所有外部服务、模型、对象存储、链路限制参数集中管理。
配置字段命名与 .env.example 一一对应，业务代码通过 settings 单例访问。
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置模型。

    所有字段均可通过环境变量或 .env 文件注入，未提供默认值的必填字段
    在缺失时会抛出 ValidationError，提示开发者补全配置。

    Attributes:
        app_env: 运行环境，用于日志与降级判断
        database_url: PostgreSQL 异步连接字符串（含 asyncpg 驱动）
        redis_url: Redis 连接字符串，用于限流与幂等
        celery_broker_url: Celery 消息队列地址
        celery_result_backend: Celery 结果后端地址
        object_storage_provider: 对象存储类型（local 或 s3）
        object_storage_path: 本地存储根目录或 S3 bucket 前缀
        chat_provider: 聊天模型 Provider（openai 兼容）
        chat_model: 聊天模型名称
        chat_api_key: 聊天模型 API Key
        chat_base_url: 聊天模型基础 URL
        embedding_provider: Embedding Provider
        embedding_model: Embedding 模型名称
        embedding_api_key: Embedding API Key
        embedding_base_url: Embedding 基础 URL
        embedding_dimension: 向量维度，默认 1536
        web_search_provider: Web 搜索 Provider
        web_search_api_key: Web 搜索 API Key
        management_api_key: 管理密钥，保护项目管理接口
        rate_limit_per_minute: 项目级限流，默认 60/min
        research_hard_timeout_seconds: 研究整体硬超时，默认 15s
        web_search_timeout_seconds: 联网搜索超时，默认 5s
        tool_timeout_seconds: 业务工具超时，默认 4s
        max_evidence: 单次研究最大证据数，默认 8
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 运行环境
    app_env: str = "development"

    # 数据库与缓存
    # 默认值对应 docker-compose.yml 中 postgres 服务（主机端口映射为 5433）
    database_url: str = "postgresql+asyncpg://aiknowledge:sss980318..@localhost:5433/knowledge_hub"
    redis_url: str = "redis://localhost:6379/0"

    # Celery 任务队列
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # 对象存储
    object_storage_provider: str = "local"
    object_storage_path: str = "./storage"

    # 聊天模型
    chat_provider: str = "openai"
    chat_model: str = "gpt-4o-mini"
    chat_api_key: str = ""
    chat_base_url: str = "https://api.openai.com/v1"

    # Embedding 模型
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_dimension: int = 1536

    # Web 搜索
    web_search_provider: str = ""
    web_search_api_key: str = ""

    # 管理密钥：保护项目管理接口（POST/GET/PATCH /api/v1/projects）
    management_api_key: str = ""

    # 链路限制参数
    rate_limit_per_minute: int = 60
    research_hard_timeout_seconds: int = 15
    web_search_timeout_seconds: int = 5
    tool_timeout_seconds: int = 4
    max_evidence: int = 8


@lru_cache
def get_settings() -> Settings:
    """获取配置单例。

    使用 lru_cache 缓存，避免重复读取环境变量；
    测试时可通过 get_settings.cache_clear() 重置。

    Returns:
        Settings: 应用配置实例。
    """
    return Settings()


# 全局配置单例，业务模块直接 from app.core.config import settings
settings = get_settings()
