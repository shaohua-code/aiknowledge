from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """运行配置。

    默认值只保证本地开发可启动；生产环境会强制校验管理员密码、会话密钥、
    CORS 和模型配置，避免示例值误入生产。
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_name: str = "AI 知识能力底座"
    database_url: str = "postgresql+asyncpg://aiknowledge@localhost:5433/aiknowledge_v2"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    object_storage_path: str = "./storage"

    admin_email: str = "admin@example.local"
    admin_password: str = ""
    session_secret: str = "development-session-secret-change-before-production"
    session_cookie_name: str = "aik_admin_session"
    session_cookie_secure: bool = False
    session_max_age_seconds: int = 8 * 60 * 60
    cors_origins: str = "http://localhost:5173"

    chat_provider: str = "disabled"
    chat_model: str = ""
    chat_api_key: str = ""
    chat_base_url: str = "https://api.openai.com/v1"
    embedding_provider: str = "local_hash"
    embedding_model: str = "local-hash-v1"
    embedding_api_key: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_dimension: int = 1536
    web_search_provider: str = "disabled"
    web_search_api_key: str = ""
    web_search_base_url: str = "https://google.serper.dev"

    rate_limit_per_minute: int = 60
    answer_timeout_seconds: int = 15
    max_evidence: int = 8
    request_body_retention_days: int = 0

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @model_validator(mode="after")
    def validate_production_secrets(self) -> Settings:
        if self.embedding_dimension != 1536:
            raise ValueError("当前数据库迁移固定使用 1536 维向量；变更维度前必须先增加数据库迁移")
        if self.app_env != "production":
            return self
        weak_markers = {"replace", "example", "change", "password"}
        if len(self.admin_password) < 12 or any(
            marker in self.admin_password.lower() for marker in weak_markers
        ):
            raise ValueError("生产环境 ADMIN_PASSWORD 至少需要 12 个字符")
        if len(self.session_secret) < 32 or any(
            marker in self.session_secret.lower() for marker in weak_markers
        ):
            raise ValueError("生产环境必须配置随机 SESSION_SECRET")
        if any(marker in self.database_url.lower() for marker in weak_markers):
            raise ValueError("生产环境 DATABASE_URL 仍包含示例凭证")
        if not self.session_cookie_secure:
            raise ValueError("生产环境必须启用 SESSION_COOKIE_SECURE")
        if "*" in self.cors_origin_list:
            raise ValueError("生产环境 CORS_ORIGINS 不能包含通配符")
        if self.embedding_provider == "local_hash":
            raise ValueError("生产环境禁止使用 local_hash Embedding")
        if self.chat_provider == "disabled" or not self.chat_model or not self.chat_api_key:
            raise ValueError("生产环境必须配置可用的 Chat Provider")
        if self.web_search_provider not in {"disabled", "serper"}:
            raise ValueError("WEB_SEARCH_PROVIDER 仅支持 disabled 或 serper")
        if self.web_search_provider == "serper" and not self.web_search_api_key:
            raise ValueError("启用 Serper 时必须配置 WEB_SEARCH_API_KEY")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
