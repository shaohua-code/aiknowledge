from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from knowledge_core.config import settings
from knowledge_core.domains.knowledge.ingestion import chunk_blocks, parse_content
from knowledge_core.domains.knowledge.repository import KnowledgeRepository
from knowledge_core.infrastructure.http_safety import RemoteFetchRequest, fetch_public_source
from knowledge_core.infrastructure.models import (
    Document,
    DocumentRevision,
    IdempotencyRecord,
    TransactionalOutbox,
)
from knowledge_core.infrastructure.providers import (
    embedding_space_fingerprint,
    get_embedding_provider_async,
)
from knowledge_core.infrastructure.storage import storage
from knowledge_core.shared.context import ApplicationContext
from knowledge_core.shared.errors import CoreError, NotFoundError
from knowledge_core.shared.request_id import set_request_id
from knowledge_core.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
WORKER_KEY_ID = UUID(int=0)
# 单次请求不应随文档长度无限膨胀；后续检查点可复用同一批次边界。
EMBEDDING_BATCH_SIZE = 64
# 检查点只保存已完整返回的批次；None 代表当前内容或模型空间没有可安全复用的结果。
EmbeddingCheckpointLoader = Callable[[int, int, int, str], Awaitable[list[list[float]] | None]]
EmbeddingCheckpointSaver = Callable[[int, int, int, str, list[list[float]]], Awaitable[None]]


@celery_app.task(bind=True, name="knowledge.ingest_document", max_retries=3)
def ingest_document(self, run_id: str, application_id: str, environment_id: str) -> dict:
    try:
        return asyncio.run(_ingest(UUID(run_id), UUID(application_id), UUID(environment_id)))
    except CoreError as exc:
        if not exc.retryable:
            raise
        raise self.retry(exc=exc, countdown=min(30 * (self.request.retries + 1), 120)) from exc


@celery_app.task(bind=True, name="knowledge.dispatch_outbox", max_retries=3)
def dispatch_outbox(self) -> dict:
    """派发已提交的 Outbox 事件；仅在 Celery 接收成功后标记已发布。"""
    try:
        return asyncio.run(_dispatch_outbox())
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc, countdown=min(30 * (self.request.retries + 1), 120)) from exc


@celery_app.task(bind=True, name="knowledge.cleanup_orphan_objects", max_retries=3)
def cleanup_orphan_objects(self) -> dict:
    """清理超过观察窗口、且不再被文档或版本引用的对象存储文件。"""
    try:
        return asyncio.run(_cleanup_orphan_objects())
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc, countdown=min(60 * (self.request.retries + 1), 300)) from exc


