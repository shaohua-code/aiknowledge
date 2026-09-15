"""建立可追踪的评测集、样本修订与门禁运行基础。"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0008_evaluation_foundation"
down_revision = "0007_trace_profile_revisions"
branch_labels = None
depends_on = None


def _context_columns() -> list[sa.Column]:
    """所有评测资源都带应用环境边界，不能创建跨环境全局样本。"""
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
    ]


def upgrade() -> None:
    """新增评测表，不读取或改变任何现有策略、请求和业务知识。"""
    op.create_table(
        "evaluation_sets",
        *_context_columns(),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(24), server_default="active", nullable=False),
    )
    op.create_table(
        "evaluation_case_revisions",
        *_context_columns(),
        sa.Column("evaluation_set_id", sa.Uuid(), nullable=False),
        sa.Column("case_code", sa.String(80), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("expected_document_ids", sa.JSON(), nullable=False),
        sa.Column("expected_answer_modes", sa.JSON(), nullable=False),
        sa.Column("expected_output_schema", sa.JSON(), nullable=False),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["evaluation_set_id"], ["evaluation_sets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "evaluation_set_id", "case_code", "revision", name="uq_evaluation_case_revision"
        ),
    )
    op.create_index(
        "ix_evaluation_case_revisions_evaluation_set_id",
        "evaluation_case_revisions",
        ["evaluation_set_id"],
    )
    op.create_table(
        "evaluation_runs",
        *_context_columns(),
        sa.Column("evaluation_set_id", sa.Uuid(), nullable=False),
        sa.Column("answer_profile_revision_id", sa.Uuid(), nullable=False),
        sa.Column("retrieval_profile_revision_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(24), server_default="queued", nullable=False),
        sa.Column("gate_passed", sa.Boolean()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["evaluation_set_id"], ["evaluation_sets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["answer_profile_revision_id"], ["answer_profile_revisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["retrieval_profile_revision_id"],
            ["retrieval_profile_revisions.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_evaluation_runs_evaluation_set_id", "evaluation_runs", ["evaluation_set_id"]
    )
    op.create_table(
        "evaluation_case_results",
        *_context_columns(),
        sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
        sa.Column("case_revision_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.ForeignKeyConstraint(["evaluation_run_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["case_revision_id"], ["evaluation_case_revisions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("evaluation_run_id", "case_revision_id", name="uq_evaluation_run_case"),
    )
    op.create_index(
        "ix_evaluation_case_results_evaluation_run_id",
        "evaluation_case_results",
        ["evaluation_run_id"],
    )


def downgrade() -> None:
    """仅删除本批新增评测表；不影响已存在的策略或追踪记录。"""
    op.drop_table("evaluation_case_results")
    op.drop_table("evaluation_runs")
    op.drop_table("evaluation_case_revisions")
    op.drop_table("evaluation_sets")
