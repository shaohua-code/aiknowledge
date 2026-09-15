"""让运行追踪记录实际使用的策略修订。"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007_trace_profile_revisions"
down_revision = "0006_profile_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """仅增加可空引用列；历史请求不需要被回写或伪造版本信息。"""
    op.add_column("request_traces", sa.Column("answer_profile_revision_id", sa.Uuid()))
    op.add_column("request_traces", sa.Column("retrieval_profile_revision_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_trace_answer_profile_revision",
        "request_traces",
        "answer_profile_revisions",
        ["answer_profile_revision_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_trace_retrieval_profile_revision",
        "request_traces",
        "retrieval_profile_revisions",
        ["retrieval_profile_revision_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """删除本批追踪引用列，不影响策略修订和既有请求字段。"""
    op.drop_constraint("fk_trace_retrieval_profile_revision", "request_traces", type_="foreignkey")
    op.drop_constraint("fk_trace_answer_profile_revision", "request_traces", type_="foreignkey")
    op.drop_column("request_traces", "retrieval_profile_revision_id")
    op.drop_column("request_traces", "answer_profile_revision_id")
