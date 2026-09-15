"""为入库 Worker 增加持久化尝试历史。"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003_ingestion_attempts"
down_revision = "0002_transactional_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """保存每次实际执行的阶段、结果和错误，支持后续恢复审计。"""
    op.create_table(
        "ingestion_attempts",
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
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("stage", sa.String(24), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["run_id"], ["ingestion_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "attempt_number", name="uq_ingestion_attempt_number"),
    )
    op.create_index("ix_ingestion_attempts_run_id", "ingestion_attempts", ["run_id"])
    op.create_index(
        "ix_ingestion_attempts_run_created", "ingestion_attempts", ["run_id", "created_at"]
    )


def downgrade() -> None:
    """删除仅由本迁移创建的尝试历史表及索引。"""
    op.drop_index("ix_ingestion_attempts_run_created", table_name="ingestion_attempts")
    op.drop_index("ix_ingestion_attempts_run_id", table_name="ingestion_attempts")
    op.drop_table("ingestion_attempts")
