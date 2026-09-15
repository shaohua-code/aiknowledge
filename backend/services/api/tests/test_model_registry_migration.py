"""验证旧模型 JSON 回填工具的预检和备份边界。"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from knowledge_core.infrastructure.model_registry_migration import (
    ModelRegistryMigrationError,
    load_legacy_registry,
)


def _write_json(path, payload: object) -> None:
    """以 UTF-8 写入测试源文件，模拟旧共享存储中的 JSON 结构。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_preflight_rejects_legacy_plaintext_configuration(tmp_path) -> None:
    """含内联凭证时不得部分回填目录/绑定，以免留下双权威来源。"""
    source_root = tmp_path / "storage"
    application_id = uuid4()
    _write_json(
        source_root / ".platform" / "model-catalog.json",
        {
            "entries": [
                {
                    "code": "resume-chat",
                    "name": "简历回答模型",
                    "capability": "chat",
                    "provider": "openai_compatible",
                    "model": "test-chat",
                    "base_url": "https://models.example.test/v1",
                    "api_key_env": "RESUME_CHAT_API_KEY",
                }
            ]
        },
    )
    _write_json(
        source_root / ".platform" / "model-bindings" / f"{application_id}.json",
        {"chat": "resume-chat"},
    )
    # 这是旧实现可能保存敏感值的文件；新工具必须完全忽略它。
    _write_json(
        source_root / ".platform" / "model-configuration.json",
        {"chat_api_key": "never-copy-this-secret"},
    )

    with pytest.raises(ModelRegistryMigrationError, match="内联凭证"):
        load_legacy_registry(source_root)
    assert not (tmp_path / "backups").exists()


def test_registry_preflight_rejects_binding_to_missing_catalog_entry(tmp_path) -> None:
    """目录与绑定不一致时必须在创建备份或数据库事务前整体拒绝。"""
    source_root = tmp_path / "storage"
    _write_json(source_root / ".platform" / "model-catalog.json", {"entries": []})
    _write_json(
        source_root / ".platform" / "model-bindings" / f"{uuid4()}.json",
        {"chat": "missing-entry"},
    )

    with pytest.raises(ModelRegistryMigrationError, match="不存在"):
        load_legacy_registry(source_root)
