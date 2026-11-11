"""检索服务模块。

提供基于检索策略的知识检索能力，支持：
- 向量检索（Embedding 相似度）
- 词法检索（pg_trgm 相似度和关键词匹配）
- 混合检索（向量和词法加权融合）
- RAG 增强（Query Rewriting、Reranking）

检索策略配置决定检索行为，包括：
- 知识范围（collection_ids）
- 向量/词法权重（vector_weight、lexical_weight）
- 最小分数阈值（minimum_score）
- RAG 增强开关（rag_config）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.domains.intelligence.rag_enhancements import (
    RAGEnhancementConfig,
    RAGMetrics,
    RerankCandidate,
    rerank_results,
    rewrite_query,
)
from knowledge_core.domains.intelligence.repository import IntelligenceRepository
from knowledge_core.infrastructure.models import (
    Document,
    DocumentChunk,
    DocumentRevision,
    KnowledgeCollection,
)
from knowledge_core.infrastructure.providers import (
    embedding_space_fingerprint,
    get_embedding_provider_async,
)
from knowledge_core.shared.context import ApplicationContext
from knowledge_core.shared.errors import NotFoundError, ProviderUnavailableError

logger = logging.getLogger(__name__)


class _ChatCompleteAdapter:
    """把聊天 Provider 适配为查询改写使用的 complete 接口。"""

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    async def complete(self, prompt: str, max_tokens: int) -> str:
        """生成改写文本。聊天接口不接收 max_tokens，长度由改写函数自行限制。"""
        _ = max_tokens
        result = await self._provider.generate([{"role": "user", "content": prompt}])
        return str(result.content)


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
    """检索服务。

    根据检索策略执行知识检索，支持 RAG 增强功能。
    """

    def __init__(self, session: AsyncSession) -> None:
        """初始化检索服务。

        Args:
            session: 数据库会话
        """
        self.session = session
        self.repository = IntelligenceRepository(session)

    async def search_by_code(
        self,
        context: ApplicationContext,
        profile_code: str,
        query: str,
        *,
        top_k: int | None = None,
        collect_metrics: bool = False,
    ) -> tuple[list[RetrievalHit], object, RAGMetrics | None]:
        """按策略编码执行检索。

        运行面只接受最近一次发布事件指向的快照，草稿不可被 API Key 直接调用。

        Args:
            context: 应用上下文
            profile_code: 检索策略编码
            query: 用户查询
            top_k: 返回结果数量限制
            collect_metrics: 是否收集 RAG 指标

        Returns:
            (检索结果列表, 策略对象, RAG 指标或 None)

        Raises:
            NotFoundError: 策略不存在或未发布
        """
        profile = await self.repository.get_published_retrieval_profile_by_code(
            context, profile_code
        )
        if not profile:
            raise NotFoundError("检索策略不存在、未启用或尚未发布")
        hits, metrics = await self.search(
            context, profile, query, top_k=top_k, collect_metrics=collect_metrics
        )
        return hits, profile, metrics

    async def search(
        self,
        context: ApplicationContext,
        profile,
        query: str,
        *,
        top_k: int | None = None,
        collect_metrics: bool = False,
    ) -> tuple[list[RetrievalHit], RAGMetrics | None]:
        """执行检索。

        支持 RAG 增强：
        - Query Rewriting：将用户查询改写为更适合检索的形式
        - Reranking：使用更精确的模型对检索结果重排序

        Args:
            context: 应用上下文
            profile: 检索策略对象
            query: 用户查询
            top_k: 返回结果数量限制
            collect_metrics: 是否收集 RAG 指标

        Returns:
            (检索结果列表, RAG 指标或 None)
        """
        metrics = RAGMetrics() if collect_metrics else None
        start_time = datetime.now(UTC)

        limit = min(top_k or profile.top_k, 30)
        collection_ids = [UUID(value) for value in profile.collection_ids]
        if not collection_ids:
            return [], metrics

        # 解析 RAG 增强配置
        rag_config = self._get_rag_config(profile)
        effective_query = query

        # 查询改写仅在发布快照显式打开时执行；Provider 不可用则保持原查询。
        if rag_config and rag_config.query_rewriting_enabled:
            rewrite_start = datetime.now(UTC)
            llm_provider = await self._rewrite_llm(context)
            effective_query = await rewrite_query(query, rag_config, llm_provider=llm_provider)
            if metrics:
                metrics.query_rewriting_enabled = True
                metrics.query_rewriting_latency_ms = (
                    datetime.now(UTC) - rewrite_start
                ).total_seconds() * 1000

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
            embedding_provider = await get_embedding_provider_async(
                self.session, context.application_id, context.environment_id
            )
            embedding_fingerprint = embedding_space_fingerprint(embedding_provider)
            query_vector = (await embedding_provider.embed([query]))[0]
            vector_expression = 1 - DocumentChunk.embedding.cosine_distance(query_vector)
            vector_rows = list(
                (
                    await self.session.execute(
                        joined.where(
                            DocumentRevision.embedding_fingerprint == embedding_fingerprint
                        )
                        .add_columns(vector_expression.label("vector_score"))
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

        # Reranking（如果启用且有足够候选）
        if rag_config and rag_config.reranking_enabled and len(hits) > 1:
            rerank_start = datetime.now(UTC)
            candidates = [
                RerankCandidate(
                    index=i,
                    content=hit.content,
                    original_score=hit.score,
                )
                for i, hit in enumerate(hits)
            ]

            # 执行重排序（当前无真实 Reranker Provider，使用原始分数）
            ranked = await rerank_results(effective_query, candidates, rag_config, reranker=None)

            # 按重排序结果重新排列
            reranked_hits: list[RetrievalHit] = []
            for idx, _new_score in ranked:
                hit = hits[idx]
                reranked_hits.append(hit)

            hits = reranked_hits

            if metrics:
                metrics.reranking_enabled = True
                metrics.reranking_latency_ms = (
                    datetime.now(UTC) - rerank_start
                ).total_seconds() * 1000

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

        # 记录最终指标
        if metrics:
            metrics.total_retrieved = len(deduplicated)
            metrics.retrieval_latency_ms = (datetime.now(UTC) - start_time).total_seconds() * 1000
            metrics.total_latency_ms = metrics.retrieval_latency_ms

        return deduplicated, metrics

    def _get_rag_config(self, profile) -> RAGEnhancementConfig | None:
        """从已发布检索快照解析 RAG 开关。

        旧快照没有 rag_config，或两个开关都关闭时返回 None，检索主链路保持原行为。

        Args:
            profile: 已发布检索策略或带 rag_config 的兼容对象

        Returns:
            至少启用一项检索增强时返回配置，否则返回 None
        """
        rag_config_data = getattr(profile, "rag_config", None)
        if rag_config_data is None:
            return None
        if isinstance(rag_config_data, RAGEnhancementConfig):
            config = rag_config_data
        elif isinstance(rag_config_data, dict):
            config = RAGEnhancementConfig.from_dict(rag_config_data)
        else:
            return None
        if not config.query_rewriting_enabled and not config.reranking_enabled:
            return None
        return config

    async def _rewrite_llm(self, context: ApplicationContext):
        """解析查询改写使用的聊天 Provider。不可用时返回 None，不中断检索。"""
        try:
            from knowledge_core.infrastructure.providers import get_chat_provider_async

            provider = await get_chat_provider_async(
                self.session, context.application_id, context.environment_id
            )
        except Exception:
            logger.warning("查询改写 Provider 解析失败，已回退为原查询", exc_info=True)
            return None
        if not getattr(provider, "available", False):
            return None
        return _ChatCompleteAdapter(provider)
