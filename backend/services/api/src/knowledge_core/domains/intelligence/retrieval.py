from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.domains.intelligence.repository import IntelligenceRepository
from knowledge_core.infrastructure.models import (
    Document,
    DocumentChunk,
    DocumentRevision,
    KnowledgeCollection,
)
from knowledge_core.infrastructure.providers import get_embedding_provider
from knowledge_core.shared.context import ApplicationContext
from knowledge_core.shared.errors import NotFoundError, ProviderUnavailableError


@dataclass(slots=True)
class RetrievalHit:
    chunk_id: UUID
    document_id: UUID
    revision_id: UUID
    title: str
    content: str
    score: float
    vector_score: float
    lexical_score: float
    citation: dict
    published_at: datetime | None


class RetrievalService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = IntelligenceRepository(session)

    async def search_by_code(
        self,
        context: ApplicationContext,
        profile_code: str,
        query: str,
        *,
        top_k: int | None = None,
    ) -> tuple[list[RetrievalHit], object]:
        profile = await self.repository.get_retrieval_profile_by_code(context, profile_code)
        if not profile:
            raise NotFoundError("检索策略不存在或未启用")
        hits = await self.search(context, profile, query, top_k=top_k)
        return hits, profile

    async def search(
        self,
        context: ApplicationContext,
        profile,
        query: str,
        *,
        top_k: int | None = None,
    ) -> list[RetrievalHit]:
        limit = min(top_k or profile.top_k, 30)
        collection_ids = [UUID(value) for value in profile.collection_ids]
        if not collection_ids:
            return []

        base_filters = (
            DocumentChunk.application_id == context.application_id,
            DocumentChunk.environment_id == context.environment_id,
            DocumentRevision.application_id == context.application_id,
            DocumentRevision.environment_id == context.environment_id,
            Document.application_id == context.application_id,
            Document.environment_id == context.environment_id,
            Document.collection_id.in_(collection_ids),
            DocumentRevision.status == "ready",
            Document.status == "ready",
            Document.current_version == DocumentRevision.version,
            KnowledgeCollection.status == "active",
        )
        joined = (
            select(DocumentChunk, DocumentRevision, Document)
            .join(
                DocumentRevision,
                (DocumentRevision.id == DocumentChunk.revision_id)
                & (DocumentRevision.application_id == DocumentChunk.application_id)
                & (DocumentRevision.environment_id == DocumentChunk.environment_id),
            )
            .join(
                Document,
                (Document.id == DocumentRevision.document_id)
                & (Document.application_id == DocumentRevision.application_id)
                & (Document.environment_id == DocumentRevision.environment_id),
            )
            .join(
                KnowledgeCollection,
                (KnowledgeCollection.id == Document.collection_id)
                & (KnowledgeCollection.application_id == Document.application_id)
                & (KnowledgeCollection.environment_id == Document.environment_id),
            )
            .where(*base_filters)
        )
        for key, value in profile.metadata_filters.items():
            joined = joined.where(DocumentRevision.metadata_[key].as_string() == str(value))

        escaped_query = query.replace("%", r"\%").replace("_", r"\_")
        lexical_expression = func.greatest(
            func.similarity(DocumentChunk.content, query),
            case((DocumentChunk.content.ilike(f"%{escaped_query}%", escape="\\"), 1.0), else_=0.0),
        )
        lexical_rows = (
            await self.session.execute(
                joined.add_columns(lexical_expression.label("lexical_score"))
                .where(lexical_expression > 0.02)
                .order_by(lexical_expression.desc())
                .limit(limit * 3)
            )
        ).all()

        vector_rows = []
        try:
            query_vector = (await get_embedding_provider().embed([query]))[0]
            vector_expression = 1 - DocumentChunk.embedding.cosine_distance(query_vector)
            vector_rows = list(
                (
                    await self.session.execute(
                        joined.add_columns(vector_expression.label("vector_score"))
                        .where(DocumentChunk.embedding.is_not(None))
                        .order_by(vector_expression.desc())
                        .limit(limit * 3)
                    )
                ).all()
            )
        except ProviderUnavailableError:
            vector_rows = []

        merged: dict[UUID, dict] = {}
        for chunk, revision, document, score in lexical_rows:
            merged[chunk.id] = {
                "chunk": chunk,
                "revision": revision,
                "document": document,
                "lexical": max(0.0, min(float(score), 1.0)),
                "vector": 0.0,
            }
        for chunk, revision, document, score in vector_rows:
            item = merged.setdefault(
                chunk.id,
                {
                    "chunk": chunk,
                    "revision": revision,
                    "document": document,
                    "lexical": 0.0,
                    "vector": 0.0,
                },
            )
            item["vector"] = max(0.0, min(float(score), 1.0))

        hits: list[RetrievalHit] = []
        for item in merged.values():
            chunk = item["chunk"]
            revision = item["revision"]
            document = item["document"]
            score = (
                item["vector"] * profile.vector_weight + item["lexical"] * profile.lexical_weight
            )
            hits.append(
                RetrievalHit(
                    chunk_id=chunk.id,
                    document_id=document.id,
                    revision_id=revision.id,
                    title=document.title,
                    content=chunk.content,
                    score=round(score, 6),
                    vector_score=round(item["vector"], 6),
                    lexical_score=round(item["lexical"], 6),
                    citation={
                        "documentId": str(document.id),
                        "revision": revision.version,
                        "page": chunk.page_number,
                        "section": chunk.section,
                    },
                    published_at=revision.published_at,
                )
            )
        hits = [item for item in hits if item.score >= profile.minimum_score]
        hits.sort(key=lambda item: item.score, reverse=True)

        # 同一文档最多保留三个片段，避免单一来源淹没回答上下文。
        deduplicated: list[RetrievalHit] = []
        per_document: dict[UUID, int] = {}
        for hit in hits:
            count = per_document.get(hit.document_id, 0)
            if count >= 3:
                continue
            per_document[hit.document_id] = count + 1
            deduplicated.append(hit)
            if len(deduplicated) >= limit:
                break
        return deduplicated
