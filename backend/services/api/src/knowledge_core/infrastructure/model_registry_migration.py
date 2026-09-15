"""将旧模型目录和应用绑定安全回填到结构化注册表的受控工具。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from knowledge_core.config import settings
from knowledge_core.infrastructure.models import (
    Application,
    ApplicationModelBindingRecord,
    ModelCatalogRecord,
)


class ModelRegistryMigrationError(ValueError):
    """旧文件格式、引用关系或已存在数据库状态不满足安全回填条件。"""


@dataclass(frozen=True, slots=True)
class LegacyCatalogEntry:
    """旧目录文件中可安全迁移的非密钥模型字段。"""

    code: str
    name: str
    capability: str
    provider: str
    model: str
    base_url: str
    api_key_env: str
    enabled: bool
    note: str | None


@dataclass(frozen=True, slots=True)
class LegacyRegistrySnapshot:
    """完成格式和引用校验后的旧文件快照，不持有平台配置或明文凭证。"""

    entries: tuple[LegacyCatalogEntry, ...]
    bindings: dict[UUID, dict[str, str]]
    digests: dict[str, str]
    source_files: tuple[Path, ...]


def _platform_directory(source_root: Path) -> Path:
    """定位旧 JSON 的平台目录，避免扫描对象存储中的业务知识文件。"""
    return source_root / ".platform"


def _read_json(path: Path) -> Any:
    """以受控错误读取 JSON，调用方不会把原始内容回显到日志。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelRegistryMigrationError(f"无法读取迁移源文件：{path.name}") from exc


