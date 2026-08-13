from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.domains.knowledge.ingestion import normalize_text, parse_content
from knowledge_core.domains.knowledge.repository import KnowledgeRepository
from knowledge_core.domains.knowledge.schemas import CollectionCreate, RemoteDocumentCreate
from knowledge_core.infrastructure.http_safety import (
    RemoteFetchRequest,
    fetch_public_source,
    validate_remote_request,
)
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
        payload: RemoteDocumentCreate,
    ):
        request = self._remote_fetch_request(payload)
        validate_remote_request(request)
        configuration = payload.model_dump(mode="json")
        return await self._create_document(
            application_id,
            environment_id,
            collection_id,
            title=payload.title,
            mime_type=None,
            extension="",
            content=None,
            source_type=payload.source_type,
            source_url=payload.url,
            source_configuration=configuration,
        )

    async def preview_remote_document(self, payload: RemoteDocumentCreate) -> dict:
        result = await fetch_public_source(self._remote_fetch_request(payload))
        blocks = parse_content(result.content, result.mime_type, f"preview{result.extension}")
        excerpt = normalize_text("\n\n".join(block.text for block in blocks))[:800]
        return {
            "finalUrl": result.final_url,
            "contentType": result.mime_type,
            "sizeBytes": result.size_bytes,
            "statusCode": result.status_code,
            "detectedTitle": result.detected_title,
            "excerpt": excerpt,
            "attempts": result.attempts,
        }

    @staticmethod
    def _remote_fetch_request(payload: RemoteDocumentCreate) -> RemoteFetchRequest:
        return RemoteFetchRequest(
            url=payload.url,
            source_type=payload.source_type,
            method=payload.method,
            headers=payload.headers,
            query_params=payload.query_params,
            json_body=payload.json_body,
            json_path=payload.json_path,
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

    async def refresh_remote_document(
        self,
        application_id: UUID,
        environment_id: UUID,
        document_id: UUID,
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
        if not document or document.status == "archived" or not document.source_id:
            raise NotFoundError("远程文档不存在或已归档")
        source = await self.repository.get_source(context, document.source_id)
        if not source or source.source_type not in {"auto", "web", "api", "feed", "text"}:
            raise ConflictError(
                "只有网页、API、RSS/XML、CSV 和文本数据源可以重新抓取",
                suggestion="文件或手工文本请通过新版本上传更新。",
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
            content_hash=hashlib.sha256(
                json.dumps(source.configuration, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            status="queued",
            metadata_={"refresh": True},
        )
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
        if document.current_version is None:
            document.status = "queued"
        await self.session.commit()
        await self.session.refresh(run)
        self._enqueue(run.id, application_id, environment_id)
        return document, run

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
        mime_type: str | None,
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
        mime_type: str | None,
        extension: str,
        content: bytes | None,
        source_type: str,
        source_url: str | None = None,
        source_configuration: dict | None = None,
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
            configuration=source_configuration or ({"url": source_url} if source_url else {}),
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
            content_hash=hashlib.sha256(
                content
                if content is not None
                else json.dumps(source_configuration or {}, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            status="queued",
            metadata_={},
        )
        if content is not None:
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
