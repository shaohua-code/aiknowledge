from __future__ import annotations

import asyncio
import logging
from dataclasses import fields
from datetime import UTC, datetime
from uuid import UUID

from knowledge_core.domains.knowledge.ingestion import chunk_blocks, parse_content
from knowledge_core.domains.knowledge.repository import KnowledgeRepository
from knowledge_core.infrastructure.database import AsyncSessionFactory
from knowledge_core.infrastructure.http_safety import RemoteFetchRequest, fetch_public_source
from knowledge_core.infrastructure.providers import get_embedding_provider
from knowledge_core.infrastructure.storage import storage
from knowledge_core.shared.context import ApplicationContext
from knowledge_core.shared.errors import CoreError, NotFoundError
from knowledge_core.shared.request_id import set_request_id
from knowledge_core.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
WORKER_KEY_ID = UUID(int=0)


@celery_app.task(bind=True, name="knowledge.ingest_document", max_retries=3)
def ingest_document(self, run_id: str, application_id: str, environment_id: str) -> dict:
    try:
        return asyncio.run(_ingest(UUID(run_id), UUID(application_id), UUID(environment_id)))
    except CoreError as exc:
        if not exc.retryable:
            raise
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
        try:
            run.status = "running"
            run.stage = "fetching" if not revision.storage_key else "parsing"
            run.progress = 5 if not revision.storage_key else 10
            run.started_at = datetime.now(UTC)
            if document.current_version is None:
                document.status = "processing"
            revision.status = "processing"
            await session.commit()

            if not revision.storage_key:
                if not document.source_id:
                    raise ValueError("文档版本缺少内容文件，也没有可重新抓取的数据源")
                source = await repository.get_source(context, document.source_id)
                if not source or source.source_type not in {"auto", "web", "api", "feed", "text"}:
                    raise ValueError("远程数据源不存在、已被删除或类型不支持")
                allowed_fields = {item.name for item in fields(RemoteFetchRequest)}
                configuration = {
                    key: value
                    for key, value in source.configuration.items()
                    if key in allowed_fields
                }
                result = await fetch_public_source(RemoteFetchRequest(**configuration))
                revision.storage_key = storage.write(
                    application_id,
                    environment_id,
                    document.id,
                    revision.id,
                    result.extension,
                    result.content,
                )
                revision.content_hash = result.content_hash
                revision.metadata_ = {
                    **revision.metadata_,
                    "remoteFetch": {
                        "statusCode": result.status_code,
                        "sizeBytes": result.size_bytes,
                        "contentType": result.mime_type,
                        "attempts": result.attempts,
                    },
                }
                document.mime_type = result.mime_type
                source.last_synced_at = datetime.now(UTC)
                run.stage = "parsing"
                run.progress = 20
                await session.commit()

            content = storage.read(revision.storage_key)
            blocks = parse_content(content, document.mime_type, revision.storage_key)
            run.stage = "chunking"
            run.progress = 40
            await session.commit()

            chunks = chunk_blocks(blocks)
            run.stage = "embedding"
            run.progress = 65
            await session.commit()

            embeddings = await get_embedding_provider().embed(
                [chunk["content"] for chunk in chunks]
            )
            for chunk, embedding in zip(chunks, embeddings, strict=True):
                chunk["embedding"] = embedding

            run.stage = "indexing"
            run.progress = 88
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
                error_code, error_message = _describe_ingestion_error(exc)
                run.error_code = error_code
                run.error_message = error_message[:2000]
                run.completed_at = datetime.now(UTC)
                run.retry_count += 1
            if document and document.current_version is None:
                document.status = "failed"
            if revision:
                revision.status = "failed"
            await session.commit()
            logger.exception("入库任务失败 run_id=%s", run_id)
            raise


def _describe_ingestion_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, CoreError):
        parts = [exc.message]
        if exc.suggestion:
            parts.append(f"处理建议：{exc.suggestion}")
        if exc.details.get("statusCode"):
            parts.append(f"HTTP 状态：{exc.details['statusCode']}")
        return exc.code, "；".join(parts)
    if isinstance(exc, ValueError):
        return "INGESTION_CONTENT_INVALID", f"内容处理失败：{exc}。请检查数据格式后重试。"
    if isinstance(exc, FileNotFoundError):
        return (
            "INGESTION_STORAGE_MISSING",
            "抓取内容文件不存在。请重新提交数据源，或检查对象存储挂载。",
        )
    return (
        "INGESTION_INTERNAL_ERROR",
        "入库任务发生未预期错误。请复制请求 ID，在 Worker 日志中查询对应记录。",
    )
