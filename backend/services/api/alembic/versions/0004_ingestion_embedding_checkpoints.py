"""为可恢复 Embedding 批次保存非秘密检查点。"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# 既有 alembic_version.version_num 为 varchar(32)，revision 标识必须保持在该上限内。
revision = "0004_embedding_checkpoints"
down_revision = "0003_ingestion_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """仅保存已完整返回的向量批次，重试按内容和模型指纹安全复用。"""
    op.create_table(
        "ingestion_embedding_checkpoints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("batch_index", sa.Integer(), nullable=False),
        sa.Column("chunk_start", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("embedding_fingerprint", sa.String(64), nullable=False),
        sa.Column("embeddings", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["ingestion_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "batch_index", name="uq_ingestion_embedding_checkpoint"),
    )
    op.create_index(
        "ix_ingestion_embedding_checkpoints_run_id",
        "ingestion_embedding_checkpoints",
        ["run_id"],
    )
    op.create_index(
        "ix_ingestion_checkpoint_run",
        "ingestion_embedding_checkpoints",
        ["run_id", "batch_index"],
    )


def downgrade() -> None:
    """删除本迁移独立创建的检查点表与索引，不影响入库任务和尝试历史。"""
    op.drop_index("ix_ingestion_checkpoint_run", table_name="ingestion_embedding_checkpoints")
    op.drop_index(
        "ix_ingestion_embedding_checkpoints_run_id", table_name="ingestion_embedding_checkpoints"
    )
    op.drop_table("ingestion_embedding_checkpoints")
