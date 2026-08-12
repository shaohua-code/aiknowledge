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
    query_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="running", nullable=False)
    answer_mode: Mapped[str | None] = mapped_column(String(40))
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    degraded_reasons: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    total_ms: Mapped[int | None] = mapped_column(Integer)
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
