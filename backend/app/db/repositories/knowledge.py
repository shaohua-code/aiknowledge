"""知识库 Repository：KnowledgeBase / Document / DocumentChunk。

对应 SubTask 6.1：知识库主表、文档表、文档分块表的数据库访问层。

设计要点（重点：复合外键与 project_id 双重过滤）
------------------------------------------------
1. 父表 ``knowledge_bases`` 持有复合唯一约束 ``uq_knowledge_bases_project_id_id``，
   保证 ``id`` 在项目内唯一；子表 ``documents`` 通过复合外键引用，
   数据库层强制文档与知识库同属一个项目。
2. Repository 在所有查询中都同时带 ``project_id`` 与业务外键（如 ``knowledge_base_id``）
   双重过滤，即使在 ORM 层也算"双保险"，杜绝跨项目数据访问。
3. ``DocumentChunkRepository`` 的向量检索与全文检索方法在 Task 10 实现，
   本任务仅留占位方法签名与 TODO 注释，避免后续修改签名。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from app.db.models.knowledge import Document, DocumentChunk, KnowledgeBase
from app.db.repositories.base import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.project_context import ProjectContext


# ---------------------------------------------------------------------------
# KnowledgeBaseRepository
# ---------------------------------------------------------------------------
class KnowledgeBaseRepository(BaseRepository):
    """知识库 Repository：操作 ``knowledge_bases`` 表。

    强制带 project_id 过滤，外部 API 通过 ``code`` 查询时同样带 project_id，
    保证不同项目可有相同 code 的知识库（虽然 code 在项目内唯一）。
    """

    model = KnowledgeBase

    async def create(
        self,
        ctx: "ProjectContext",
        name: str,
        code: str,
        description: str | None = None,
        embedding_model: str | None = None,
        embedding_dimension: int | None = None,
    ) -> KnowledgeBase:
        """创建知识库。

        Args:
            ctx: 项目上下文，提供 ``project_id``。
            name: 知识库名称。
            code: 知识库编码，项目内唯一（CIText 大小写不敏感）。
            description: 知识库描述，可空。
            embedding_model: Embedding 模型名称，创建后不可改。
            embedding_dimension: 向量维度，必须与 embedding_model 匹配。

        Returns:
            创建后的 KnowledgeBase 实例。
        """
        # project_id 由 ctx 注入，强制项目归属
        kb = KnowledgeBase(
            project_id=ctx.project_id,
            name=name,
            code=code,
            description=description,
            embedding_model=embedding_model,
            embedding_dimension=embedding_dimension,
            status="active",
        )
        self.session.add(kb)
        await self.session.flush()
        return kb

    async def get_by_id(
        self, ctx: "ProjectContext", id: str
    ) -> KnowledgeBase | None:
        """按 ID 查询知识库（强制 project_id 过滤）。

        Args:
            ctx: 项目上下文。
            id: 知识库主键。

        Returns:
            KnowledgeBase 实例；不存在或属于其他项目返回 None。
        """
        # 双重过滤：id + project_id
        stmt = select(KnowledgeBase).where(
            KnowledgeBase.id == id,
            KnowledgeBase.project_id == ctx.project_id,
        )
        return await self.session.scalar(stmt)

    async def get_by_code(
        self, ctx: "ProjectContext", code: str
    ) -> KnowledgeBase | None:
        """按 code 查询知识库（外部 API 用 code 查询）。

        为什么必须带 project_id？
            ``code`` 仅在项目内唯一（复合唯一约束 ``uq_knowledge_bases_project_code``），
            不同项目可有相同 code 的知识库。必须同时按 project_id 过滤，
            否则可能返回其他项目的知识库。

        Args:
            ctx: 项目上下文。
            code: 知识库编码（CIText 大小写不敏感）。

        Returns:
            KnowledgeBase 实例；不存在或属于其他项目返回 None。
        """
        # WHERE code = ? AND project_id = ?，双重过滤
        stmt = select(KnowledgeBase).where(
            KnowledgeBase.code == code,
            KnowledgeBase.project_id == ctx.project_id,
        )
        return await self.session.scalar(stmt)

    async def list(
        self,
        ctx: "ProjectContext",
        status_filter: str | None = None,
    ) -> list[KnowledgeBase]:
        """列出当前项目的知识库，可按状态过滤。

        Args:
            ctx: 项目上下文。
            status_filter: 状态过滤，``active`` / ``disabled``；None 表示不过滤。

        Returns:
            KnowledgeBase 列表，按创建时间倒序。
        """
        # 强制 project_id 过滤
        stmt = (
            select(KnowledgeBase)
            .where(KnowledgeBase.project_id == ctx.project_id)
            .order_by(KnowledgeBase.created_at.desc())
        )
        if status_filter is not None:
            # 按状态过滤，便于只看启用或停用的知识库
            stmt = stmt.where(KnowledgeBase.status == status_filter)
        return list((await self.session.execute(stmt)).scalars().all())

    async def update(
        self,
        ctx: "ProjectContext",
        id: str,
        **fields: Any,
    ) -> KnowledgeBase | None:
        """按 ID 更新知识库字段（强制 project_id 过滤）。

        注意：``embedding_model`` / ``embedding_dimension`` / ``code`` 创建后不可改，
        调用方应避免传入这些字段。

        Args:
            ctx: 项目上下文。
            id: 知识库主键。
            **fields: 待更新字段，如 ``name``、``description``、``status``。

        Returns:
            更新后的 KnowledgeBase 实例；不存在或属于其他项目返回 None。
        """
        # 复用基类 update：UPDATE...RETURNING，带 project_id 过滤
        return await super().update(ctx, id, **fields)

    async def set_status(
        self,
        ctx: "ProjectContext",
        id: str,
        status: str,
    ) -> KnowledgeBase | None:
        """更新知识库状态（active / disabled）。

        停用后知识库不参与检索，但已有文档与 chunk 保留。

        Args:
            ctx: 项目上下文。
            id: 知识库主键。
            status: 目标状态，``active`` 或 ``disabled``。

        Returns:
            更新后的 KnowledgeBase 实例；不存在或属于其他项目返回 None。
        """
        # 复用 update，仅修改 status
        return await self.update(ctx, id, status=status)

    async def delete(self, ctx: "ProjectContext", id: str) -> bool:
        """删除知识库（仅空知识库可删，由 Service 层校验）。

        本方法仅做物理删除，是否允许删除（如知识库下有文档）由 Service 层校验。
        若 Service 层未校验直接调用，将级联删除文档与 chunk
        （由 documents 的复合外键 ``ON DELETE CASCADE`` 保证）。

        Args:
            ctx: 项目上下文。
            id: 知识库主键。

        Returns:
            True 表示删除成功；False 表示不存在或属于其他项目。
        """
        # 复用基类 delete：DELETE WHERE id = ? AND project_id = ?
        return await super().delete(ctx, id)

    async def count_documents(self, ctx: "ProjectContext", kb_id: str) -> int:
        """统计当前项目下指定知识库的文档数。

        为什么同时带 project_id 与 kb_id？
            ``kb_id`` 可能是其他项目的知识库 ID，仅按 kb_id 过滤会泄露其他项目数据。
            双重过滤保证即使 kb_id 错传也只返回 0。

        Args:
            ctx: 项目上下文。
            kb_id: 知识库 ID。

        Returns:
            文档总数。
        """
        # COUNT(*) WHERE project_id = ? AND knowledge_base_id = ?
        # 双重过滤杜绝跨项目统计
        stmt = (
            select(func.count())
            .select_from(Document)
            .where(
                Document.project_id == ctx.project_id,
                Document.knowledge_base_id == kb_id,
            )
        )
        return await self.session.scalar(stmt) or 0


# ---------------------------------------------------------------------------
# DocumentRepository
# ---------------------------------------------------------------------------
class DocumentRepository(BaseRepository):
    """文档 Repository：操作 ``documents`` 表。

    所有查询同时带 ``project_id`` 与业务外键（如 ``knowledge_base_id``）双重过滤，
    杜绝跨项目数据访问。
    """

    model = Document

    async def create(self, ctx: "ProjectContext", **fields: Any) -> Document:
        """创建文档记录。

        ``project_id`` 由 ctx 注入；``knowledge_base_id`` 必须由调用方传入，
        并在数据库层通过复合外键校验是否属于同一项目。

        Args:
            ctx: 项目上下文。
            **fields: 文档字段，必须包含 ``knowledge_base_id`` / ``source_type`` /
                ``title`` 等。

        Returns:
            创建后的 Document 实例。
        """
        # 强制 project_id 由 ctx 注入，覆盖可能传入的同名字段
        fields["project_id"] = ctx.project_id
        doc = Document(**fields)
        self.session.add(doc)
        await self.session.flush()
        return doc

    async def get_by_id(self, ctx: "ProjectContext", id: str) -> Document | None:
        """按 ID 查询文档（强制 project_id 过滤）。

        Args:
            ctx: 项目上下文。
            id: 文档主键。

        Returns:
            Document 实例；不存在或属于其他项目返回 None。
        """
        # WHERE id = ? AND project_id = ?
        stmt = select(Document).where(
            Document.id == id,
            Document.project_id == ctx.project_id,
        )
        return await self.session.scalar(stmt)

    async def get_by_external_id(
        self,
        ctx: "ProjectContext",
        kb_id: str,
        external_id: str,
    ) -> Document | None:
        """按项目+知识库+external_id 查询文档（外部业务幂等用）。

        为什么三重过滤？
            ``external_id`` 仅在项目+知识库内唯一，必须同时带 project_id 与
            knowledge_base_id 过滤，否则可能返回其他项目的文档。

        Args:
            ctx: 项目上下文。
            kb_id: 知识库 ID。
            external_id: 业务项目稳定资源 ID。

        Returns:
            Document 实例；不存在返回 None。
        """
        # 三重过滤：project_id + knowledge_base_id + external_id
        stmt = select(Document).where(
            Document.project_id == ctx.project_id,
            Document.knowledge_base_id == kb_id,
            Document.external_id == external_id,
        )
        return await self.session.scalar(stmt)

    async def get_by_content_hash(
        self,
        ctx: "ProjectContext",
        kb_id: str,
        content_hash: str,
    ) -> Document | None:
        """按内容哈希查询文档（去重用）。

        用于上传时检测同一知识库下是否已存在相同内容的文档，
        避免重复入库与向量化。

        Args:
            ctx: 项目上下文。
            kb_id: 知识库 ID。
            content_hash: 正文 SHA-256。

        Returns:
            已存在的 Document 实例；不存在返回 None。
        """
        # 三重过滤：project_id + knowledge_base_id + content_hash
        stmt = select(Document).where(
            Document.project_id == ctx.project_id,
            Document.knowledge_base_id == kb_id,
            Document.content_hash == content_hash,
        )
        return await self.session.scalar(stmt)

    async def list(
        self,
        ctx: "ProjectContext",
        kb_id: str | None = None,
        status_filter: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Document], int]:
        """分页列出当前项目的文档，可按知识库与状态过滤。

        Args:
            ctx: 项目上下文。
            kb_id: 知识库 ID，None 表示不限知识库。
            status_filter: 处理状态过滤，如 ``completed``。
            offset: 分页偏移。
            limit: 每页条数。

        Returns:
            元组 ``(items, total)``。
        """
        # 条件列表：始终带 project_id
        conditions = [Document.project_id == ctx.project_id]
        if kb_id is not None:
            # 限定知识库范围（仍带 project_id 双重过滤）
            conditions.append(Document.knowledge_base_id == kb_id)
        if status_filter is not None:
            # 按处理状态过滤
            conditions.append(Document.processing_status == status_filter)

        # 数据查询：按 created_at 倒序
        stmt_data = (
            select(Document)
            .where(*conditions)
            .order_by(Document.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        items = list((await self.session.execute(stmt_data)).scalars().all())

        # 计数查询
        stmt_count = select(func.count()).select_from(Document).where(*conditions)
        total = await self.session.scalar(stmt_count) or 0

        return items, total

    async def update(
        self,
        ctx: "ProjectContext",
        id: str,
        **fields: Any,
    ) -> Document | None:
        """按 ID 更新文档字段（强制 project_id 过滤）。

        Args:
            ctx: 项目上下文。
            id: 文档主键。
            **fields: 待更新字段。

        Returns:
            更新后的 Document 实例；不存在或属于其他项目返回 None。
        """
        # 复用基类 update
        return await super().update(ctx, id, **fields)

    async def set_processing_status(
        self,
        ctx: "ProjectContext",
        id: str,
        status: str,
    ) -> Document | None:
        """更新文档处理状态（pending / processing / completed / failed）。

        入库流程中由 IngestionWorker 调用，反映当前处理阶段。

        Args:
            ctx: 项目上下文。
            id: 文档主键。
            status: 处理状态。

        Returns:
            更新后的 Document 实例；不存在返回 None。
        """
        return await self.update(ctx, id, processing_status=status)

    async def set_enabled(
        self,
        ctx: "ProjectContext",
        id: str,
        enabled: bool,
    ) -> Document | None:
        """更新文档是否参与检索（enabled=true 时参与向量化与召回）。

        Args:
            ctx: 项目上下文。
            id: 文档主键。
            enabled: 是否启用。

        Returns:
            更新后的 Document 实例；不存在返回 None。
        """
        return await self.update(ctx, id, enabled=enabled)

    async def delete(self, ctx: "ProjectContext", id: str) -> bool:
        """删除文档（级联删除其下所有 chunk）。

        Args:
            ctx: 项目上下文。
            id: 文档主键。

        Returns:
            True 表示删除成功；False 表示不存在或属于其他项目。
        """
        # 复用基类 delete
        # document_chunks 通过复合外键 ON DELETE CASCADE 自动级联删除
        return await super().delete(ctx, id)


# ---------------------------------------------------------------------------
# DocumentChunkRepository
# ---------------------------------------------------------------------------
class DocumentChunkRepository(BaseRepository):
    """文档分块 Repository：操作 ``document_chunks`` 表。

    检索逻辑（向量检索 + 全文检索 + RRF 合并）集中在
    ``app.modules.retrieval.hybrid_searcher.HybridSearcher``，
    本 Repository 仅提供分块 CRUD 与批量操作，避免检索 SQL 分散在多处难以维护。

    通用 CRUD 方法（list_by_document / delete_by_document / set_enabled /
    bulk_create）已实现，供入库流程与文档管理接口调用。
    """

    model = DocumentChunk

    async def create(self, ctx: "ProjectContext", **fields: Any) -> DocumentChunk:
        """创建单个分块。

        Args:
            ctx: 项目上下文。
            **fields: 分块字段，必须包含 ``document_id`` / ``chunk_index`` / ``content``。

        Returns:
            创建后的 DocumentChunk 实例。
        """
        # project_id 由 ctx 注入
        fields["project_id"] = ctx.project_id
        chunk = DocumentChunk(**fields)
        self.session.add(chunk)
        await self.session.flush()
        return chunk

    async def bulk_create(
        self,
        ctx: "ProjectContext",
        chunks: list[dict[str, Any]],
    ) -> list[DocumentChunk]:
        """批量创建分块（入库流程中分块后一次性写入）。

        使用 ``session.add_all`` + ``flush``，比逐条 add 更高效。
        ``project_id`` 由 ctx 统一注入，避免每条 dict 都重复传入。

        Args:
            ctx: 项目上下文。
            chunks: 分块字段字典列表，每个 dict 包含 ``document_id`` /
                ``chunk_index`` / ``content`` 等。

        Returns:
            创建后的 DocumentChunk 实例列表（含数据库生成的 id）。
        """
        # 构造 ORM 实例列表，统一注入 project_id
        instances = [
            DocumentChunk(**{**chunk, "project_id": ctx.project_id})
            for chunk in chunks
        ]
        # add_all 批量加入会话
        self.session.add_all(instances)
        # flush 触发数据库默认值填充与约束检查
        await self.session.flush()
        return instances

    async def list_by_document(
        self,
        ctx: "ProjectContext",
        document_id: str,
    ) -> list[DocumentChunk]:
        """列出指定文档的所有分块，按 chunk_index 排序。

        Args:
            ctx: 项目上下文。
            document_id: 文档 ID。

        Returns:
            DocumentChunk 列表，按 chunk_index 升序。
        """
        # 双重过滤：project_id + document_id
        stmt = (
            select(DocumentChunk)
            .where(
                DocumentChunk.project_id == ctx.project_id,
                DocumentChunk.document_id == document_id,
            )
            .order_by(DocumentChunk.chunk_index.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def delete_by_document(
        self,
        ctx: "ProjectContext",
        document_id: str,
    ) -> int:
        """删除指定文档的所有分块（重新分块前清理旧分块用）。

        Args:
            ctx: 项目上下文。
            document_id: 文档 ID。

        Returns:
            删除的分块数量。
        """
        # 使用 DELETE WHERE，返回受影响行数
        from sqlalchemy import delete as sa_delete

        stmt = sa_delete(DocumentChunk).where(
            DocumentChunk.project_id == ctx.project_id,
            DocumentChunk.document_id == document_id,
        )
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def set_enabled(
        self,
        ctx: "ProjectContext",
        document_id: str,
        enabled: bool,
    ) -> None:
        """批量更新指定文档下所有分块的 enabled 状态。

        与文档级别的 enabled 配合：文档禁用时，所有分块也禁用，
        检索时通过部分索引 ``idx_chunks_project_kb WHERE enabled = true`` 过滤。

        Args:
            ctx: 项目上下文。
            document_id: 文档 ID。
            enabled: 是否启用。
        """
        from sqlalchemy import update as sa_update

        # UPDATE document_chunks SET enabled = ? WHERE project_id = ? AND document_id = ?
        stmt = (
            sa_update(DocumentChunk)
            .where(
                DocumentChunk.project_id == ctx.project_id,
                DocumentChunk.document_id == document_id,
            )
            .values(enabled=enabled)
        )
        await self.session.execute(stmt)

    # ------------------------------------------------------------------
    # 检索方法说明
    # ------------------------------------------------------------------
    # 向量检索与全文检索的实现在 ``app.modules.retrieval.hybrid_searcher.HybridSearcher``，
    # 检索逻辑集中在 HybridSearcher 中而非 Repository，原因：
    # 1. 混合检索需同时调用两路 SQL 并做 RRF 合并，封装为独立 Searcher 更内聚
    # 2. Repository 职责是单表 CRUD，检索涉及 SQL 构造、Embedding 调用、
    #    排名合并等多步流程，放在 Repository 会膨胀且难以单元测试
    # 3. 后续如需扩展（如重排、过滤、多路召回），Searcher 模式更易扩展
    #
    # 如需直接调用单路检索，可参考 HybridSearcher._vector_search / _fulltext_search
    # 的实现，或在 Searcher 上新增 public 方法。
