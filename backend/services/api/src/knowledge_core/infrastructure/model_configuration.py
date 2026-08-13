from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from knowledge_core.config import settings


@dataclass(slots=True)
class ModelConfiguration:
    chat_provider: str
    chat_model: str
    chat_base_url: str
    chat_api_key: str
    embedding_provider: str
    embedding_model: str
    embedding_base_url: str
    embedding_api_key: str
    web_search_provider: str
    web_search_base_url: str
    web_search_api_key: str


_lock = Lock()


def _defaults() -> ModelConfiguration:
    return ModelConfiguration(
        chat_provider=settings.chat_provider,
        chat_model=settings.chat_model,
        chat_base_url=settings.chat_base_url,
        chat_api_key=settings.chat_api_key,
        embedding_provider=settings.embedding_provider,
        embedding_model=settings.embedding_model,
        embedding_base_url=settings.embedding_base_url,
        embedding_api_key=settings.embedding_api_key,
        web_search_provider=settings.web_search_provider,
        web_search_base_url=settings.web_search_base_url,
        web_search_api_key=settings.web_search_api_key,
    )


def _configuration_path() -> Path:
    return Path(settings.object_storage_path) / ".platform" / "model-configuration.json"


def get_model_configuration() -> ModelConfiguration:
    """读取控制台配置；文件不存在或损坏时安全回退到环境变量。"""
    path = _configuration_path()
    if not path.exists():
        return _defaults()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        defaults = asdict(_defaults())
        values = {key: payload.get(key, value) for key, value in defaults.items()}
        return ModelConfiguration(**values)
    except (OSError, json.JSONDecodeError, TypeError):
        return _defaults()


def save_model_configuration(values: dict[str, Any]) -> ModelConfiguration:
    """原子保存模型配置，让 API 与共享存储的 Worker 在下次调用时立即读取。"""
    with _lock:
        current = asdict(get_model_configuration())
        for key, value in values.items():
            if key.endswith("_api_key") and value in {None, ""}:
                continue
            if value is not None:
                current[key] = value
        configuration = ModelConfiguration(**current)
        path = _configuration_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(asdict(configuration), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        return configuration


def model_configuration_view(configuration: ModelConfiguration) -> dict[str, Any]:
    values = asdict(configuration)
    return {
        key: value
        for key, value in values.items()
        if not key.endswith("_api_key")
    } | {
        "chat_api_key_configured": bool(configuration.chat_api_key),
        "embedding_api_key_configured": bool(configuration.embedding_api_key),
        "web_search_api_key_configured": bool(configuration.web_search_api_key),
    }