async def _dispatch_outbox() -> dict:
    """扫描待派发事件，保留失败记录供后续 Celery 重试恢复。"""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            events = list(
                await session.scalars(
                    select(TransactionalOutbox)
                    .where(
                        TransactionalOutbox.published_at.is_(None),
                        (TransactionalOutbox.lease_expires_at.is_(None))
                        | (TransactionalOutbox.lease_expires_at < datetime.now(UTC)),
                    )
                    .order_by(TransactionalOutbox.created_at)
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
            )
            lease_token = str(uuid4())
            for event in events:
                event.lease_token = lease_token
                event.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
            await session.commit()
            for event in events:
                if event.topic != "knowledge.ingest_document":
                    continue
                ingest_document.delay(
                    event.payload["runId"],
                    event.payload["applicationId"],
                    event.payload["environmentId"],
                )
                event.published_at = datetime.now(UTC)
                event.lease_token = None
                event.lease_expires_at = None
                event.attempt_count += 1
            await session.commit()
            return {"published": len(events)}
    finally:
        await engine.dispose()


async def _cleanup_orphan_objects() -> dict:
    """清理过期幂等元数据和孤儿对象，网络或文件操作不持有数据库事务。"""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            # 幂等结果只需保留 24 小时；先清理元数据，避免维护表无限增长。
            expired_records = await session.execute(
                delete(IdempotencyRecord).where(
                    IdempotencyRecord.expires_at < datetime.now(UTC)
                )
            )
            await session.commit()
            document_keys = set(
                await session.scalars(
                    select(Document.storage_key).where(Document.storage_key.is_not(None))
                )
            )
            revision_keys = set(
                await session.scalars(
                    select(DocumentRevision.storage_key).where(DocumentRevision.storage_key.is_not(None))
                )
            )
        before = datetime.now(UTC) - timedelta(hours=settings.orphan_object_retention_hours)
        deleted = storage.cleanup_orphans(document_keys | revision_keys, before=before)
        return {
            "deleted": len(deleted),
            "expiredIdempotencyRecords": max(expired_records.rowcount or 0, 0),
        }
    finally:
        await engine.dispose()


async def _ingest(run_id: UUID, application_id: UUID, environment_id: UUID) -> dict:
    # Celery 任务每次都通过 asyncio.run 在新事件循环中执行；若复用全局 engine，
    # 池中连接会绑定到已关闭的旧循环，下一个任务复用时抛出 "attached to a different loop"。
    # 因此任务内使用一次性 NullPool engine，并在任务自己的循环结束前正常释放。
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            return await _run_ingestion(session, run_id, application_id, environment_id)
    finally:
        await engine.dispose()


async def _run_ingestion(
    session: AsyncSession, run_id: UUID, application_id: UUID, environment_id: UUID
) -> dict:
    context = ApplicationContext(
        application_id=application_id,
        environment_id=environment_id,
        application_code="worker",
        environment_code="worker",
        api_key_id=WORKER_KEY_ID,
        scopes=frozenset({"knowledge:write"}),
    )
    repository = KnowledgeRepository(session)
    run = await repository.get_run(context, run_id)
    if not run:
        raise NotFoundError("入库任务不存在或上下文不一致")
    set_request_id(run.request_id)
    document = await repository.get_document(context, run.document_id)
    revision = await repository.get_revision(context, run.revision_id)
    if not document or not revision:
        raise NotFoundError("入库任务关联的文档版本不存在")
    attempt_id = None
    try:
        # Broker 至少一次投递可能产生重复消息；只有 queued 任务能被原子领取，
        # 已运行或已完成任务直接返回当前状态，避免重复替换片段或倒退版本。
        if not await repository.claim_queued_run(context, run_id):
            # 当前 session 可能在领取前已读到旧状态，刷新后再向重复消息报告实际结果。
            await session.refresh(run)
            return {"runId": str(run.id), "status": run.status, "duplicate": True}
        await session.refresh(run)
        attempt = await repository.create_ingestion_attempt(context, run_id)
        attempt_id = attempt.id
        if not revision.storage_key:
            run.stage = "fetching"
            run.progress = 5
        else:
            run.stage = "parsing"
            run.progress = 10
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

        embedding_provider = await get_embedding_provider_async(
            session, application_id, environment_id
        )
        embedding_fingerprint = embedding_space_fingerprint(embedding_provider)
        # 指纹随版本一起保存，检索可据此隔离不同模型的向量空间。
        revision.embedding_fingerprint = embedding_fingerprint

        async def load_checkpoint(
            batch_index: int, chunk_start: int, chunk_count: int, content_digest: str
        ) -> list[list[float]] | None:
            """只复用当前任务、内容和模型指纹均匹配的完整批次。"""
            checkpoint = await repository.get_embedding_checkpoint(
                context,
                run_id,
                batch_index,
                content_digest,
                embedding_fingerprint,
            )
            if checkpoint is None or checkpoint.chunk_start != chunk_start:
                return None
            if checkpoint.chunk_count != chunk_count:
                return None
            return checkpoint.embeddings

        async def save_checkpoint(
            batch_index: int,
            chunk_start: int,
            chunk_count: int,
            content_digest: str,
            embeddings: list[list[float]],
        ) -> None:
            """每个网络批次完成后立即短事务提交，崩溃后仍可从下一批恢复。"""
            await repository.save_embedding_checkpoint(
                context,
                run_id=run_id,
                batch_index=batch_index,
                chunk_start=chunk_start,
                chunk_count=chunk_count,
                content_digest=content_digest,
                embedding_fingerprint=embedding_fingerprint,
                embeddings=embeddings,
            )
            await session.commit()

        await _embed_chunks(
            chunks,
            embedding_provider,
            load_checkpoint=load_checkpoint,
            save_checkpoint=save_checkpoint,
        )

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
        attempt.status = "succeeded"
        attempt.stage = "published"
        attempt.completed_at = datetime.now(UTC)
        await session.commit()
        return {"runId": str(run.id), "status": "succeeded", "chunkCount": len(chunks)}
    except Exception as exc:
        await session.rollback()
        run = await repository.get_run(context, run_id)
        document = await repository.get_document(context, run.document_id) if run else None
        revision = await repository.get_revision(context, run.revision_id) if run else None
        attempt = (
            await repository.get_ingestion_attempt(context, attempt_id) if attempt_id else None
        )
        error_code, error_message = _describe_ingestion_error(exc)
        if run:
            run.status = "failed"
            run.error_code = error_code
            run.error_message = error_message[:2000]
            run.completed_at = datetime.now(UTC)
            run.retry_count += 1
        if document and document.current_version is None:
            document.status = "failed"
        if revision:
            revision.status = "failed"
        if attempt:
            attempt.status = "failed"
            attempt.stage = run.stage if run else "failed"
            attempt.error_code = error_code
            attempt.error_message = error_message[:2000]
            attempt.completed_at = datetime.now(UTC)
        await session.commit()
        logger.exception("入库任务失败 run_id=%s", run_id)
        raise


def _embedding_batch_digest(batch: list[dict[str, Any]]) -> str:
    """将本批文本以稳定 JSON 编码摘要，避免简单字符串拼接产生边界碰撞。"""
    encoded = json.dumps(
        [str(chunk["content"]) for chunk in batch], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def _embed_chunks(
    chunks: list[dict[str, Any]],
    provider: Any,
    *,
    batch_size: int = EMBEDDING_BATCH_SIZE,
    load_checkpoint: EmbeddingCheckpointLoader | None = None,
    save_checkpoint: EmbeddingCheckpointSaver | None = None,
) -> None:
    """按稳定顺序批量生成或复用向量，并拒绝任何错位或维度不一致结果。"""
    if batch_size < 1:
        raise ValueError("Embedding 批次大小必须大于 0")
    for batch_index, start in enumerate(range(0, len(chunks), batch_size)):
        batch = chunks[start : start + batch_size]
        content_digest = _embedding_batch_digest(batch)
        embeddings = (
            await load_checkpoint(batch_index, start, len(batch), content_digest)
            if load_checkpoint
            else None
        )
        reused_checkpoint = embeddings is not None
        if embeddings is None:
            embeddings = await provider.embed([chunk["content"] for chunk in batch])
        # 结果数量不一致会错配知识正文和向量，必须在写入索引前失败而不是静默截断。
        if len(embeddings) != len(batch):
            raise ValueError(
                f"Embedding Provider 返回数量异常：期望 {len(batch)}，实际 {len(embeddings)}"
            )
        if any(len(embedding) != settings.embedding_dimension for embedding in embeddings):
            raise ValueError("Embedding 检查点或 Provider 返回了不兼容的向量维度")
        if not reused_checkpoint and save_checkpoint:
            await save_checkpoint(batch_index, start, len(batch), content_digest, embeddings)
        for chunk, embedding in zip(batch, embeddings, strict=True):
            chunk["embedding"] = embedding


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