def _file_digest(path: Path) -> str:
    """返回源文件 SHA-256，用于备份和回填前后的可审计一致性核对。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_entry(row: object) -> LegacyCatalogEntry:
    """将单个旧目录对象收敛为新表允许的字段，并拒绝缺失关键标识的条目。"""
    if not isinstance(row, dict):
        raise ModelRegistryMigrationError("模型目录 entries 必须全部为对象")
    required = ("code", "name", "capability", "provider", "base_url", "api_key_env")
    if any(not isinstance(row.get(key), str) or not row[key].strip() for key in required):
        raise ModelRegistryMigrationError("模型目录条目缺少必要的非密钥字段")
    note = row.get("note")
    if note is not None and not isinstance(note, str):
        raise ModelRegistryMigrationError("模型目录备注必须是字符串或 null")
    enabled = row.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ModelRegistryMigrationError("模型目录 enabled 必须是布尔值")
    return LegacyCatalogEntry(
        code=row["code"].strip(),
        name=row["name"].strip(),
        capability=row["capability"].strip(),
        provider=row["provider"].strip(),
        model=str(row.get("model", "")).strip(),
        base_url=row["base_url"].strip(),
        api_key_env=row["api_key_env"].strip(),
        enabled=enabled,
        note=note,
    )


def load_legacy_registry(source_root: Path) -> LegacyRegistrySnapshot:
    """读取目录和应用绑定，并在任何数据库写入前校验所有内部引用。"""
    platform_directory = _platform_directory(source_root)
    catalog_path = platform_directory / "model-catalog.json"
    bindings_directory = platform_directory / "model-bindings"
    configuration_path = platform_directory / "model-configuration.json"
    if configuration_path.is_file():
        configuration = _read_json(configuration_path)
        if isinstance(configuration, dict) and any(
            isinstance(configuration.get(key), str) and configuration[key].strip()
            for key in ("chat_api_key", "embedding_api_key", "web_search_api_key")
        ):
            # 不复制、不摘要、不写入含凭证文件的任何内容；先处理凭证才能安全切换来源。
            raise ModelRegistryMigrationError("旧平台配置含内联凭证，必须先迁移凭证后再回填")
    source_files = [path for path in [catalog_path] if path.is_file()]
    if bindings_directory.is_dir():
        source_files.extend(sorted(bindings_directory.glob("*.json")))
    if not source_files:
        raise ModelRegistryMigrationError("未找到模型目录或应用绑定 JSON，拒绝执行空回填")

    entries: tuple[LegacyCatalogEntry, ...] = ()
    if catalog_path.is_file():
        payload = _read_json(catalog_path)
        rows = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ModelRegistryMigrationError("model-catalog.json 必须包含 entries 数组")
        entries = tuple(_parse_entry(row) for row in rows)
    entry_by_code = {entry.code: entry for entry in entries}
    if len(entry_by_code) != len(entries):
        raise ModelRegistryMigrationError("模型目录存在重复 code，无法确定性回填")

    bindings: dict[UUID, dict[str, str]] = {}
    for path in source_files:
        if path == catalog_path:
            continue
        try:
            application_id = UUID(path.stem)
        except ValueError as exc:
            raise ModelRegistryMigrationError("模型绑定文件名必须是应用 UUID") from exc
        payload = _read_json(path)
        if not isinstance(payload, dict):
            raise ModelRegistryMigrationError("模型绑定文件必须是能力到目录 code 的对象")
        binding: dict[str, str] = {}
        for capability, code in payload.items():
            if not isinstance(capability, str) or not isinstance(code, str) or not code:
                raise ModelRegistryMigrationError("模型绑定必须使用非空字符串能力和 code")
            entry = entry_by_code.get(code)
            if entry is None:
                raise ModelRegistryMigrationError("模型绑定引用了目录中不存在的 code")
            if entry.capability != capability:
                raise ModelRegistryMigrationError("模型绑定能力与目录条目能力不匹配")
            binding[capability] = code
        bindings[application_id] = binding

    return LegacyRegistrySnapshot(
        entries=entries,
        bindings=bindings,
        digests={str(path.relative_to(source_root)): _file_digest(path) for path in source_files},
        source_files=tuple(source_files),
    )


def backup_legacy_source(
    source_root: Path, snapshot: LegacyRegistrySnapshot, backup_root: Path
) -> Path:
    """复制已校验源文件并保存摘要清单；备份完成后才允许执行数据库回填。"""
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_directory = backup_root / f"model-registry-{timestamp}"
    for path in snapshot.source_files:
        destination = backup_directory / path.relative_to(source_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    manifest = {
        "entryCount": len(snapshot.entries),
        "bindingApplicationCount": len(snapshot.bindings),
        "sha256": snapshot.digests,
    }
    (backup_directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return backup_directory


def _same_entry(record: ModelCatalogRecord, entry: LegacyCatalogEntry) -> bool:
    """判断既有结构化目录是否等价，防止回填工具静默覆盖新写入。"""
    return (
        record.name == entry.name
        and record.capability == entry.capability
        and record.provider == entry.provider
        and record.model == entry.model
        and record.base_url == entry.base_url
        and record.api_key_env == entry.api_key_env
        and record.enabled == entry.enabled
        and record.note == entry.note
    )


async def apply_legacy_registry(
    snapshot: LegacyRegistrySnapshot, session: AsyncSession
) -> dict[str, int]:
    """在一个事务内插入缺失目录和应用默认绑定，冲突时整体回滚。"""
    application_ids = set(snapshot.bindings)
    applications = list(
        await session.scalars(select(Application).where(Application.id.in_(application_ids)))
    ) if application_ids else []
    if {application.id for application in applications} != application_ids:
        raise ModelRegistryMigrationError("模型绑定包含数据库中不存在的应用，拒绝部分回填")

    existing_records = list(
        await session.scalars(select(ModelCatalogRecord).where(ModelCatalogRecord.code.in_([
            entry.code for entry in snapshot.entries
        ])))
    ) if snapshot.entries else []
    records_by_code = {record.code: record for record in existing_records}
    created_entries = 0
    for entry in snapshot.entries:
        record = records_by_code.get(entry.code)
        if record is not None:
            if not _same_entry(record, entry):
                raise ModelRegistryMigrationError("数据库目录与旧 JSON 同 code 内容不同，拒绝覆盖")
            continue
        record = ModelCatalogRecord(
            code=entry.code,
            name=entry.name,
            capability=entry.capability,
            provider=entry.provider,
            model=entry.model,
            base_url=entry.base_url,
            api_key_env=entry.api_key_env,
            enabled=entry.enabled,
            note=entry.note,
        )
        session.add(record)
        records_by_code[entry.code] = record
        created_entries += 1
    await session.flush()

    created_bindings = 0
    for application_id, binding in snapshot.bindings.items():
        existing_bindings = list(
            await session.scalars(
                select(ApplicationModelBindingRecord).where(
                    ApplicationModelBindingRecord.application_id == application_id,
                    ApplicationModelBindingRecord.environment_id.is_(None),
                )
            )
        )
        existing_by_capability = {item.capability: item for item in existing_bindings}
        for capability, code in binding.items():
            record = records_by_code[code]
            existing = existing_by_capability.get(capability)
            if existing is not None:
                if existing.catalog_entry_id != record.id:
                    raise ModelRegistryMigrationError("数据库应用绑定与旧 JSON 不同，拒绝覆盖")
                continue
            session.add(
                ApplicationModelBindingRecord(
                    application_id=application_id,
                    environment_id=None,
                    catalog_entry_id=record.id,
                    capability=capability,
                )
            )
            created_bindings += 1
    return {"createdEntries": created_entries, "createdBindings": created_bindings}


async def _run_apply(snapshot: LegacyRegistrySnapshot) -> dict[str, int]:
    """为命令行 apply 创建一次性连接，避免迁移脚本残留跨事件循环连接。"""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session, session.begin():
            return await apply_legacy_registry(snapshot, session)
    finally:
        await engine.dispose()


def main() -> int:
    """默认输出不含敏感值的预检摘要，只有显式 --apply 才会备份并写入数据库。"""
    parser = argparse.ArgumentParser(description="安全回填旧模型目录与应用绑定")
    parser.add_argument("--source-root", type=Path, default=Path(settings.object_storage_path))
    parser.add_argument("--backup-root", type=Path)
    parser.add_argument("--apply", action="store_true", help="完成备份后写入结构化模型注册表")
    arguments = parser.parse_args()
    try:
        snapshot = load_legacy_registry(arguments.source_root)
    except ModelRegistryMigrationError as exc:
        # 预检失败只给出原因，不回显无法信任或可能含敏感字段的源内容。
        print(json.dumps({"applied": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    summary: dict[str, object] = {
        "entryCount": len(snapshot.entries),
        "bindingApplicationCount": len(snapshot.bindings),
        "sourceDigests": snapshot.digests,
        "applied": False,
    }
    if arguments.apply:
        backup_root = arguments.backup_root or (
            arguments.source_root / ".platform" / "migration-backups"
        )
        backup_directory = backup_legacy_source(arguments.source_root, snapshot, backup_root)
        summary["backupDirectory"] = str(backup_directory)
        summary |= asyncio.run(_run_apply(snapshot))
        summary["applied"] = True
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
