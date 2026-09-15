from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from knowledge_core.config import settings
from knowledge_core.infrastructure.database import Base, IdMixin, TimestampMixin


class Application(Base, IdMixin, TimestampMixin):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("code", name="uq_applications_code"),)

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    application_type: Mapped[str] = mapped_column(String(32), default="general", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)


class ApplicationEnvironment(Base, IdMixin, TimestampMixin):
    __tablename__ = "application_environments"
    __table_args__ = (
        UniqueConstraint("application_id", "code", name="uq_environment_app_code"),
        UniqueConstraint("application_id", "id", name="uq_environment_app_id"),
    )

    application_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)


class ApplicationApiKey(Base, IdMixin, TimestampMixin):
    __tablename__ = "application_api_keys"
    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "environment_id"],
            ["application_environments.application_id", "application_environments.id"],
            ondelete="CASCADE",
        ),
        Index("ix_api_keys_prefix_status", "key_prefix", "status"),
    )

    application_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    environment_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApplicationBoundMixin:
    application_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    environment_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)


class TransactionalOutbox(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    """事务内待派发事件；消息投递失败不会丢失已提交的领域变更。"""

    __tablename__ = "transactional_outbox"
    __table_args__ = (Index("ix_outbox_pending", "published_at", "created_at"),)

    topic: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class IdempotencyRecord(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    """记录异步写请求的幂等结果，避免网络重试重复创建文档或任务。"""

    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "application_id",
            "environment_id",
            "operation",
            "idempotency_key",
            name="uq_idempotency_context_operation_key",
        ),
        Index(
            "ix_idempotency_expiry",
            "application_id",
            "environment_id",
            "expires_at",
        ),
    )

    # 操作名使同一个客户端键可以安全地在不同异步写入口复用。
    operation: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelCatalogRecord(Base, IdMixin, TimestampMixin):
    """模型目录的结构化非密钥记录；实际凭证始终只引用部署环境变量。"""

    __tablename__ = "model_catalog_records"

    code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    # 名称是管理员识别目录条目的业务字段，不能只依赖不可读的 code。
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    capability: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key_env: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    note: Mapped[str | None] = mapped_column(Text)
    # 每次目录写入递增，控制面可据此拒绝基于旧快照的并发覆盖。
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ApplicationModelBindingRecord(Base, IdMixin, TimestampMixin):
    """应用默认与环境覆盖的模型绑定，环境为空时代表应用默认绑定。"""

    __tablename__ = "application_model_binding_records"
    __table_args__ = (
        ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["application_id", "environment_id"],
            ["application_environments.application_id", "application_environments.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["catalog_entry_id"], ["model_catalog_records.id"], ondelete="RESTRICT"
        ),
        # PostgreSQL 的 NULL 不参与普通唯一约束，因此分别约束应用默认和环境覆盖。
        Index(
            "uq_model_binding_application_capability",
            "application_id",
            "capability",
            unique=True,
            postgresql_where=text("environment_id IS NULL"),
        ),
        Index(
            "uq_model_binding_environment_capability",
            "environment_id",
            "capability",
            unique=True,
            postgresql_where=text("environment_id IS NOT NULL"),
        ),
    )

    application_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    environment_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    catalog_entry_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    capability: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class PlatformModelDefaultRecord(Base, IdMixin, TimestampMixin):
    """平台能力默认模型；仅保存目录引用，以便解析链明确显示来源。"""

    __tablename__ = "platform_model_default_records"

    capability: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    catalog_entry_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("model_catalog_records.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class KnowledgeCollection(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    __tablename__ = "knowledge_collections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "environment_id"],
            ["application_environments.application_id", "application_environments.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "application_id", "environment_id", "code", name="uq_collection_context_code"
        ),
        UniqueConstraint("application_id", "environment_id", "id", name="uq_collection_context_id"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    document_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Source(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    __tablename__ = "sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "environment_id", "collection_id"],
            [
                "knowledge_collections.application_id",
                "knowledge_collections.environment_id",
                "knowledge_collections.id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint("application_id", "environment_id", "id", name="uq_source_context_id"),
    )

    collection_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Document(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    __tablename__ = "documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "environment_id", "collection_id"],
            [
                "knowledge_collections.application_id",
                "knowledge_collections.environment_id",
                "knowledge_collections.id",
            ],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["application_id", "environment_id", "source_id"],
            ["sources.application_id", "sources.environment_id", "sources.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("application_id", "environment_id", "id", name="uq_document_context_id"),
    )

    collection_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    source_id: Mapped[UUID | None] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(160))
    storage_key: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    current_version: Mapped[int | None] = mapped_column(Integer)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentRevision(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    __tablename__ = "document_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "environment_id", "document_id"],
            ["documents.application_id", "documents.environment_id", "documents.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "application_id", "environment_id", "document_id", "version", name="uq_revision_version"
        ),
        UniqueConstraint("application_id", "environment_id", "id", name="uq_revision_context_id"),
    )

    document_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    # Embedding 空间指纹属于索引兼容条件；旧版本为空时不会被新模型的向量查询召回。
    embedding_fingerprint: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentChunk(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    __tablename__ = "document_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "environment_id", "revision_id"],
            [
                "document_revisions.application_id",
                "document_revisions.environment_id",
                "document_revisions.id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "application_id", "environment_id", "revision_id", "chunk_index", name="uq_chunk_index"
        ),
        Index("ix_chunks_context_revision", "application_id", "environment_id", "revision_id"),
    )

    revision_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(240))
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.embedding_dimension))


