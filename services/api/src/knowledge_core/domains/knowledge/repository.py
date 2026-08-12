from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.infrastructure.models import (
    AuditEvent,
    Document,
    DocumentChunk,
    DocumentRevision,
    IngestionRun,
    KnowledgeCollection,
    Source,
)
from knowledge_core.shared.context import ApplicationContext


class KnowledgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_collection(
        self, application_id: UUID, environment_id: UUID, collection_id: UUID
    ) -> KnowledgeCollection | None:
        return await self.session.scalar(
            select(KnowledgeCollection).where(
                KnowledgeCollection.id == collection_id,
                KnowledgeCollection.application_id == application_id,
                KnowledgeCollection.environment_id == environment_id,
            )
        )

    async def list_collections(
        self, application_id: UUID, environment_id: UUID
    ) -> list[KnowledgeCollection]:
        result = await self.session.scalars(
            select(KnowledgeCollection)
            .where(
                KnowledgeCollection.application_id == application_id,
                KnowledgeCollection.environment_id == environment_id,
            )
            .order_by(KnowledgeCollection.created_at.desc())
        )
        return list(result)

    async def create_collection(self, **values: object) -> KnowledgeCollection:
        collection = KnowledgeCollection(**values)
        self.session.add(collection)
        await self.session.flush()
        return collection

    async def create_source(self, **values: object) -> Source:
        source = Source(**values)
        self.session.add(source)
        await self.session.flush()
        return source

    async def create_document(self, **values: object) -> Document:
        document = Document(**values)
        self.session.add(document)
        await self.session.flush()
        return document

    async def create_revision(self, **values: object) -> DocumentRevision:
        revision = DocumentRevision(**values)
        self.session.add(revision)
        await self.session.flush()
        return revision

    async def create_run(self, **values: object) -> IngestionRun:
        run = IngestionRun(**values)
        self.session.add(run)
        await self.session.flush()
        return run

    async def create_audit_event(self, **values: object) -> AuditEvent:
        event = AuditEvent(**values)
        self.session.add(event)
        await self.session.flush()
        return event

    async def list_documents(
        self, application_id: UUID, environment_id: UUID, collection_id: UUID
    ) -> list[Document]:
        result = await self.session.scalars(
            select(Document)
            .where(
                Document.application_id == application_id,
                Document.environment_id == environment_id,
                Document.collection_id == collection_id,
            )
            .order_by(Document.created_at.desc())
        )
        return list(result)

    async def list_runs(
        self, application_id: UUID, environment_id: UUID, *, limit: int = 100
    ) -> list[IngestionRun]:
        result = await self.session.scalars(
            select(IngestionRun)
            .where(
                IngestionRun.application_id == application_id,
                IngestionRun.environment_id == environment_id,
            )
            .order_by(IngestionRun.created_at.desc())
            .limit(limit)
        )
        return list(result)

    async def get_run(self, context: ApplicationContext, run_id: UUID) -> IngestionRun | None:
        return await self.session.scalar(
            select(IngestionRun).where(
                IngestionRun.id == run_id,
                IngestionRun.application_id == context.application_id,
                IngestionRun.environment_id == context.environment_id,
            )
        )

    async def get_run_by_ids(
        self, application_id: UUID, environment_id: UUID, run_id: UUID
    ) -> IngestionRun | None:
        return await self.session.scalar(
            select(IngestionRun).where(
                IngestionRun.id == run_id,
                IngestionRun.application_id == application_id,
                IngestionRun.environment_id == environment_id,
            )
        )

    async def get_document(self, context: ApplicationContext, document_id: UUID) -> Document | None:
        return await self.session.scalar(
            select(Document).where(
                Document.id == document_id,
                Document.application_id == context.application_id,
                Document.environment_id == context.environment_id,
            )
        )

    async def get_revision(
        self, context: ApplicationContext, revision_id: UUID
    ) -> DocumentRevision | None:
        return await self.session.scalar(
            select(DocumentRevision).where(
                DocumentRevision.id == revision_id,
                DocumentRevision.application_id == context.application_id,
                DocumentRevision.environment_id == context.environment_id,
            )
        )

    async def replace_chunks(
        self, context: ApplicationContext, revision_id: UUID, chunks: list[dict]
    ) -> None:
        await self.session.execute(
            delete(DocumentChunk).where(
                DocumentChunk.revision_id == revision_id,
                DocumentChunk.application_id == context.application_id,
                DocumentChunk.environment_id == context.environment_id,
            )
        )
        self.session.add_all(
            [
                DocumentChunk(
                    application_id=context.application_id,
                    environment_id=context.environment_id,
                    revision_id=revision_id,
                    **chunk,
                )
                for chunk in chunks
            ]
        )

    async def publish_revision(
        self,
        context: ApplicationContext,
        document: Document,
        revision: DocumentRevision,
        run: IngestionRun,
        *,
        chunk_count: int,
        char_count: int,
    ) -> None:
        now = datetime.now(UTC)
        revision.status = "ready"
        revision.char_count = char_count
        revision.published_at = now
        document.status = "ready"
        document.current_version = revision.version
        document.storage_key = revision.storage_key
        run.status = "succeeded"
        run.stage = "published"
        run.progress = 100
        run.completed_at = now
        collection = await self.get_collection(
            context.application_id, context.environment_id, document.collection_id
        )
        if collection:
            collection.document_count = int(
                await self.session.scalar(
                    select(func.count(Document.id)).where(
                        Document.application_id == context.application_id,
                        Document.environment_id == context.environment_id,
                        Document.collection_id == collection.id,
                        Document.status == "ready",
                    )
                )
                or 0
            )
            collection.chunk_count = int(
                await self.session.scalar(
                    select(func.count(DocumentChunk.id))
                    .join(DocumentRevision, DocumentRevision.id == DocumentChunk.revision_id)
                    .join(Document, Document.id == DocumentRevision.document_id)
                    .where(
                        DocumentChunk.application_id == context.application_id,
                        DocumentChunk.environment_id == context.environment_id,
                        Document.collection_id == collection.id,
                        DocumentRevision.status == "ready",
                    )
                )
                or 0
            )
            collection.last_published_at = now
