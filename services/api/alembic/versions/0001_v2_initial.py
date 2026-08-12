"""AI 知识能力底座 V2 初始结构。

Revision ID: 0001_v2_initial
Revises: None
Create Date: 2026-08-11
"""

from __future__ import annotations

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision = "0001_v2_initial"
down_revision = None
branch_labels = None
depends_on = None


def _context_columns() -> list[sa.Column]:
    return [
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
    ]


def _identity_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "applications",
        *_identity_columns(),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("application_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_applications_code"),
    )
    op.create_table(
        "application_environments",
        *_identity_columns(),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(24), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "code", name="uq_environment_app_code"),
        sa.UniqueConstraint("application_id", "id", name="uq_environment_app_id"),
    )
    op.create_index(
        "ix_application_environments_application_id", "application_environments", ["application_id"]
    )

    op.create_table(
        "application_api_keys",
        *_identity_columns(),
        *_context_columns(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("key_prefix", sa.String(32), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["application_id", "environment_id"],
            ["application_environments.application_id", "application_environments.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_keys_prefix_status", "application_api_keys", ["key_prefix", "status"])
    op.create_index(
        "ix_application_api_keys_application_id", "application_api_keys", ["application_id"]
    )
    op.create_index(
        "ix_application_api_keys_environment_id", "application_api_keys", ["environment_id"]
    )

    op.create_table(
        "knowledge_collections",
        *_identity_columns(),
        *_context_columns(),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("document_count", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("last_published_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["application_id", "environment_id"],
            ["application_environments.application_id", "application_environments.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "application_id", "environment_id", "code", name="uq_collection_context_code"
        ),
        sa.UniqueConstraint(
            "application_id", "environment_id", "id", name="uq_collection_context_id"
        ),
    )
    op.create_index(
        "ix_knowledge_collections_application_id", "knowledge_collections", ["application_id"]
    )
    op.create_index(
        "ix_knowledge_collections_environment_id", "knowledge_collections", ["environment_id"]
    )

    op.create_table(
        "sources",
        *_identity_columns(),
        *_context_columns(),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["application_id", "environment_id", "collection_id"],
            [
                "knowledge_collections.application_id",
                "knowledge_collections.environment_id",
                "knowledge_collections.id",
            ],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "environment_id", "id", name="uq_source_context_id"),
    )
    op.create_index("ix_sources_application_id", "sources", ["application_id"])
    op.create_index("ix_sources_environment_id", "sources", ["environment_id"])
    op.create_index("ix_sources_collection_id", "sources", ["collection_id"])

    op.create_table(
        "documents",
        *_identity_columns(),
        *_context_columns(),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid()),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("mime_type", sa.String(160)),
        sa.Column("storage_key", sa.Text()),
        sa.Column("source_url", sa.Text()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("current_version", sa.Integer()),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["application_id", "environment_id", "collection_id"],
            [
                "knowledge_collections.application_id",
                "knowledge_collections.environment_id",
                "knowledge_collections.id",
            ],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["application_id", "environment_id", "source_id"],
            ["sources.application_id", "sources.environment_id", "sources.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "application_id", "environment_id", "id", name="uq_document_context_id"
        ),
    )
    op.create_index("ix_documents_application_id", "documents", ["application_id"])
    op.create_index("ix_documents_environment_id", "documents", ["environment_id"])
    op.create_index("ix_documents_collection_id", "documents", ["collection_id"])

    op.create_table(
        "document_revisions",
        *_identity_columns(),
        *_context_columns(),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.Text()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["application_id", "environment_id", "document_id"],
            ["documents.application_id", "documents.environment_id", "documents.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "application_id", "environment_id", "document_id", "version", name="uq_revision_version"
        ),
        sa.UniqueConstraint(
            "application_id", "environment_id", "id", name="uq_revision_context_id"
        ),
    )
    op.create_index(
        "ix_document_revisions_application_id", "document_revisions", ["application_id"]
    )
    op.create_index(
        "ix_document_revisions_environment_id", "document_revisions", ["environment_id"]
    )
    op.create_index("ix_document_revisions_document_id", "document_revisions", ["document_id"])

    op.create_table(
        "document_chunks",
        *_identity_columns(),
        *_context_columns(),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer()),
        sa.Column("section", sa.String(240)),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("embedding", Vector(1536)),
        sa.ForeignKeyConstraint(
            ["application_id", "environment_id", "revision_id"],
            [
                "document_revisions.application_id",
                "document_revisions.environment_id",
                "document_revisions.id",
            ],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "application_id", "environment_id", "revision_id", "chunk_index", name="uq_chunk_index"
        ),
    )
    op.create_index(
        "ix_chunks_context_revision",
        "document_chunks",
        ["application_id", "environment_id", "revision_id"],
    )
    op.create_index("ix_document_chunks_revision_id", "document_chunks", ["revision_id"])

    op.create_table(
        "ingestion_runs",
        *_identity_columns(),
        *_context_columns(),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("stage", sa.String(24), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(80), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["application_id", "environment_id", "document_id"],
            ["documents.application_id", "documents.environment_id", "documents.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["application_id", "environment_id", "revision_id"],
            [
                "document_revisions.application_id",
                "document_revisions.environment_id",
                "document_revisions.id",
            ],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ingestion_runs_application_id", "ingestion_runs", ["application_id"])
    op.create_index("ix_ingestion_runs_environment_id", "ingestion_runs", ["environment_id"])
    op.create_index("ix_ingestion_runs_document_id", "ingestion_runs", ["document_id"])
    op.create_index("ix_ingestion_runs_revision_id", "ingestion_runs", ["revision_id"])

    op.create_table(
        "retrieval_profiles",
        *_identity_columns(),
        *_context_columns(),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("collection_ids", sa.JSON(), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("minimum_score", sa.Float(), nullable=False),
        sa.Column("vector_weight", sa.Float(), nullable=False),
        sa.Column("lexical_weight", sa.Float(), nullable=False),
        sa.Column("metadata_filters", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id", "environment_id"],
            ["application_environments.application_id", "application_environments.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "application_id", "environment_id", "code", name="uq_retrieval_profile_code"
        ),
        sa.UniqueConstraint(
            "application_id", "environment_id", "id", name="uq_retrieval_profile_id"
        ),
    )
    op.create_index(
        "ix_retrieval_profiles_application_id", "retrieval_profiles", ["application_id"]
    )
    op.create_index(
        "ix_retrieval_profiles_environment_id", "retrieval_profiles", ["environment_id"]
    )

    op.create_table(
        "answer_profiles",
        *_identity_columns(),
        *_context_columns(),
        sa.Column("retrieval_profile_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("output_schema", sa.JSON(), nullable=False),
        sa.Column("tool_codes", sa.JSON(), nullable=False),
        sa.Column("knowledge_required", sa.Boolean(), nullable=False),
        sa.Column("model_fallback_allowed", sa.Boolean(), nullable=False),
        sa.Column("web_fallback_allowed", sa.Boolean(), nullable=False),
        sa.Column("minimum_evidence_count", sa.Integer(), nullable=False),
        sa.Column("minimum_evidence_score", sa.Float(), nullable=False),
        sa.Column("require_fresh_data", sa.Boolean(), nullable=False),
        sa.Column("maximum_data_age_seconds", sa.Integer()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id", "environment_id", "retrieval_profile_id"],
            [
                "retrieval_profiles.application_id",
                "retrieval_profiles.environment_id",
                "retrieval_profiles.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "application_id", "environment_id", "code", name="uq_answer_profile_code"
        ),
    )
    op.create_index("ix_answer_profiles_application_id", "answer_profiles", ["application_id"])
    op.create_index("ix_answer_profiles_environment_id", "answer_profiles", ["environment_id"])

    op.create_table(
        "request_traces",
        *_identity_columns(),
        *_context_columns(),
        sa.Column("request_id", sa.String(80), nullable=False),
        sa.Column("operation", sa.String(40), nullable=False),
        sa.Column("profile_code", sa.String(80)),
        sa.Column("query_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("answer_mode", sa.String(40)),
        sa.Column("confidence", sa.Float()),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("degraded", sa.Boolean(), nullable=False),
        sa.Column("degraded_reasons", sa.JSON(), nullable=False),
        sa.Column("total_ms", sa.Integer()),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.ForeignKeyConstraint(
            ["application_id", "environment_id"],
            ["application_environments.application_id", "application_environments.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "application_id", "environment_id", "id", name="uq_request_trace_context_id"
        ),
    )
    op.create_index(
        "ix_request_trace_context_created",
        "request_traces",
        ["application_id", "environment_id", "created_at"],
    )

    op.create_table(
        "evidence_records",
        *_identity_columns(),
        *_context_columns(),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column("source_id", sa.Uuid()),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("citation", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id", "environment_id", "trace_id"],
            ["request_traces.application_id", "request_traces.environment_id", "request_traces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evidence_records_trace_id", "evidence_records", ["trace_id"])

    op.create_table(
        "user_feedback",
        *_identity_columns(),
        *_context_columns(),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(80)),
        sa.Column("comment", sa.Text()),
        sa.ForeignKeyConstraint(
            ["application_id", "environment_id", "trace_id"],
            ["request_traces.application_id", "request_traces.environment_id", "request_traces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "application_id", "environment_id", "trace_id", name="uq_feedback_trace"
        ),
    )
    op.create_index("ix_user_feedback_trace_id", "user_feedback", ["trace_id"])

    op.create_table(
        "audit_events",
        *_identity_columns(),
        *_context_columns(),
        sa.Column("actor", sa.String(160), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("resource_type", sa.String(80), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.String(80), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id", "environment_id"],
            ["application_environments.application_id", "application_environments.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_context_created",
        "audit_events",
        ["application_id", "environment_id", "created_at"],
    )
    op.create_index("ix_audit_events_application_id", "audit_events", ["application_id"])
    op.create_index("ix_audit_events_environment_id", "audit_events", ["environment_id"])


def downgrade() -> None:
    for table in (
        "audit_events",
        "user_feedback",
        "evidence_records",
        "request_traces",
        "answer_profiles",
        "retrieval_profiles",
        "ingestion_runs",
        "document_chunks",
        "document_revisions",
        "documents",
        "sources",
        "knowledge_collections",
        "application_api_keys",
        "application_environments",
        "applications",
    ):
        op.drop_table(table)
