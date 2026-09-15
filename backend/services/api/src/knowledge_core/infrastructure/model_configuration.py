from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from knowledge_core.config import settings
from knowledge_core.shared.errors import ConflictError


@dataclass(slots=True)
class ModelConfiguration:
    """平台默认模型配置。

    配置文件只保存部署环境变量的名称，实际凭证始终由部署环境解析。三个
    ``_legacy_*`` 字段仅用于只读兼容旧 JSON；一旦检测到它们，更新会被阻止，
    防止管理员在未迁移凭证时意外覆盖或继续写入明文。
    """

    chat_provider: str
    chat_model: str
    chat_base_url: str
    chat_api_key_env: str
    embedding_provider: str
    embedding_model: str
    embedding_base_url: str
    embedding_api_key_env: str
    web_search_provider: str
    web_search_base_url: str
    web_search_api_key_env: str
    _legacy_chat_api_key: str = ""
    _legacy_embedding_api_key: str = ""
    _legacy_web_search_api_key: str = ""

    @property
    def chat_api_key(self) -> str:
        """解析对话密钥；旧文件仅在完成显式迁移前作为只读兼容来源。"""
        return _resolve_credential(
            self.chat_api_key_env, settings.chat_api_key, self._legacy_chat_api_key
        )

    @property
    def embedding_api_key(self) -> str:
        """解析向量密钥；不将任何解析结果写回配置文件。"""
        return _resolve_credential(
            self.embedding_api_key_env,
            settings.embedding_api_key,
            self._legacy_embedding_api_key,
        )

    @property
    def web_search_api_key(self) -> str:
        """解析联网搜索密钥；部署变量优先于历史兼容值。"""
        return _resolve_credential(
            self.web_search_api_key_env,
            settings.web_search_api_key,
            self._legacy_web_search_api_key,
        )

    @property
    def has_legacy_inline_credentials(self) -> bool:
        """标记旧 JSON 是否仍含明文凭证，供控制面阻止不安全的覆盖写入。"""
        return any(
            (
                self._legacy_chat_api_key,
                self._legacy_embedding_api_key,
                self._legacy_web_search_api_key,
            )
        )


_lock = Lock()


def _resolve_credential(reference: str, configured_default: str, legacy_value: str) -> str:
    """按“部署环境变量→根配置→只读旧文件”顺序解析密钥，绝不持久化结果。"""
    deployed_value = os.environ.get(reference, "").strip()
    # 根 .env 是开发部署配置来源；只对三个默认引用保留此兼容入口，
    # 自定义引用必须由实际进程环境提供，不能意外回退到另一把默认密钥。
    default_references = {"CHAT_API_KEY", "EMBEDDING_API_KEY", "WEB_SEARCH_API_KEY"}
    configured_value = configured_default.strip() if reference in default_references else ""
    return deployed_value or configured_value or legacy_value


def _storable_values(configuration: ModelConfiguration) -> dict[str, str]:
    """取得允许落盘的非密钥字段，集中防止未来调用方误序列化 dataclass。"""
    return {
        "chat_provider": configuration.chat_provider,
        "chat_model": configuration.chat_model,
        "chat_base_url": configuration.chat_base_url,
        "chat_api_key_env": configuration.chat_api_key_env,
        "embedding_provider": configuration.embedding_provider,
        "embedding_model": configuration.embedding_model,
        "embedding_base_url": configuration.embedding_base_url,
        "embedding_api_key_env": configuration.embedding_api_key_env,
        "web_search_provider": configuration.web_search_provider,
        "web_search_base_url": configuration.web_search_base_url,
        "web_search_api_key_env": configuration.web_search_api_key_env,
    }


def _defaults() -> ModelConfiguration:
    return ModelConfiguration(
        chat_provider=settings.chat_provider,
        chat_model=settings.chat_model,
        chat_base_url=settings.chat_base_url,
        chat_api_key_env="CHAT_API_KEY",
        embedding_provider=settings.embedding_provider,
        embedding_model=settings.embedding_model,
        embedding_base_url=settings.embedding_base_url,
        embedding_api_key_env="EMBEDDING_API_KEY",
        web_search_provider=settings.web_search_provider,
        web_search_base_url=settings.web_search_base_url,
        web_search_api_key_env="WEB_SEARCH_API_KEY",
    )


def _configuration_path() -> Path:
    return Path(settings.object_storage_path) / ".platform" / "model-configuration.json"


def get_model_configuration() -> ModelConfiguration:
    """读取模型配置；旧明文文件只读兼容，缺失或损坏时回退部署配置。"""
    path = _configuration_path()
    if not path.exists():
        return _defaults()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        defaults = _storable_values(_defaults())
        values = {key: payload.get(key, value) for key, value in defaults.items()}
        # 旧键不得再被保存，但要保持可运行直到运维人员将其移入部署密钥系统。
        return ModelConfiguration(
            **values,
            _legacy_chat_api_key=_legacy_value(payload, "chat_api_key"),
            _legacy_embedding_api_key=_legacy_value(payload, "embedding_api_key"),
            _legacy_web_search_api_key=_legacy_value(payload, "web_search_api_key"),
        )
    except (OSError, json.JSONDecodeError, TypeError):
        return _defaults()


def _legacy_value(payload: dict[str, Any], key: str) -> str:
    """仅从旧 JSON 读取字符串密钥，畸形内容不参与凭证解析。"""
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


def save_model_configuration(values: dict[str, Any]) -> ModelConfiguration:
    """原子保存非密钥配置；发现旧明文时拒绝覆盖并要求先完成凭证迁移。"""
    with _lock:
        existing = get_model_configuration()
        if existing.has_legacy_inline_credentials:
            raise ConflictError(
                "检测到旧模型配置仍含明文凭证；请先将凭证移入部署环境变量，再保存引用配置"
            )
        current = _storable_values(existing)
        for key, value in values.items():
            if key.endswith("_api_key"):
                raise ConflictError("模型凭证只能通过环境变量引用配置，不能写入平台配置文件")
            if value is not None:
                current[key] = value
        configuration = ModelConfiguration(**current)
        path = _configuration_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(_storable_values(configuration), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        return configuration


def model_configuration_view(configuration: ModelConfiguration) -> dict[str, Any]:
    """构造无密钥公开视图，并向管理员暴露需要迁移旧文件的安全状态。"""
    return _storable_values(configuration) | {
        "chat_api_key_configured": bool(configuration.chat_api_key),
        "embedding_api_key_configured": bool(configuration.embedding_api_key),
        "web_search_api_key_configured": bool(configuration.web_search_api_key),
        "legacy_inline_credentials_detected": configuration.has_legacy_inline_credentials,
    }
