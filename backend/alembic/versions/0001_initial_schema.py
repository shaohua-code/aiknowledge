"""initial schema: 创建全部 23 张表与索引

Revision ID: 0001
Revises:
Create Date: 2025-01-01 00:00:00

对应 SubTask 4.5：手动编写首个迁移，混合使用 op.create_table() 与 op.execute()。

设计要点
--------
1. 由于 pgvector 的 Vector 类型、TSVECTOR 的 GIN 索引、HNSW 向量索引
   需要原生 PostgreSQL 语法，无法通过 op.create_table() 标准 API 表达，
   故在 op.create_table() 创建表后，用 op.execute() 补充 raw SQL。
2. 表创建顺序遵循外键依赖：projects → 业务表 → 子表。
3. 向下迁移按反向顺序撤销所有对象。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from app.core.config import settings

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """向上迁移：创建全部表、约束、索引。"""

    # ------------------------------------------------------------------
    # 1. projects：项目主表
    # ------------------------------------------------------------------
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("code", postgresql.CITEXT(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), server_default=sa.text("'active'"), nullable=False),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_projects_code"),
        comment="项目主表",
    )

    # ------------------------------------------------------------------
    # 2. api_keys：项目 API Key
    # ------------------------------------------------------------------
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("key_prefix", sa.String(24), nullable=False),
        sa.Column("key_hash", sa.String(128), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), server_default=sa.text("'active'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_api_keys_project"),
        comment="项目 API Key 表",
    )
    op.create_index("ix_api_keys_project_id", "api_keys", ["project_id"])

    # ------------------------------------------------------------------
    # 3. project_settings：项目设置
    # ------------------------------------------------------------------
    op.create_table(
        "project_settings",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("chat_model", sa.String(120), nullable=True),
        sa.Column("embedding_model", sa.String(120), nullable=True),
        sa.Column("web_search_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("allowed_domains", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("blocked_domains", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("max_evidence", sa.Integer(), server_default=sa.text("8"), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), server_default=sa.text("15"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_project_settings_project"),
        comment="项目设置表",
    )
    op.create_index("ix_project_settings_project_id", "project_settings", ["project_id"])

    # ------------------------------------------------------------------
    # 4. knowledge_bases：知识库（含复合唯一约束，供 documents 复合外键引用）
    # ------------------------------------------------------------------
    op.create_table(
        "knowledge_bases",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("code", postgresql.CITEXT(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("embedding_model", sa.String(120), nullable=True),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), server_default=sa.text("'active'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_knowledge_bases_project"),
        sa.UniqueConstraint("project_id", "id", name="uq_knowledge_bases_project_id_id"),
        sa.UniqueConstraint("project_id", "code", name="uq_knowledge_bases_project_code"),
        comment="知识库表",
    )
    op.create_index("ix_knowledge_bases_project_id", "knowledge_bases", ["project_id"])

    # ------------------------------------------------------------------
    # 5. documents：文档（含复合外键 + 复合唯一约束）
    # ------------------------------------------------------------------
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("mime_type", sa.String(120), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("processing_status", sa.String(32), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("external_id", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # 复合外键：强制文档与知识库同项目
        sa.ForeignKeyConstraint(
            ["project_id", "knowledge_base_id"],
            ["knowledge_bases.project_id", "knowledge_bases.id"],
            name="fk_documents_project_knowledge_base",
        ),
        # 复合唯一约束：供 document_chunks 复合外键引用
        sa.UniqueConstraint("project_id", "id", name="uq_documents_project_id_id"),
        comment="文档表",
    )
    op.create_index("ix_documents_project_id", "documents", ["project_id"])
    op.create_index(
        "idx_documents_project_status",
        "documents",
        ["project_id", "processing_status", "created_at"],
    )

    # ------------------------------------------------------------------
    # 6. document_chunks：分块（含 pgvector 向量列 + TSVECTOR 全文列）
    # ------------------------------------------------------------------
    op.create_table(
        "document_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        # TSVECTOR 全文索引列
        sa.Column("content_tsv", postgresql.TSVECTOR(), nullable=True),
        # pgvector 向量列，维度由 settings.embedding_dimension 决定
        sa.Column("embedding", Vector(settings.embedding_dimension), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # 复合外键：强制 chunk 与文档同项目，文档删除时级联
        sa.ForeignKeyConstraint(
            ["project_id", "document_id"],
            ["documents.project_id", "documents.id"],
            ondelete="CASCADE",
            name="fk_chunks_project_document",
        ),
        comment="文档分块表",
    )
    op.create_index("ix_document_chunks_project_id", "document_chunks", ["project_id"])
    # 部分索引：仅对 enabled=true 的 chunk 建索引，加速检索过滤
    op.create_index(
        "idx_chunks_project_kb",
        "document_chunks",
        ["project_id", "knowledge_base_id"],
        postgresql_where=sa.text("enabled = true"),
    )
    # GIN 全文索引：加速 TSVECTOR 关键词检索
    op.execute(
        "CREATE INDEX idx_chunks_content_tsv ON document_chunks USING gin(content_tsv)"
    )
    # HNSW 向量索引：加速 pgvector 余弦相似度检索
    op.execute(
        "CREATE INDEX idx_chunks_embedding_hnsw ON document_chunks "
        "USING hnsw(embedding vector_cosine_ops)"
    )
    # 自动维护 content_tsv 列：插入/更新时由 to_tsvector 生成
    op.execute(
        "CREATE TRIGGER trg_chunks_content_tsv BEFORE INSERT OR UPDATE "
        "ON document_chunks FOR EACH ROW EXECUTE FUNCTION "
        "tsvector_update_trigger(content_tsv, 'pg_catalog.simple', content)"
    )

    # ------------------------------------------------------------------
    # 7. prompt_versions：提示词版本
    # ------------------------------------------------------------------
    op.create_table(
        "prompt_versions",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("evidence_rules", sa.Text(), nullable=True),
        sa.Column("output_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("prohibitions", sa.Text(), nullable=True),
        sa.Column("risk_template", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_prompt_versions_project"),
        comment="提示词版本表",
    )
    op.create_index("ix_prompt_versions_project_id", "prompt_versions", ["project_id"])
    # 部分唯一索引：每项目仅一个 is_active=true
    op.execute(
        "CREATE UNIQUE INDEX uq_prompt_active_per_project "
        "ON prompt_versions(project_id) WHERE is_active = true"
    )

    # ------------------------------------------------------------------
    # 8. tool_definitions：全局工具定义（不含 project_id）
    # ------------------------------------------------------------------
    op.create_table(
        "tool_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("input_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("output_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), server_default=sa.text("4"), nullable=False),
        sa.Column("applicable_projects", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("failure_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("degradation", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_tool_definitions_code"),
        comment="工具定义表",
    )

    # ------------------------------------------------------------------
    # 9. project_tools：项目工具配置
    # ------------------------------------------------------------------
    op.create_table(
        "project_tools",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("tool_code", sa.String(64), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_project_tools_project"),
        comment="项目工具配置表",
    )
    op.create_index("ix_project_tools_project_id", "project_tools", ["project_id"])

    # ------------------------------------------------------------------
    # 10. ingestion_jobs：入库任务
    # ------------------------------------------------------------------
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("status", sa.String(32), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("stage", sa.String(32), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_ingestion_jobs_project"),
        comment="入库任务表",
    )
    op.create_index("ix_ingestion_jobs_project_id", "ingestion_jobs", ["project_id"])

    # ------------------------------------------------------------------
    # 11. research_tasks：研究任务
    # ------------------------------------------------------------------
    op.create_table(
        "research_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("output_type", sa.String(64), nullable=False),
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column("input_context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("knowledge_base_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=False)), nullable=True),
        sa.Column("requested_tools", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("use_web", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("prompt_version_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("degraded", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("degraded_reasons", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", name="uq_research_tasks_request_id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_research_tasks_project"),
        comment="研究任务表",
    )
    op.create_index("ix_research_tasks_project_id", "research_tasks", ["project_id"])
    op.create_index(
        "idx_research_tasks_project_created",
        "research_tasks",
        ["project_id", "created_at"],
    )

    # ------------------------------------------------------------------
    # 12. research_evidence：研究证据
    # ------------------------------------------------------------------
    op.create_table(
        "research_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("research_task_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("evidence_type", sa.String(32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_research_evidence_project"),
        comment="研究证据表",
    )
    op.create_index("ix_research_evidence_project_id", "research_evidence", ["project_id"])

    # ------------------------------------------------------------------
    # 13. research_results：研究结果
    # ------------------------------------------------------------------
    op.create_table(
        "research_results",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("research_task_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("conclusions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("suggested_actions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("uncertainties", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("risk_notice", sa.Text(), nullable=True),
        sa.Column("timing", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_research_results_project"),
        comment="研究结果表",
    )
    op.create_index("ix_research_results_project_id", "research_results", ["project_id"])

    # ------------------------------------------------------------------
    # 14. retrieval_logs：检索日志
    # ------------------------------------------------------------------
    op.create_table(
        "retrieval_logs",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("knowledge_base_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=False)), nullable=True),
        sa.Column("hit_count", sa.Integer(), nullable=True),
        sa.Column("timing_ms", sa.Integer(), nullable=True),
        sa.Column("scores", postgresql.ARRAY(sa.Float()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_retrieval_logs_project"),
        comment="检索日志表",
    )
    op.create_index("ix_retrieval_logs_project_id", "retrieval_logs", ["project_id"])

    # ------------------------------------------------------------------
    # 15. schedules：定时任务
    # ------------------------------------------------------------------
    op.create_table(
        "schedules",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("task_type", sa.String(32), nullable=False),
        sa.Column("cron_expression", sa.String(64), nullable=False),
        sa.Column("timezone", sa.String(64), server_default=sa.text("'Asia/Shanghai'"), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("concurrency_policy", sa.String(16), server_default=sa.text("'skip'"), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), server_default=sa.text("300"), nullable=False),
        sa.Column("max_retries", sa.Integer(), server_default=sa.text("2"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_schedules_project"),
        comment="定时任务表",
    )
    op.create_index("ix_schedules_project_id", "schedules", ["project_id"])
    # 部分索引：仅对 enabled=true 的任务建索引，加速 Beat 扫描到期任务
    op.create_index(
        "idx_schedules_due",
        "schedules",
        ["enabled", "next_run_at"],
        postgresql_where=sa.text("enabled = true"),
    )

    # ------------------------------------------------------------------
    # 16. schedule_runs：定时运行记录（含复合唯一约束，保证幂等）
    # ------------------------------------------------------------------
    op.create_table(
        "schedule_runs",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("schedule_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("queue_job_id", sa.String(120), nullable=True),
        sa.Column("result_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_schedule_runs_project"),
        # 复合唯一约束：保证同一任务同一计划时间只执行一次（幂等）
        sa.UniqueConstraint(
            "project_id", "schedule_id", "planned_at",
            name="uq_schedule_runs_project_schedule_planned",
        ),
        comment="定时运行记录表",
    )
    op.create_index("ix_schedule_runs_project_id", "schedule_runs", ["project_id"])

    # ------------------------------------------------------------------
    # 17. crawl_sources：采集源（含复合唯一约束）
    # ------------------------------------------------------------------
    op.create_table(
        "crawl_sources",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("start_urls", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("allowed_domains", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("blocked_paths", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("destination_knowledge_base_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("extract_rules", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("import_policy", sa.String(32), server_default=sa.text("'review_required'"), nullable=False),
        sa.Column("limits", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(32), server_default=sa.text("'active'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_crawl_sources_project"),
        # 复合唯一约束：采集源 code 在项目内唯一
        sa.UniqueConstraint("project_id", "code", name="uq_crawl_sources_project_code"),
        comment="采集源表",
    )
    op.create_index("ix_crawl_sources_project_id", "crawl_sources", ["project_id"])

    # ------------------------------------------------------------------
    # 18. crawl_runs：采集运行记录
    # ------------------------------------------------------------------
    op.create_table(
        "crawl_runs",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("crawl_source_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("status", sa.String(32), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("discovered_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("success_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failed_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("imported_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_crawl_runs_project"),
        comment="采集运行记录表",
    )
    op.create_index("ix_crawl_runs_project_id", "crawl_runs", ["project_id"])

    # ------------------------------------------------------------------
    # 19. crawl_pages：采集页面（含复合唯一约束，URL 去重）
    # ------------------------------------------------------------------
    op.create_table(
        "crawl_pages",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("crawl_source_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("crawl_run_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("canonical_url_hash", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), server_default=sa.text("'discovered'"), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_crawl_pages_project"),
        # 复合唯一约束：同一采集源内 URL 去重
        sa.UniqueConstraint(
            "project_id", "crawl_source_id", "canonical_url_hash",
            name="uq_crawl_page_url",
        ),
        comment="采集页面表",
    )
    op.create_index("ix_crawl_pages_project_id", "crawl_pages", ["project_id"])

    # ------------------------------------------------------------------
    # 20. web_materials：网络待审核资料池
    # ------------------------------------------------------------------
    op.create_table(
        "web_materials",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("crawl_source_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("crawl_page_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_web_materials_project"),
        comment="网络待审核资料池表",
    )
    op.create_index("ix_web_materials_project_id", "web_materials", ["project_id"])

    # ------------------------------------------------------------------
    # 21. source_policies：来源策略
    # ------------------------------------------------------------------
    op.create_table(
        "source_policies",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("policy_type", sa.String(16), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_source_policies_project"),
        comment="来源策略表",
    )
    op.create_index("ix_source_policies_project_id", "source_policies", ["project_id"])

    # ------------------------------------------------------------------
    # 22. feedback：用户反馈
    # ------------------------------------------------------------------
    op.create_table(
        "feedback",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("rating", sa.String(32), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("business_result_id", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_feedback_project"),
        comment="用户反馈表",
    )
    op.create_index("ix_feedback_project_id", "feedback", ["project_id"])

    # ------------------------------------------------------------------
    # 23. usage_logs：使用日志
    # ------------------------------------------------------------------
    op.create_table(
        "usage_logs",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("api_key_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("endpoint", sa.String(120), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("internal_retrieval_ms", sa.Integer(), nullable=True),
        sa.Column("external_parallel_ms", sa.Integer(), nullable=True),
        sa.Column("generation_ms", sa.Integer(), nullable=True),
        sa.Column("total_ms", sa.Integer(), nullable=True),
        sa.Column("evidence_count", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("degraded", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("degraded_reasons", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_usage_logs_project"),
        comment="使用日志表",
    )
    op.create_index("ix_usage_logs_project_id", "usage_logs", ["project_id"])


def downgrade() -> None:
    """向下迁移：按反向顺序撤销所有表。"""

    # 先撤销触发器与特殊索引（避免 drop table 时依赖报错）
    op.execute("DROP TRIGGER IF EXISTS trg_chunks_content_tsv ON document_chunks")
    op.execute("DROP INDEX IF EXISTS idx_chunks_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS idx_chunks_content_tsv")
    op.execute("DROP INDEX IF EXISTS uq_prompt_active_per_project")

    # 按反向顺序撤销所有表
    op.drop_table("usage_logs")
    op.drop_table("feedback")
    op.drop_table("source_policies")
    op.drop_table("web_materials")
    op.drop_table("crawl_pages")
    op.drop_table("crawl_runs")
    op.drop_table("crawl_sources")
    op.drop_table("schedule_runs")
    op.drop_table("schedules")
    op.drop_table("retrieval_logs")
    op.drop_table("research_results")
    op.drop_table("research_evidence")
    op.drop_table("research_tasks")
    op.drop_table("ingestion_jobs")
    op.drop_table("project_tools")
    op.drop_table("tool_definitions")
    op.drop_table("prompt_versions")
    op.drop_table("document_chunks")
    op.drop_table("documents")
    op.drop_table("knowledge_bases")
    op.drop_table("project_settings")
    op.drop_table("api_keys")
    op.drop_table("projects")
