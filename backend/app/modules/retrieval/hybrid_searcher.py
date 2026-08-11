"""混合检索器：全文检索 + 向量检索 + RRF 合并。

对应 Task 10.2：实现 ``HybridSearcher``，将 PostgreSQL 全文检索与 pgvector
向量检索的结果用 RRF（Reciprocal Rank Fusion）算法合并，返回 Top K 片段。

为什么需要混合检索？
--------------------
单一检索方式各有局限：
1. **全文检索（TSVECTOR + GIN）**：擅长关键词精确匹配，但对同义词、语义相近
   但用词不同的查询无能为力（如查询"基金涨跌"无法召回只含"净值波动"的片段）。
2. **向量检索（pgvector + HNSW）**：擅长语义相似匹配，但对罕见专有名词、
   编号、缩写等无义可循的词反而不如全文检索精准。

混合检索取两者之长：全文检索保证关键词命中，向量检索保证语义召回，
RRF 合并避免单路结果霸占榜单，最终 Top K 既精准又具备语义覆盖。

RRF（Reciprocal Rank Fusion）原理
---------------------------------
RRF 是一种无需归一化的排名合并算法，公式：
    score(d) = sum( 1 / (k + rank_i(d)) )
- ``d``：某个文档/片段
- ``rank_i(d)``：文档 ``d`` 在第 ``i`` 个检索结果列表中的排名（从 1 开始）
- ``k``：平滑常数，标准经验值 60

为什么选 k=60？
    k 越大，排名靠后结果的权重衰减越慢（更"民主"）；k 越小，头部结果权重越大
    （更"精英"）。k=60 是论文与工业界（Elastic、LinkedIn）广泛采用的经验值，
    在多数场景下平衡了头部权重与长尾覆盖。

为什么 RRF 优于分数加权合并？
    全文检索的 ``ts_rank_cd`` 分数范围不固定（与词频、文档长度相关），
    向量检索的余弦相似度固定在 [0,1]。两者量纲不同，直接加权需要复杂的
    归一化（min-max / z-score），且归一化参数对数据分布敏感。RRF 只用排名
    信息，天然消除量纲差异，参数极少（仅 k），鲁棒性强。

为什么 project_id 必须前置过滤？
-------------------------------
1. **安全隔离**：项目间数据强隔离是核心设计，AI 基金项目的查询绝不能召回
   AI 简历项目的片段。``project_id`` 在 WHERE 子句前置过滤，从数据库层杜绝
   跨项目召回。
2. **性能优化**：``document_chunks`` 上有部分索引
   ``idx_chunks_project_kb (project_id, knowledge_base_id) WHERE enabled=true``，
   project_id 作为索引前导列，前置过滤可命中索引，避免全表扫描。
3. **复合外键保障**：``document_chunks`` 通过复合外键
   ``fk_chunks_project_document`` 保证 chunk 与文档同项目，即使应用层漏掉
   project_id 过滤，数据库层也不会产生跨项目引用（但检索 SQL 仍必须显式过滤）。
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from app.db.models.knowledge import DocumentChunk
from app.providers.embeddings import get_embedding_provider

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.project_context import ProjectContext


# 候选集规模：每路检索先取 Top 30，RRF 合并后再截断到 Top K。
# 取 30 而非更大的原因：
#   - Top K 通常 ≤ 5，30 的候选池已足够覆盖两路结果的重叠与差异部分
#   - 候选越多，HNSW 索引扫描成本越高（pgvector 的 LIMIT 可提前终止扫描，
#     但过大仍会增加排序负担）
#   - 30 是工业界 RRF 候选集的常见规模（Elastic recency search 默认 30~50）
CANDIDATE_TOP_N = 30

# RRF 平滑常数 k：标准经验值 60，详见模块 docstring
RRF_K = 60


class HybridSearcher:
    """混合检索器：全文检索 + 向量检索 + RRF 合并。

    检索流程
    --------
    1. 对查询文本生成一次 Embedding（复用 EmbeddingProvider 单例）
    2. PostgreSQL 全文检索取候选 Top 30（GIN 索引加速）
    3. pgvector 向量检索取候选 Top 30（HNSW 索引加速）
    4. 使用 RRF 合并两个排名（k=60）
    5. 按文档去重：同一文档仅保留分数最高的 1 个片段，避免单文档霸占结果
    6. 返回 Top K（默认 5）

    性能优化点
    ----------
    - **并行检索**：全文与向量检索用 ``asyncio.gather`` 并行执行，
      总耗时 ≈ max(全文耗时, 向量耗时) 而非两者之和。
    - **索引复用**：HNSW 向量索引 + GIN 全文索引 + 部分索引
      ``idx_chunks_project_kb WHERE enabled=true``，三者配合保证检索在
      百万级 chunk 下仍能 P95 ≤ 800ms。
    - **候选池控制**：每路仅取 Top 30，避免 RRF 合并阶段处理过多数据。
    - **Embedding 单例**：``get_embedding_provider`` 用 ``lru_cache`` 缓存，
      复用 httpx 连接池，避免每次检索重建客户端。

    Attributes:
        session: 异步数据库会话，由接口层通过 ``Depends(get_db)`` 注入。
    """

    def __init__(self, session: "AsyncSession") -> None:
        """初始化混合检索器。

        Args:
            session: 异步数据库会话。检索过程不修改数据，无需显式 commit。
        """
        self.session = session

    async def search(
        self,
        ctx: "ProjectContext",
        query: str,
        knowledge_base_ids: list[str],
        top_k: int = 5,
        enabled_only: bool = True,
    ) -> list[dict[str, Any]]:
        """执行混合检索，返回 Top K 片段。

        Args:
            ctx: 项目上下文，提供 ``project_id`` 用于前置过滤（安全隔离核心）。
            query: 查询文本，将同时用于全文检索与生成 Embedding 向量。
            knowledge_base_ids: 参与检索的知识库 ID 列表（必须属于当前项目）。
                空列表时直接返回空结果，避免无意义 SQL 调用。
            top_k: 最终返回的片段数，默认 5。候选池固定 30，top_k 仅作用于
                RRF 合并后的截断。
            enabled_only: 是否仅检索 enabled=true 的片段。默认 True，
                禁用片段（如文档被下架）不参与召回。

        Returns:
            片段字典列表，按 ``merged_score`` 降序，长度 ≤ ``top_k``。
            每个字典包含：
            - ``chunk_id``：片段 ID
            - ``document_id``：所属文档 ID
            - ``content``：片段原文（用于 RAG 上下文拼接）
            - ``page_number``：页码（可空）
            - ``metadata``：片段元数据（JSONB）
            - ``merged_score``：RRF 合并分数
            - ``source``：来源标记，"vector" / "fulltext" / "both"
        """
        # 边界处理：空查询或空知识库列表直接返回，避免无效 SQL
        if not query.strip() or not knowledge_base_ids:
            return []

        # ------------------------------------------------------------------
        # 步骤 1：生成查询向量
        # ------------------------------------------------------------------
        # 复用 lru_cache 单例，避免每次检索重建 httpx 客户端
        embedding_provider = get_embedding_provider()
        # embed_texts 接受文本列表，返回等长向量列表；这里只查一条
        query_embeddings = await embedding_provider.embed_texts([query])
        query_embedding = query_embeddings[0]

        # ------------------------------------------------------------------
        # 步骤 2 & 3：并行执行全文与向量检索
        # ------------------------------------------------------------------
        # asyncio.gather 并行调度两个协程，总耗时 ≈ max(两路耗时)
        # 注意：两个协程共享同一 session，SQLAlchemy 异步 session 在并发执行
        # 多个查询时是安全的（每个 execute 独立的事务上下文），但不要在并发
        # 期间对同一 session 做写操作
        text_results, vector_results = await asyncio.gather(
            self._fulltext_search(
                ctx, query, knowledge_base_ids,
                top_n=CANDIDATE_TOP_N, enabled_only=enabled_only,
            ),
            self._vector_search(
                ctx, query_embedding, knowledge_base_ids,
                top_n=CANDIDATE_TOP_N, enabled_only=enabled_only,
            ),
        )

        # ------------------------------------------------------------------
        # 步骤 4：RRF 合并
        # ------------------------------------------------------------------
        merged = self._rrf_merge(text_results, vector_results, k=RRF_K)

        # ------------------------------------------------------------------
        # 步骤 5 & 6：过滤与去重，取 Top K
        # ------------------------------------------------------------------
        filtered = self._filter_and_dedup(merged, enabled_only)

        # 截断到 top_k（候选池 30 经 RRF 合并 + 去重后通常已 < top_k，
        # 但兜底截断保证返回长度契约）
        return filtered[:top_k]

    async def _vector_search(
        self,
        ctx: "ProjectContext",
        query_embedding: list[float],
        kb_ids: list[str],
        top_n: int = 30,
        enabled_only: bool = True,
    ) -> list[dict[str, Any]]:
        """向量检索：使用 pgvector 余弦相似度召回 Top N。

        SQL 逻辑
        --------
        ::

            SELECT id, document_id, content, page_number, metadata,
                   1 - (embedding <=> :query_embedding) AS vector_score
            FROM document_chunks
            WHERE project_id = :project_id
              AND knowledge_base_id IN :kb_ids
              AND enabled = true              -- enabled_only=True 时
            ORDER BY embedding <=> :query_embedding
            LIMIT 30

        关键点
        ------
        - ``<=>`` 是 pgvector 余弦距离操作符（范围 [0,2]，0=完全相似）。
          ``cosine_distance`` 是 SQLAlchemy pgvector 适配器的封装。
        - ``vector_score = 1 - distance``，将距离转换为相似度（范围 [-1,1]，
          越大越相似），便于业务展示。
        - ``ORDER BY embedding <=> :query`` 直接按距离升序（距离越小越相似），
          pgvector 的 HNSW 索引（``vector_cosine_ops``）会加速此排序。
        - ``LIMIT`` 配合 HNSW 的 ``ef_search`` 参数可提前终止扫描，避免全表遍历。
        - **project_id 前置过滤**：作为 WHERE 第一个条件，命中部分索引
          ``idx_chunks_project_kb`` 的前导列，杜绝跨项目召回。

        Args:
            ctx: 项目上下文。
            query_embedding: 查询向量，维度 = ``settings.embedding_dimension``。
            kb_ids: 知识库 ID 列表。
            top_n: 候选数量上限。
            enabled_only: 是否仅检索 enabled=true 的片段。

        Returns:
            片段字典列表，按向量相似度降序，每项含 ``score`` 与 ``source="vector"``。
        """
        # 构造条件列表：project_id 前置（安全隔离 + 索引命中）
        conditions = [
            # project_id 必须前置过滤：杜绝跨项目召回，命中部分索引前导列
            DocumentChunk.project_id == ctx.project_id,
            # 知识库范围过滤：IN 列表，复用 project_id 索引的第二列
            DocumentChunk.knowledge_base_id.in_(kb_ids),
            # embedding 非空：避免未向量化的片段参与检索（如入库失败残留）
            DocumentChunk.embedding.isnot(None),
        ]
        if enabled_only:
            # enabled=true 过滤：复用部分索引 idx_chunks_project_kb WHERE enabled=true
            # noqa: E712 比较布尔列必须用 == True，不能用 is True
            conditions.append(DocumentChunk.enabled == True)  # noqa: E712

        # cosine_distance 生成 <=> 操作符；1 - distance 转为相似度分数
        cosine_distance = DocumentChunk.embedding.cosine_distance(query_embedding)
        vector_score = (1 - cosine_distance).label("vector_score")

        stmt = (
            select(
                DocumentChunk.id.label("chunk_id"),
                DocumentChunk.document_id,
                DocumentChunk.content,
                DocumentChunk.page_number,
                # metadata_ 是 Python 属性名，列名为 metadata；用 label 显式命名便于取值
                DocumentChunk.metadata_.label("chunk_metadata"),
                vector_score,
            )
            .where(*conditions)
            # ORDER BY <=> 距离升序：HNSW 索引加速，LIMIT 可提前终止扫描
            .order_by(cosine_distance)
            .limit(top_n)
        )
        result = await self.session.execute(stmt)

        # 构造统一结构的字典列表，source 标记来源便于调试
        return [
            {
                "chunk_id": row.chunk_id,
                "document_id": row.document_id,
                "content": row.content,
                "page_number": row.page_number,
                "metadata": row.chunk_metadata,
                "score": float(row.vector_score) if row.vector_score is not None else 0.0,
                "source": "vector",
            }
            for row in result
        ]

    async def _fulltext_search(
        self,
        ctx: "ProjectContext",
        query: str,
        kb_ids: list[str],
        top_n: int = 30,
        enabled_only: bool = True,
    ) -> list[dict[str, Any]]:
        """全文检索：使用 PostgreSQL ts_rank_cd 召回 Top N。

        SQL 逻辑
        --------
        ::

            SELECT id, document_id, content, page_number, metadata,
                   ts_rank_cd(content_tsv, websearch_to_tsquery('simple', :query)) AS text_score
            FROM document_chunks
            WHERE project_id = :project_id
              AND knowledge_base_id IN :kb_ids
              AND content_tsv @@ websearch_to_tsquery('simple', :query)
              AND enabled = true              -- enabled_only=True 时
            ORDER BY text_score DESC
            LIMIT 30

        关键点
        ------
        - ``websearch_to_tsquery('simple', :query)``：将查询文本解析为 tsquery。
          使用 ``websearch_to_tsquery`` 而非 ``plainto_tsquery``，支持引号精确匹配、
          OR、- 排除等搜索语法，容错性更好（非法字符不会报错）。
        - ``'simple'`` 配置：不做词干提取与停用词过滤，按空格分词。
          **中文全文检索局限**：PostgreSQL 内置分词器对中文支持不足
          （'simple' 按空格切分，中文连续无空格会被当作单个词）。
          MVP 阶段先用 ``simple`` + 向量召回兜底，后续可接入 zhparser/jieba
          等中文分词扩展提升中文全文召回率。
        - ``content_tsv @@ tsquery``：GIN 索引加速的匹配操作符，``idx_chunks_content_tsv``
          是 GIN 索引。
        - ``ts_rank_cd``：基于词频与文档长度的排名函数（cd = cover density，
          考虑匹配词在文档中的覆盖密度），比 ``ts_rank`` 更精细。
        - **project_id 前置过滤**：与向量检索一致，安全隔离 + 索引命中。

        Args:
            ctx: 项目上下文。
            query: 查询文本（原样传入，由 websearch_to_tsquery 解析）。
            kb_ids: 知识库 ID 列表。
            top_n: 候选数量上限。
            enabled_only: 是否仅检索 enabled=true 的片段。

        Returns:
            片段字典列表，按文本相关性降序，每项含 ``score`` 与 ``source="fulltext"``。
        """
        # 中文全文检索效果不足时由向量召回兜底（见模块 docstring 说明）
        # websearch_to_tsquery 容错性好，非法字符不会报错
        tsquery = func.websearch_to_tsquery("simple", query)

        # 构造条件列表：project_id 前置
        conditions = [
            # project_id 必须前置过滤：安全隔离 + 索引命中
            DocumentChunk.project_id == ctx.project_id,
            DocumentChunk.knowledge_base_id.in_(kb_ids),
            # @@ 操作符：GIN 索引加速，仅保留命中关键词的片段
            DocumentChunk.content_tsv.match(tsquery),
        ]
        if enabled_only:
            # enabled=true 过滤：复用部分索引
            conditions.append(DocumentChunk.enabled == True)  # noqa: E712

        # ts_rank_cd：基于覆盖密度的排名，比 ts_rank 更精细
        text_score = func.ts_rank_cd(
            DocumentChunk.content_tsv,
            tsquery,
        ).label("text_score")

        stmt = (
            select(
                DocumentChunk.id.label("chunk_id"),
                DocumentChunk.document_id,
                DocumentChunk.content,
                DocumentChunk.page_number,
                DocumentChunk.metadata_.label("chunk_metadata"),
                text_score,
            )
            .where(*conditions)
            # 按文本相关性降序：text_score 越大越相关
            .order_by(text_score.desc())
            .limit(top_n)
        )
        result = await self.session.execute(stmt)

        return [
            {
                "chunk_id": row.chunk_id,
                "document_id": row.document_id,
                "content": row.content,
                "page_number": row.page_number,
                "metadata": row.chunk_metadata,
                "score": float(row.text_score) if row.text_score is not None else 0.0,
                "source": "fulltext",
            }
            for row in result
        ]

    def _rrf_merge(
        self,
        text_results: list[dict[str, Any]],
        vector_results: list[dict[str, Any]],
        k: int = RRF_K,
    ) -> list[dict[str, Any]]:
        """RRF 合并：Reciprocal Rank Fusion。

        算法原理
        --------
        对每个片段 ``d``，其在合并后的分数为：
            score(d) = sum_i ( 1 / (k + rank_i(d)) )
        - ``rank_i(d)``：``d`` 在第 ``i`` 个检索结果列表中的排名（从 1 开始）
        - ``k``：平滑常数（默认 60），平衡头部与长尾权重

        合并规则
        --------
        - 同一 chunk 在两个列表中都有排名时，分数相加（两路都命中的片段更可信）
        - 仅在一个列表中出现的片段，分数 = 1/(k + rank)
        - 合并后按总分降序排序

        为什么用排名而非分数？
        ---------------------
        全文检索的 ``ts_rank_cd`` 分数范围不固定，向量检索的余弦相似度在 [0,1]，
        两者量纲不同，直接加权需复杂归一化。RRF 只用排名信息，天然消除量纲差异，
        鲁棒性强，参数极少（仅 k）。

        Args:
            text_results: 全文检索结果列表，按相关性降序。
            vector_results: 向量检索结果列表，按相似度降序。
            k: 平滑常数，默认 60（标准经验值）。

        Returns:
            合并后的片段字典列表，按 ``merged_score`` 降序。每项新增：
            - ``merged_score``：RRF 合并分数
            - ``source``：来源标记，"both"（两路都命中）/ "fulltext" / "vector"
        """
        # scores：chunk_id -> 累计 RRF 分数
        scores: dict[str, float] = defaultdict(float)
        # items：chunk_id -> 片段字典（保留首次出现的字典，后续仅更新分数）
        items: dict[str, dict[str, Any]] = {}

        # 遍历全文检索结果，按排名累加分数（rank 从 1 开始）
        for rank, item in enumerate(text_results, start=1):
            cid = item["chunk_id"]
            # 1/(k+rank)：rank 越靠前分数越大；k=60 时 rank=1 分数≈0.0164
            scores[cid] += 1.0 / (k + rank)
            items[cid] = item

        # 遍历向量检索结果，按排名累加分数
        vector_seen: set[str] = set()
        for rank, item in enumerate(vector_results, start=1):
            cid = item["chunk_id"]
            scores[cid] += 1.0 / (k + rank)
            vector_seen.add(cid)
            # 若全文检索已有该 chunk，保留全文检索的字典（含其 content 等字段）
            if cid not in items:
                items[cid] = item

        # 构造合并结果：标记 source 并附加 merged_score
        merged: list[dict[str, Any]] = []
        # text_results 的 chunk_id 集合，用于判断 source
        text_seen = {item["chunk_id"] for item in text_results}
        for cid, score in scores.items():
            item = items[cid]
            # 复制字典避免污染输入（_filter_and_dedup 会基于此继续处理）
            merged_item = dict(item)
            merged_item["merged_score"] = score
            # 标记来源：两路都命中标记为 both，便于调试与权重分析
            in_text = cid in text_seen
            in_vector = cid in vector_seen
            if in_text and in_vector:
                merged_item["source"] = "both"
            elif in_text:
                merged_item["source"] = "fulltext"
            else:
                merged_item["source"] = "vector"
            merged.append(merged_item)

        # 按合并分数降序：两路都命中的片段分数更高，自然排前面
        merged.sort(key=lambda x: x["merged_score"], reverse=True)
        return merged

    def _filter_and_dedup(
        self,
        merged: list[dict[str, Any]],
        enabled_only: bool,
    ) -> list[dict[str, Any]]:
        """过滤与去重。

        去重策略
        --------
        按文档分组，每个文档仅保留分数最高的 1 个片段。

        为什么按文档去重？
        -----------------
        RAG 场景下，若 Top 5 中有 3 个片段来自同一文档，上下文多样性不足，
        且单文档可能霸占结果（如某文档被切成多个相似片段）。
        每文档仅保留 1 个最高分片段，保证结果来源多样，覆盖更多文档。

        扩展点
        ------
        如需每文档多片段（如法律条款检索需同文档上下文），可在后续版本扩展为
        ``max_chunks_per_document`` 参数，默认 1，可调大。

        Args:
            merged: RRF 合并后的片段列表，已按 ``merged_score`` 降序。
            enabled_only: 是否仅保留 enabled 片段。SQL 层已过滤，此处为防御性
                二次校验（避免未来 SQL 改动遗漏过滤时仍能保证安全）。

        Returns:
            去重后的片段列表，保持降序。
        """
        # seen_docs：已纳入结果的文档 ID 集合
        seen_docs: set[str] = set()
        result: list[dict[str, Any]] = []

        for item in merged:
            # 防御性 enabled 校验：SQL 层已过滤，此处兜底
            # （merged 字典无 enabled 字段时跳过校验，保持向后兼容）
            if enabled_only and item.get("enabled") is False:
                continue

            doc_id = item["document_id"]
            # 同文档仅保留分数最高的片段（merged 已降序，首个出现的即最高分）
            if doc_id in seen_docs:
                continue
            seen_docs.add(doc_id)
            result.append(item)

        return result
