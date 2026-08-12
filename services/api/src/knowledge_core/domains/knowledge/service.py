from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.domains.knowledge.repository import KnowledgeRepository
from knowledge_core.domains.knowledge.schemas import CollectionCreate
from knowledge_core.infrastructure.http_safety import fetch_public_source
from knowledge_core.infrastructure.models import DocumentRevision, KnowledgeCollection
from knowledge_core.infrastructure.storage import storage
from knowledge_core.shared.context import ApplicationContext
from knowledge_core.shared.errors import ConflictError, NotFoundError, ProviderUnavailableError
from knowledge_core.shared.request_id import get_request_id

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}


class KnowledgeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = KnowledgeRepository(session)

    async def create_collection(
        self, application_id: UUID, environment_id: UUID, payload: CollectionCreate
    ) -> KnowledgeCollection:
        existing = await self.session.scalar(
            select(KnowledgeCollection).where(
                KnowledgeCollection.application_id == application_id,
                KnowledgeCollection.environment_id == environment_id,
                KnowledgeCollection.code == payload.code,
            )
        )
        if existing:
            raise ConflictError("知识集合编码已存在")
        collection = await self.repository.create_collection(
            application_id=application_id,
            environment_id=environment_id,
            status="active",
            **payload.model_dump(),
        )
        await self.session.commit()
        await self.session.refresh(collection)
        return collection

    async def create_file_document(
        self,
        application_id: UUID,
        environment_id: UUID,
        collection_id: UUID,
        file: UploadFile,
    ):
        filename = file.filename or "未命名文件"
        extension = Path(filename).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise ConflictError(f"不支持的文件类型：{extension or '无扩展名'}")
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise ConflictError("文件大小不能超过 20MB")
        return await self._create_document(
            application_id,
            environment_id,
            collection_id,
            title=filename,
            mime_type=file.content_type or "application/octet-stream",
            extension=extension,
            content=content,
            source_type="file",
        )

    async def create_text_document(
        self,
        application_id: UUID,
        environment_id: UUID,
        collection_id: UUID,
        *,
        title: str,
        content: str,
    ):
        return await self._create_document(
            application_id,
            environment_id,
            collection_id,
            title=title,
            mime_type="text/plain",
            extension=".txt",
            content=content.encode("utf-8"),
            source_type="manual",
        )

    async def create_remote_document(
        self,
        application_id: UUID,
        environment_id: UUID,
        collection_id: UUID,
        *,
        title: str,
        url: str,
        source_type: str,
    ):
        content, mime_type, extension = await fetch_public_source(url, source_type)
        return await self._create_document(
            application_id,
            environment_id,
            collection_id,
            title=title,
            mime_type=mime_type,
            extension=extension,
            content=content,
            source_type=source_type,
            source_url=url,
        )

    async def create_file_revision(
        self,
        application_id: UUID,
        environment_id: UUID,
        document_id: UUID,
        file: UploadFile,
    ):
        filename = file.filename or "未命名文件"
        extension = Path(filename).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise ConflictError(f"不支持的文件类型：{extension or '无扩展名'}")
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise ConflictError("文件大小不能超过 20MB")
        return await self._create_revision(
            application_id,
            environment_id,
            document_id,
            mime_type=file.content_type or "application/octet-stream",
            extension=extension,
            content=content,
        )

    async def create_text_revision(
        self,
        application_id: UUID,
        environment_id: UUID,
        document_id: UUID,
        content: str,
    ):
        return await self._create_revision(
            application_id,
            environment_id,
            document_id,
            mime_type="text/plain",
            extension=".txt",
            content=content.encode(),
        )

    async def _create_revision(
        self,
        application_id: UUID,
        environment_id: UUID,
        document_id: UUID,
        *,
        mime_type: str,
        extension: str,
        content: bytes,
    ):
        context = ApplicationContext(
            application_id=application_id,
            environment_id=environment_id,
            application_code="control",
            environment_code="control",
            api_key_id=UUID(int=0),
            scopes=frozenset(),
        )
        document = await self.repository.get_document(context, document_id)
        if not document or document.status == "archived":
            raise NotFoundError("文档不存在或已归档")
        next_version = (
            int(
                await self.session.scalar(
                    select(func.coalesce(func.max(DocumentRevision.version), 0)).where(
                        DocumentRevision.application_id == application_id,
                        DocumentRevision.environment_id == environment_id,
                        DocumentRevision.document_id == document.id,
                    )
                )
                or 0
            )
            + 1
        )
        revision = await self.repository.create_revision(
            application_id=application_id,
            environment_id=environment_id,
            document_id=document.id,
            version=next_version,
            content_hash=hashlib.sha256(content).hexdigest(),
            status="queued",
            metadata_={},
        )
        storage_key = storage.write(
            application_id,
            environment_id,
            document.id,
            revision.id,
            extension,
            content,
        )
        revision.storage_key = storage_key
        run = await self.repository.create_run(
            application_id=application_id,
            environment_id=environment_id,
            document_id=document.id,
            revision_id=revision.id,
            status="queued",
            stage="received",
            progress=0,
            request_id=get_request_id(),
        )
        document.mime_type = mime_type
        await self.session.commit()
        await self.session.refresh(run)
        self._enqueue(run.id, application_id, environment_id)
        return document, run

    async def _create_document(
        self,
        application_id: UUID,
        environment_id: UUID,
        collection_id: UUID,
        *,
        title: str,
        mime_type: str,
        extension: str,
        content: bytes,
        source_type: str,
        source_url: str | None = None,
    ):
        collection = await self.repository.get_collection(
            application_id, environment_id, collection_id
        )
        if not collection or collection.status != "active":
            raise NotFoundError("知识集合不存在或已归档")

        source = await self.repository.create_source(
            application_id=application_id,
            environment_id=environment_id,
            collection_id=collection_id,
            source_type=source_type,
            name=title,
            configuration={"url": source_url} if source_url else {},
            status="active",
        )
        document = await self.repository.create_document(
            application_id=application_id,
            environment_id=environment_id,
            collection_id=collection_id,
            source_id=source.id,
            title=title,
            mime_type=mime_type,
            source_url=source_url,
            status="queued",
        )
        next_version = (
            int(
                await self.session.scalar(
                    select(func.coalesce(func.max(DocumentRevision.version), 0)).where(
                        DocumentRevision.application_id == application_id,
                        DocumentRevision.environment_id == environment_id,
                        DocumentRevision.document_id == document.id,
                    )
                )
                or 0
            )
            + 1
        )
        revision = await self.repository.create_revision(
            application_id=application_id,
            environment_id=environment_id,
            document_id=document.id,
            version=next_version,
            content_hash=hashlib.sha256(content).hexdigest(),
            status="queued",
            metadata_={},
        )
        storage_key = storage.write(
            application_id,
            environment_id,
            document.id,
            revision.id,
            extension,
            content,
        )
        document.storage_key = storage_key
        revision.storage_key = storage_key
        run = await self.repository.create_run(
            application_id=application_id,
            environment_id=environment_id,
            document_id=document.id,
            revision_id=revision.id,
            status="queued",
            stage="received",
            progress=0,
            request_id=get_request_id(),
        )
        await self.session.commit()
        await self.session.refresh(document)
        await self.session.refresh(run)
        self._enqueue(run.id, application_id, environment_id)
        return document, run

    async def retry_run(self, application_id: UUID, environment_id: UUID, run_id: UUID):
        run = await self.repository.get_run_by_ids(application_id, environment_id, run_id)
        if not run:
            raise NotFoundError("入库任务不存在")
        if run.status not in {"failed", "queued"}:
            raise ConflictError("只有失败或尚未执行的入库任务可以重试")
        run.status = "queued"
        run.stage = "received"
        run.progress = 0
        run.error_code = None
        run.error_message = None
        run.started_at = None
        run.completed_at = None
        run.retry_count += 1
        run.request_id = get_request_id()
        await self.session.commit()
        self._enqueue(run.id, application_id, environment_id)
        return run

    async def archive_document(
        self, application_id: UUID, environment_id: UUID, document_id: UUID, *, actor: str
    ) -> None:
        context = ApplicationContext(
            application_id=application_id,
            environment_id=environment_id,
            application_code="control",
            environment_code="control",
            api_key_id=UUID(int=0),
            scopes=frozenset(),
        )
        document = await self.repository.get_document(context, document_id)
        if not document:
            raise NotFoundError("文档不存在")
        document.status = "archived"
        document.archived_at = datetime.now(UTC)
        await self.repository.create_audit_event(
            application_id=application_id,
            environment_id=environment_id,
            actor=actor,
            action="document.archived",
            resource_type="document",
            resource_id=document.id,
            request_id=get_request_id(),
            details={"title": document.title},
        )
        await self.session.commit()

    async def archive_collection(
        self, application_id: UUID, environment_id: UUID, collection_id: UUID, *, actor: str
    ) -> None:
        collection = await self.repository.get_collection(
            application_id, environment_id, collection_id
        )
        if not collection:
            raise NotFoundError("知识集合不存在")
        collection.status = "archived"
        await self.repository.create_audit_event(
            application_id=application_id,
            environment_id=environment_id,
            actor=actor,
            action="collection.archived",
            resource_type="knowledge_collection",
            resource_id=collection.id,
            request_id=get_request_id(),
            details={"name": collection.name},
        )
        await self.session.commit()

    def _enqueue(self, run_id: UUID, application_id: UUID, environment_id: UUID) -> None:
        try:
            from knowledge_core.workers.tasks import ingest_document

            ingest_document.delay(str(run_id), str(application_id), str(environment_id))
        except Exception as exc:
            raise ProviderUnavailableError(
                "文档已保存，但任务队列暂不可用",
                details={"runId": str(run_id)},
                suggestion="恢复 Worker 与 Redis 后重新提交该入库任务",
            ) from exc
