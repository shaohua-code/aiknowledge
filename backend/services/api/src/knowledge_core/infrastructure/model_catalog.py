from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from threading import Lock
from typing import Any

from knowledge_core.config import settings
from knowledge_core.shared.errors import ConflictError, NotFoundError


@dataclass(slots=True)
class ModelCatalogEntry:
    code: str
    name: str
    capability: str
    provider: str
    model: str
    base_url: str
    api_key_env: str
    enabled: bool = True
    note: str | None = None


_lock = Lock()
_ENTRY_FIELDS = {item.name for item in fields(ModelCatalogEntry)}


def _catalog_path() -> Path:
    return Path(settings.object_storage_path) / ".platform" / "model-catalog.json"


def _load_entries() -> list[ModelCatalogEntry]:
    """读取模型目录；文件不存在或损坏时安全回退为空目录。"""
    path = _catalog_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("entries", []) if isinstance(payload, dict) else []
        return [
            ModelCatalogEntry(**{key: row[key] for key in _ENTRY_FIELDS if key in row})
            for row in rows
            if isinstance(row, dict)
        ]
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def _write_entries(entries: list[ModelCatalogEntry]) -> None:
    """原子写模型目录，避免 API 与共享存储的 Worker 读到半写文件。"""
    with _lock:
        path = _catalog_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"entries": [asdict(entry) for entry in entries]},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)


def list_catalog_entries(capability: str | None = None) -> list[ModelCatalogEntry]:
    entries = _load_entries()
    if capability:
        entries = [entry for entry in entries if entry.capability == capability]
    return entries


def get_catalog_entry(code: str) -> ModelCatalogEntry | None:
    return next((entry for entry in _load_entries() if entry.code == code), None)


def create_catalog_entry(entry: ModelCatalogEntry) -> ModelCatalogEntry:
    entries = _load_entries()
    if any(item.code == entry.code for item in entries):
        raise ConflictError(f"模型编码 {entry.code} 已存在，请更换编码")
    entries.append(entry)
    _write_entries(entries)
    return entry


def update_catalog_entry(code: str, values: dict[str, Any]) -> ModelCatalogEntry:
    entries = _load_entries()
    index = next((i for i, item in enumerate(entries) if item.code == code), None)
    if index is None:
        raise NotFoundError(f"模型目录中不存在编码为 {code} 的模型")
    current = asdict(entries[index])
    current.update(
        {key: value for key, value in values.items() if key in _ENTRY_FIELDS and key != "code"}
    )
    updated = ModelCatalogEntry(**current)
    entries[index] = updated
    _write_entries(entries)
    return updated


def delete_catalog_entry(code: str) -> None:
    from knowledge_core.infrastructure.model_bindings import list_binding_references

    references = list_binding_references(code)
    if references:
        raise ConflictError(
            f"模型 {code} 仍被 {len(references)} 个应用绑定，请先解除绑定后再删除",
            details={"applicationIds": references},
        )
    entries = _load_entries()
    remaining = [entry for entry in entries if entry.code != code]
    if len(remaining) == len(entries):
        raise NotFoundError(f"模型目录中不存在编码为 {code} 的模型")
    _write_entries(remaining)


def catalog_entry_view(entry: ModelCatalogEntry) -> dict[str, Any]:
    """目录视图不回显任何密钥，只报告密钥环境变量是否已配置。"""
    return asdict(entry) | {
        "api_key_configured": bool(os.environ.get(entry.api_key_env, "").strip())
    }
