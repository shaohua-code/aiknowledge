"""知识库相关模型：KnowledgeBase / Document / DocumentChunk。

对应 SubTask 4.1 / 4.2 / 4.3：知识库主表、文档表、文档分块表。

设计要点（重点：复合外键实现项目强隔离）
----------------------------------------
1. 父表（如 knowledge_bases）持有复合唯一约束 ``UniqueConstraint("project_id", "id")``，
   保证 ``id`` 在项目内唯一。
2. 子表（如 documents）使用复合外键
   ``ForeignKeyConstraint(["project_id", "knowledge_base_id"],
                          ["knowledge_bases.project_id", "knowledge_bases.id"])``
   强制子表行必须与父表行属于同一项目，从数据库层杜绝跨项目引用。
3. ``DocumentChunk`` 同时承载 TSVECTOR 全文索引与 pgvector 向量列，
   是混合检索（关键词 + 向量）的物理基础。
4. ``embedding`` 维度由 ``settings.embedding_dimension`` 决定，默认 1536。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CHAR,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
# CIText 在 SQLAlchemy 2.0+ 需自定义（见 app.db.models.types）
from app.db.models.types import CIText
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.db.models.base import Base, ProjectOwnedMixin, TimestampMixin


class KnowledgeBase(Base, ProjectOwnedMixin, TimestampMixin):
    """知识库表：项目内文档与向量的逻辑容器。

    每个 KnowledgeBase 绑定一个 embedding_model 与 embedding_dimension，
    同一知识库内所有 chunk 的向量维度一致，便于 HNSW 索引复用。

    表名: knowledge_bases

    复合唯一约束
    ------------
    ``uq_knowledge_bases_project_id_id (project_id, id)``：
    供 documents 表复合外键引用，保证 id 在项目内唯一。
    """

    __tablename__ = "knowledge_bases"
    __table_args__ = (
        # 复合唯一约束：供子表 documents 复合外键引用
        UniqueConstraint(
            "project_id",
            "id",
            name="uq_knowledge_bases_project_id_id",
        ),
        # 项目内 code 唯一索引：知识库 code 在项目内唯一
        UniqueConstraint(
            "project_id",
            "code",
            name="uq_knowledge_bases_project_code",
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="知识库 ID",
    )
    # 知识库名称
    name: Mapped[str] = mapped_column(String(120), nullable=False, comment="知识库名称")
    # 知识库编码：CIText 大小写不敏感，项目内唯一
    code: Mapped[str] = mapped_column(
        CIText(), nullable=False, comment="知识库编码（项目内唯一，大小写不敏感）"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="知识库描述")
    # Embedding 模型名称：决定向量维度，创建后不可改
    embedding_model: Mapped[str | None] = mapped_column(
        String(120), nullable=True, comment="Embedding 模型名称"
    )
    # 向量维度：必须与 embedding_model 匹配，影响 document_chunks.embedding 列类型
    embedding_dimension: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="向量维度"
    )
    # 状态：active=启用 / disabled=停用
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'active'"),
        comment="状态：active / disabled",
    )


class Document(Base, ProjectOwnedMixin, TimestampMixin):
    """文档表：知识库内的原始文档记录。

    一个 Document 对应一份上传文件、URL 抓取结果或手动录入内容，
    通过 ingestion 流程拆分为多个 DocumentChunk。

    表名: documents

    复合外键
    --------
    ``fk_documents_project_knowledge_base``：
    ``FOREIGN KEY (project_id, knowledge_base_id)
       REFERENCES knowledge_bases(project_id, id)``
    强制文档与知识库必须同属一个项目。

    复合唯一约束
    ------------
    ``uq_documents_project_id_id (project_id, id)``：供 document_chunks 复合外键引用。
    """

    __tablename__ = "documents"
    __table_args__ = (
        # 复合外键：强制文档与知识库同项目
        ForeignKeyConstraint(
            ["project_id", "knowledge_base_id"],
            ["knowledge_bases.project_id", "knowledge_bases.id"],
            name="fk_documents_project_knowledge_base",
        ),
        # 复合唯一约束：供 document_chunks 复合外键引用
        UniqueConstraint(
            "project_id",
            "id",
            name="uq_documents_project_id_id",
        ),
        # 索引：按项目+处理状态+创建时间查询文档列表
        Index(
            "idx_documents_project_status",
            "project_id",
            "processing_status",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="文档 ID",
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, comment="所属知识库 ID"
    )
    # 来源类型：file=上传文件 / url=URL 抓取 / manual=手动录入 / crawler=爬虫采集
    source_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="来源类型：file / url / manual / crawler"
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, comment="文档标题")
    # 原始 URL：来源为 url/crawler 时填写
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True, comment="原始 URL")
    # 对象存储路径：以 projects/{project_id}/ 开头，实现存储层项目隔离
    storage_key: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="对象存储路径（projects/{project_id}/ 前缀）"
    )
    mime_type: Mapped[str | None] = mapped_column(
        String(120), nullable=True, comment="MIME 类型"
    )
    # 正文 SHA-256：用于去重与变更检测
    content_hash: Mapped[str | None] = mapped_column(
        CHAR(64), nullable=True, comment="正文 SHA-256"
    )
    # 处理状态：pending=待处理 / processing=处理中 / completed=已完成 / failed=失败
    processing_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'pending'"),
        comment="处理状态：pending / processing / completed / failed",
    )
    # 是否参与检索：false 时跳过向量化与检索召回
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        comment="是否参与检索",
    )
    # 元数据：JSONB，存储页数/作者/发布时间等扩展信息
    # 列名 metadata_ 避免与 SQLAlchemy 保留属性冲突
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB, nullable=True, comment="元数据（JSONB）"
    )
    # 业务项目稳定资源 ID：可空，项目+知识库内唯一，用于外部系统幂等
    external_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True, comment="业务项目稳定资源 ID"
    )


class DocumentChunk(Base, ProjectOwnedMixin, TimestampMixin):
    """文档分块表：承载向量化与全文索引的最小检索单元。

    每个文档拆分为多个 chunk，每个 chunk 同时存储：
    1. ``content``：原始文本，用于 RAG 上下文拼接。
    2. ``content_tsv``：PostgreSQL TSVECTOR，由 ``to_tsvector`` 生成，支持 GIN 全文检索。
    3. ``embedding``：pgvector 向量，支持 HNSW 余弦相似度检索。

    混合检索流程：TSVECTOR 关键词召回 + 向量召回 + RRF 合并 + project_id 前置过滤。

    表名: document_chunks

    复合外键
    --------
    ``fk_chunks_project_document``：
    ``FOREIGN KEY (project_id, document_id)
       REFERENCES documents(project_id, id) ON DELETE CASCADE``
    文档删除时级联删除其下所有 chunk。
    """

    __tablename__ = "document_chunks"
    __table_args__ = (
        # 复合外键：强制 chunk 与文档同项目，文档删除时级联
        ForeignKeyConstraint(
            ["project_id", "document_id"],
            ["documents.project_id", "documents.id"],
            ondelete="CASCADE",
            name="fk_chunks_project_document",
        ),
        # 部分索引：仅对 enabled=true 的 chunk 建索引，加速检索过滤
        Index(
            "idx_chunks_project_kb",
            "project_id",
            "knowledge_base_id",
            postgresql_where=text("enabled = true"),
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="分块 ID",
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, comment="所属知识库 ID"
    )
    document_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, comment="所属文档 ID"
    )
    # 分块序号：文档内递增，用于排序
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, comment="分块序号")
    # 原始文本：用于 RAG 上下文拼接
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="分块原始文本")
    # 全文索引列：TSVECTOR，由 Alembic 用 raw SQL 创建 GIN 索引
    content_tsv: Mapped[Any] = mapped_column(
        TSVECTOR, nullable=True, comment="全文索引列（TSVECTOR）"
    )
    # 向量列：pgvector Vector，维度由 settings.embedding_dimension 决定（默认 1536）
    # HNSW 索引由 Alembic 用 raw SQL 创建（vector_cosine_ops）
    embedding: Mapped[Any] = mapped_column(
        Vector(settings.embedding_dimension), nullable=True, comment="分块向量"
    )
    # token 数：用于上下文长度估算
    token_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="token 数"
    )
    # 页码：可空，PDF 等分页文档使用
    page_number: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="页码"
    )
    # 元数据：JSONB
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB, nullable=True, comment="元数据（JSONB）"
    )
    # 是否参与检索：false 时跳过召回
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        comment="是否参与检索",
    )