class IngestionRun(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    __tablename__ = "ingestion_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "environment_id", "document_id"],
            ["documents.application_id", "documents.environment_id", "documents.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["application_id", "environment_id", "revision_id"],
            [
                "document_revisions.application_id",
                "document_revisions.environment_id",
                "document_revisions.id",
            ],
            ondelete="CASCADE",
        ),
    )

    document_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    revision_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    stage: Mapped[str] = mapped_column(String(24), default="received", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    request_id: Mapped[str] = mapped_column(String(80), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngestionAttempt(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    """记录每次实际 Worker 执行，区分人工重试与消息重复投递。"""

    __tablename__ = "ingestion_attempts"
    __table_args__ = (
        ForeignKeyConstraint(["run_id"], ["ingestion_runs.id"], ondelete="CASCADE"),
        UniqueConstraint("run_id", "attempt_number", name="uq_ingestion_attempt_number"),
        Index("ix_ingestion_attempts_run_created", "run_id", "created_at"),
    )

    run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="running", nullable=False)
    stage: Mapped[str] = mapped_column(String(24), default="received", nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngestionEmbeddingCheckpoint(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    """保存已完成 Embedding 批次，供同一入库任务的安全重试复用。"""

    __tablename__ = "ingestion_embedding_checkpoints"
    __table_args__ = (
        ForeignKeyConstraint(["run_id"], ["ingestion_runs.id"], ondelete="CASCADE"),
        UniqueConstraint("run_id", "batch_index", name="uq_ingestion_embedding_checkpoint"),
        Index("ix_ingestion_checkpoint_run", "run_id", "batch_index"),
    )

    # 内容摘要和模型指纹同时命中才允许复用，避免同维度不同模型或内容变更时混用向量。
    run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    batch_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_start: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    embeddings: Mapped[list[list[float]]] = mapped_column(JSON, nullable=False)


class RetrievalProfile(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    __tablename__ = "retrieval_profiles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "environment_id"],
            ["application_environments.application_id", "application_environments.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "application_id", "environment_id", "code", name="uq_retrieval_profile_code"
        ),
        UniqueConstraint("application_id", "environment_id", "id", name="uq_retrieval_profile_id"),
    )

    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    collection_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    top_k: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    minimum_score: Mapped[float] = mapped_column(Float, default=0.55, nullable=False)
    vector_weight: Mapped[float] = mapped_column(Float, default=0.65, nullable=False)
    lexical_weight: Mapped[float] = mapped_column(Float, default=0.35, nullable=False)
    metadata_filters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)


class AnswerProfile(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    __tablename__ = "answer_profiles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "environment_id", "retrieval_profile_id"],
            [
                "retrieval_profiles.application_id",
                "retrieval_profiles.environment_id",
                "retrieval_profiles.id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("application_id", "environment_id", "code", name="uq_answer_profile_code"),
    )

    retrieval_profile_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    tool_codes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    knowledge_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    model_fallback_allowed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    web_fallback_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    minimum_evidence_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    minimum_evidence_score: Mapped[float] = mapped_column(Float, default=0.55, nullable=False)
    require_fresh_data: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    maximum_data_age_seconds: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)


class RetrievalProfileRevision(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    """检索策略的不可变配置快照；发布后运行时可精确复现当时的参数。"""

    __tablename__ = "retrieval_profile_revisions"
    __table_args__ = (
        # 策略主表以全局主键关联；与已执行的 0006 迁移保持一致，避免元数据漂移。
        ForeignKeyConstraint(["profile_id"], ["retrieval_profiles.id"], ondelete="CASCADE"),
        UniqueConstraint(
            "application_id",
            "environment_id",
            "profile_id",
            "revision",
            name="uq_retrieval_revision",
        ),
    )

    profile_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnswerProfileRevision(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    """回答策略的不可变配置快照，显式记录所依赖的检索修订。"""

    __tablename__ = "answer_profile_revisions"
    __table_args__ = (
        ForeignKeyConstraint(["profile_id"], ["answer_profiles.id"], ondelete="CASCADE"),
        UniqueConstraint(
            "application_id", "environment_id", "profile_id", "revision", name="uq_answer_revision"
        ),
    )

    profile_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    retrieval_revision_id: Mapped[UUID | None] = mapped_column(Uuid)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProfileRelease(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    """不可变发布事件；回滚通过创建指向旧修订的新事件，而非改写历史。"""

    __tablename__ = "profile_releases"

    profile_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    profile_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    revision_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False, default="published")


class EvaluationSet(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    """应用环境内固定评测集，样本版本独立保存以支持后续对比。"""

    __tablename__ = "evaluation_sets"
    # 同一环境的编码是稳定的自动化引用，不允许并发请求创建两个同名评测集。
    __table_args__ = (
        UniqueConstraint(
            "application_id", "environment_id", "code", name="uq_evaluation_set_context_code"
        ),
    )

    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)


class EvaluationCaseRevision(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    """评测样本的不可变修订，保存问题、预期约束和脱敏临时输入。"""

    __tablename__ = "evaluation_case_revisions"

    evaluation_set_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    case_code: Mapped[str] = mapped_column(String(80), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    expected_document_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    expected_answer_modes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    expected_output_schema: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    labels: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)


class EvaluationRun(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    """一次评测运行固定策略修订和样本集，技术状态与门禁结论分离。"""

    __tablename__ = "evaluation_runs"

    evaluation_set_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    answer_profile_revision_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    retrieval_profile_revision_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    gate_passed: Mapped[bool | None] = mapped_column(Boolean)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class EvaluationCaseResult(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    """单样本评测结果，保存确定性检查和失败原因而非仅一个总分。"""

    __tablename__ = "evaluation_case_results"

    evaluation_run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    case_revision_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    checks: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))


class RequestTrace(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    __tablename__ = "request_traces"
    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "environment_id"],
            ["application_environments.application_id", "application_environments.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "application_id", "environment_id", "id", name="uq_request_trace_context_id"
        ),
        Index("ix_request_trace_context_created", "application_id", "environment_id", "created_at"),
    )

    request_id: Mapped[str] = mapped_column(String(80), nullable=False)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    profile_code: Mapped[str | None] = mapped_column(String(80))
    # 运行请求固定记录解析到的不可变修订，旧追踪允许为空以保持兼容。
    answer_profile_revision_id: Mapped[UUID | None] = mapped_column(Uuid)
    retrieval_profile_revision_id: Mapped[UUID | None] = mapped_column(Uuid)
    query_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="running", nullable=False)
    answer_mode: Mapped[str | None] = mapped_column(String(40))
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    degraded_reasons: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    total_ms: Mapped[int | None] = mapped_column(Integer)
    # 阶段耗时对历史轨迹可为空，避免迁移时伪造无法复原的诊断数据。
    retrieval_ms: Mapped[int | None] = mapped_column(Integer)
    model_ms: Mapped[int | None] = mapped_column(Integer)
    web_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))


class EvidenceRecord(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    __tablename__ = "evidence_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "environment_id", "trace_id"],
            ["request_traces.application_id", "request_traces.environment_id", "request_traces.id"],
            ondelete="CASCADE",
        ),
    )

    trace_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_id: Mapped[UUID | None] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    citation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class UserFeedback(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    __tablename__ = "user_feedback"
    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "environment_id", "trace_id"],
            ["request_traces.application_id", "request_traces.environment_id", "request_traces.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("application_id", "environment_id", "trace_id", name="uq_feedback_trace"),
    )

    trace_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(80))
    comment: Mapped[str | None] = mapped_column(Text)


class AuditEvent(Base, IdMixin, TimestampMixin, ApplicationBoundMixin):
    __tablename__ = "audit_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "environment_id"],
            ["application_environments.application_id", "application_environments.id"],
            ondelete="CASCADE",
        ),
        Index("ix_audit_context_created", "application_id", "environment_id", "created_at"),
    )

    actor: Mapped[str] = mapped_column(String(160), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    request_id: Mapped[str] = mapped_column(String(80), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
