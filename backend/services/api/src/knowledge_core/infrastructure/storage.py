from __future__ import annotations

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


storage = LocalObjectStorage()
