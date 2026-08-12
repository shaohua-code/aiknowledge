from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from knowledge_core.domains.knowledge.ingestion import chunk_blocks, parse_content
from knowledge_core.domains.knowledge.repository import KnowledgeRepository
from knowledge_core.infrastructure.database import AsyncSessionFactory
from knowledge_core.infrastructure.providers import get_embedding_provider
from knowledge_core.infrastructure.storage import storage
from knowledge_core.shared.context import ApplicationContext
from knowledge_core.shared.errors import NotFoundError, ProviderUnavailableError
from knowledge_core.shared.request_id import set_request_id
from knowledge_core.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
WORKER_KEY_ID = UUID(int=0)


@celery_app.task(bind=True, name="knowledge.ingest_document", max_retries=3)
def ingest_document(self, run_id: str, application_id: str, environment_id: str) -> dict:
    try:
        return asyncio.run(_ingest(UUID(run_id), UUID(application_id), UUID(environment_id)))
    except ProviderUnavailableError as exc:
        raise self.retry(exc=exc, countdown=min(30 * (self.request.retries + 1), 120)) from exc


async def _ingest(run_id: UUID, application_id: UUID, environment_id: UUID) -> dict:
    context = ApplicationContext(
        application_id=application_id,
        environment_id=environment_id,
        application_code="worker",
        environment_code="worker",
        api_key_id=WORKER_KEY_ID,
        scopes=frozenset({"knowledge:write"}),
    )
    async with AsyncSessionFactory() as session:
        repository = KnowledgeRepository(session)
        run = await repository.get_run(context, run_id)
        if not run:
            raise NotFoundError("入库任务不存在或上下文不一致")
        set_request_id(run.request_id)
        document = await repository.get_document(context, run.document_id)
        revision = await repository.get_revision(context, run.revision_id)
        if not document or not revision:
            raise NotFoundError("入库任务关联的文档版本不存在")
        if not revision.storage_key:
            raise ValueError("文档版本缺少对象存储位置")

        try:
            run.status = "running"
            run.stage = "parsing"
            run.progress = 10
            run.started_at = datetime.now(UTC)
            if document.current_version is None:
                document.status = "processing"
            revision.status = "processing"
            await session.commit()

            content = storage.read(revision.storage_key)
            blocks = parse_content(content, document.mime_type, revision.storage_key)
            run.stage = "chunking"
            run.progress = 35
            await session.commit()

            chunks = chunk_blocks(blocks)
            run.stage = "embedding"
            run.progress = 60
            await session.commit()

            embeddings = await get_embedding_provider().embed(
                [chunk["content"] for chunk in chunks]
            )
            for chunk, embedding in zip(chunks, embeddings, strict=True):
                chunk["embedding"] = embedding

            run.stage = "indexing"
            run.progress = 85
            await repository.replace_chunks(context, revision.id, chunks)
            await repository.publish_revision(
                context,
                document,
                revision,
                run,
                chunk_count=len(chunks),
                char_count=sum(len(block.text) for block in blocks),
            )
            await session.commit()
            return {"runId": str(run.id), "status": "succeeded", "chunkCount": len(chunks)}
        except Exception as exc:
            await session.rollback()
            run = await repository.get_run(context, run_id)
            document = await repository.get_document(context, run.document_id) if run else None
            revision = await repository.get_revision(context, run.revision_id) if run else None
            if run:
                run.status = "failed"
                run.error_code = type(exc).__name__.upper()
                run.error_message = str(exc)[:1000]
                run.completed_at = datetime.now(UTC)
                run.retry_count += 1
            if document and document.current_version is None:
                document.status = "failed"
            if revision:
                revision.status = "failed"
            await session.commit()
            logger.exception("入库任务失败 run_id=%s", run_id)
            raise
