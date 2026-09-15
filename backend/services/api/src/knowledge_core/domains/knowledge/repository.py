from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.infrastructure.models import (
    AuditEvent,
    Document,
    DocumentChunk,
    DocumentRevision,
    IngestionAttempt,
    IngestionEmbeddingCheckpoint,
    IngestionRun,
    KnowledgeCollection,
    Source,
    TransactionalOutbox,
)
from knowledge_core.shared.context import ApplicationContext
from knowledge_core.shared.errors import ConflictError


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

    async def create_outbox_event(self, **values: object) -> TransactionalOutbox:
        """与领域变更共用当前事务保存待派发消息，避免提交后投递丢失。"""
        event = TransactionalOutbox(**values)
        self.session.add(event)
        await self.session.flush()
        return event

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

    async def get_document_in_context(
        self, application_id: UUID, environment_id: UUID, document_id: UUID
    ) -> Document | None:
        """控制面读取文档详情时固定应用和环境，不能仅按全局文档 ID 查询。"""
        return await self.session.scalar(
            select(Document).where(
                Document.id == document_id,
                Document.application_id == application_id,
                Document.environment_id == environment_id,
            )
        )

    async def list_document_revisions(
        self, application_id: UUID, environment_id: UUID, document_id: UUID
    ) -> list[DocumentRevision]:
        """按版本倒序列出同一隔离上下文中的文档修订历史。"""
        result = await self.session.scalars(
            select(DocumentRevision)
            .where(
                DocumentRevision.application_id == application_id,
                DocumentRevision.environment_id == environment_id,
                DocumentRevision.document_id == document_id,
            )
            .order_by(DocumentRevision.version.desc())
        )
        return list(result)

    async def get_document_revision(
        self,
        application_id: UUID,
        environment_id: UUID,
        document_id: UUID,
        revision_id: UUID,
    ) -> DocumentRevision | None:
        """读取指定文档的一个版本，避免跨文档 ID 直接查看片段。"""
        return await self.session.scalar(
            select(DocumentRevision).where(
                DocumentRevision.id == revision_id,
                DocumentRevision.application_id == application_id,
                DocumentRevision.environment_id == environment_id,
                DocumentRevision.document_id == document_id,
            )
        )

    async def list_revision_chunks(
        self, application_id: UUID, environment_id: UUID, revision_id: UUID, *, limit: int
    ) -> list[DocumentChunk]:
        """只返回目标版本的有限片段预览，防止控制台一次加载整份超大文档。"""
        result = await self.session.scalars(
            select(DocumentChunk)
            .where(
                DocumentChunk.application_id == application_id,
                DocumentChunk.environment_id == environment_id,
                DocumentChunk.revision_id == revision_id,
            )
            .order_by(DocumentChunk.chunk_index)
            .limit(limit)
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

    async def claim_queued_run(self, context: ApplicationContext, run_id: UUID) -> bool:
        """原子领取排队入库任务，阻止重复 Celery 消息并行处理同一版本。"""
        claimed = await self.session.execute(
            update(IngestionRun)
            .where(
                IngestionRun.id == run_id,
                IngestionRun.application_id == context.application_id,
                IngestionRun.environment_id == context.environment_id,
                IngestionRun.status == "queued",
            )
            .values(status="running", started_at=datetime.now(UTC))
        )
        return claimed.rowcount == 1

    async def create_ingestion_attempt(
        self, context: ApplicationContext, run_id: UUID
    ) -> IngestionAttempt:
        """在已领取任务的事务中创建一次执行历史，便于审计失败与恢复。"""
        latest_attempt = await self.session.scalar(
            select(func.coalesce(func.max(IngestionAttempt.attempt_number), 0)).where(
                IngestionAttempt.run_id == run_id
            )
        )
        attempt = IngestionAttempt(
            application_id=context.application_id,
            environment_id=context.environment_id,
            run_id=run_id,
            attempt_number=int(latest_attempt or 0) + 1,
            status="running",
            stage="received",
            started_at=datetime.now(UTC),
        )
        self.session.add(attempt)
        await self.session.flush()
        return attempt

    async def get_ingestion_attempt(
        self, context: ApplicationContext, attempt_id: UUID
    ) -> IngestionAttempt | None:
        """按应用与环境读取已提交的执行历史，避免错误路径跨上下文更新。"""
        return await self.session.scalar(
            select(IngestionAttempt).where(
                IngestionAttempt.id == attempt_id,
                IngestionAttempt.application_id == context.application_id,
                IngestionAttempt.environment_id == context.environment_id,
            )
        )

    async def get_embedding_checkpoint(
        self,
        context: ApplicationContext,
        run_id: UUID,
        batch_index: int,
        content_digest: str,
        embedding_fingerprint: str,
    ) -> IngestionEmbeddingCheckpoint | None:
        """仅返回与当前内容和模型空间一致的已完成 Embedding 批次。"""
        return await self.session.scalar(
            select(IngestionEmbeddingCheckpoint).where(
                IngestionEmbeddingCheckpoint.application_id == context.application_id,
                IngestionEmbeddingCheckpoint.environment_id == context.environment_id,
                IngestionEmbeddingCheckpoint.run_id == run_id,
                IngestionEmbeddingCheckpoint.batch_index == batch_index,
                IngestionEmbeddingCheckpoint.content_digest == content_digest,
                IngestionEmbeddingCheckpoint.embedding_fingerprint == embedding_fingerprint,
            )
        )

    async def save_embedding_checkpoint(
        self,
        context: ApplicationContext,
        *,
        run_id: UUID,
        batch_index: int,
        chunk_start: int,
        chunk_count: int,
        content_digest: str,
        embedding_fingerprint: str,
        embeddings: list[list[float]],
    ) -> IngestionEmbeddingCheckpoint:
        """保存一个已验证的批次结果；调用方负责在短事务内提交。"""
        checkpoint = await self.session.scalar(
            select(IngestionEmbeddingCheckpoint).where(
                IngestionEmbeddingCheckpoint.application_id == context.application_id,
                IngestionEmbeddingCheckpoint.environment_id == context.environment_id,
                IngestionEmbeddingCheckpoint.run_id == run_id,
                IngestionEmbeddingCheckpoint.batch_index == batch_index,
            )
        )
        values = {
            "chunk_start": chunk_start,
            "chunk_count": chunk_count,
            "content_digest": content_digest,
            "embedding_fingerprint": embedding_fingerprint,
            "embeddings": embeddings,
        }
        if checkpoint is None:
            checkpoint = IngestionEmbeddingCheckpoint(
                application_id=context.application_id,
                environment_id=context.environment_id,
                run_id=run_id,
                batch_index=batch_index,
                **values,
            )
            self.session.add(checkpoint)
        else:
            for key, value in values.items():
                setattr(checkpoint, key, value)
        await self.session.flush()
        return checkpoint

    async def get_document(self, context: ApplicationContext, document_id: UUID) -> Document | None:
        return await self.session.scalar(
            select(Document).where(
                Document.id == document_id,
                Document.application_id == context.application_id,
                Document.environment_id == context.environment_id,
            )
        )

    async def get_source(self, context: ApplicationContext, source_id: UUID) -> Source | None:
        return await self.session.scalar(
            select(Source).where(
                Source.id == source_id,
                Source.application_id == context.application_id,
                Source.environment_id == context.environment_id,
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
        # 使用条件更新而非内存对象赋值：较旧任务在较新版本发布后完成时，
        # rowcount 会为 0，整个事务回滚，不能把 document.current_version 倒退。
        publication = await self.session.execute(
            update(Document)
            .where(
                Document.id == document.id,
                Document.application_id == context.application_id,
                Document.environment_id == context.environment_id,
                or_(
                    Document.current_version.is_(None),
                    Document.current_version < revision.version,
                ),
            )
            .values(
                status="ready",
                current_version=revision.version,
                storage_key=revision.storage_key,
            )
        )
        if publication.rowcount != 1:
            raise ConflictError("文档已有更新版本发布，当前旧任务不能覆盖活动版本")
        revision.status = "ready"
        revision.char_count = char_count
        revision.published_at = now
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
                        # 旧修订可保持 ready 供审计，但集合概览只反映当前可检索版本。
                        DocumentRevision.version == Document.current_version,
                    )
                )
                or 0
            )
            collection.last_published_at = now

    async def rollback_revision(
        self,
        context: ApplicationContext,
        document: Document,
        revision: DocumentRevision,
        *,
        current_embedding_fingerprint: str,
    ) -> None:
        """将文档活动指针切回已有完整版本，不删除任何历史对象。"""
        if revision.status != "ready":
            raise ConflictError("只有已完成入库的版本可以回滚")
        if revision.embedding_fingerprint != current_embedding_fingerprint:
            raise ConflictError("目标版本使用了其他 Embedding 模型，请先用当前模型重新入库")
        chunk_exists = await self.session.scalar(
            select(DocumentChunk.id)
            .where(
                DocumentChunk.application_id == context.application_id,
                DocumentChunk.environment_id == context.environment_id,
                DocumentChunk.revision_id == revision.id,
                DocumentChunk.embedding.is_not(None),
            )
            .limit(1)
        )
        if chunk_exists is None:
            raise ConflictError("目标版本没有完整知识片段，不能回滚")
        changed = await self.session.execute(
            update(Document)
            .where(
                Document.id == document.id,
                Document.application_id == context.application_id,
                Document.environment_id == context.environment_id,
                Document.status != "archived",
            )
            .values(
                status="ready",
                current_version=revision.version,
                storage_key=revision.storage_key,
            )
        )
        if changed.rowcount != 1:
            raise ConflictError("文档已归档或发生并发变更，请刷新后重试")
        # 回滚后重新计算集合概览，旧版本仍保留 ready 供审计和再次切换。
        collection = await self.get_collection(
            context.application_id, context.environment_id, document.collection_id
        )
        if collection:
            collection.chunk_count = int(
                await self.session.scalar(
                    select(func.count(DocumentChunk.id))
                    .join(DocumentRevision, DocumentRevision.id == DocumentChunk.revision_id)
                    .join(Document, Document.id == DocumentRevision.document_id)
                    .where(
                        DocumentChunk.application_id == context.application_id,
                        DocumentChunk.environment_id == context.environment_id,
                        Document.collection_id == collection.id,
                        Document.status == "ready",
                        DocumentRevision.status == "ready",
                        DocumentRevision.version == Document.current_version,
                    )
                )
                or 0
            )
            collection.last_published_at = datetime.now(UTC)
