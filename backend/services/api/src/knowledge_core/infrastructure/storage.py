from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from knowledge_core.config import settings


class LocalObjectStorage:
    def __init__(self, root: str | Path = settings.object_storage_path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("对象存储路径越界")
        return path

    def write(
        self,
        application_id: UUID,
        environment_id: UUID,
        document_id: UUID,
        revision_id: UUID,
        extension: str,
        content: bytes,
    ) -> str:
        safe_extension = (
            extension.lower()
            if extension.lower()
            in {".pdf", ".docx", ".txt", ".md", ".html", ".json", ".xml", ".csv"}
            else ""
        )
        key = (
            f"app/{application_id}/env/{environment_id}/"
            f"doc/{document_id}/{revision_id}{safe_extension}"
        )
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return key

    def read(self, key: str) -> bytes:
        return self._resolve(key).read_bytes()

    def cleanup_orphans(self, referenced_keys: set[str], *, before: datetime) -> list[str]:
        """删除已过保留期且无数据库引用的应用对象，永不扫描平台配置目录。"""
        application_root = self.root / "app"
        if not application_root.is_dir():
            return []
        deleted: list[str] = []
        for path in application_root.rglob("*"):
            if not path.is_file():
                continue
            key = path.relative_to(self.root).as_posix()
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if key in referenced_keys or modified_at >= before:
                continue
            # 仅删除已由相对路径边界校验过的常规文件，避免清理任务越出对象存储根目录。
            self._resolve(key).unlink()
            deleted.append(key)
        # 由内向外移除清理后留下的空目录，根目录和 app 前缀本身始终保留。
        directories = sorted(
            application_root.rglob("*"), key=lambda item: len(item.parts), reverse=True
        )
        for directory in directories:
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        return deleted


storage = LocalObjectStorage()
