from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from knowledge_core.config import settings

_lock = Lock()
_BINDABLE_CAPABILITIES = {"chat", "embedding", "web_search"}


def _bindings_directory() -> Path:
    return Path(settings.object_storage_path) / ".platform" / "model-bindings"


def _binding_path(application_id: str) -> Path:
    return _bindings_directory() / f"{application_id}.json"


def get_model_binding(application_id: str) -> dict[str, str]:
    """读取应用的模型绑定；文件不存在或损坏时视为未绑定。"""
    if not application_id:
        return {}
    path = _binding_path(application_id)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        capability: code
        for capability, code in payload.items()
        if capability in _BINDABLE_CAPABILITIES and isinstance(code, str) and code
    }


def save_model_binding(application_id: str, bindings: dict[str, str | None]) -> dict[str, str]:
    """原子保存应用绑定；空值表示解除对应能力的绑定。"""
    clean = {
        capability: code
        for capability, code in bindings.items()
        if capability in _BINDABLE_CAPABILITIES and code
    }
    with _lock:
        path = _binding_path(application_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(clean, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    return clean


def list_binding_references(code: str) -> list[str]:
    """列出仍引用指定模型编码的应用 ID，用于删除前保护。"""
    directory = _bindings_directory()
    if not directory.exists():
        return []
    references: list[str] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and code in payload.values():
            references.append(path.stem)
    return references
