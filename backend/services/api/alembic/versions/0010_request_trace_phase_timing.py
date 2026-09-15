"""为运行轨迹增加可空阶段耗时诊断列。"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0010_request_trace_phase_timing"
down_revision = "0009_evaluation_set_code_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增可空列，历史轨迹保留未知而不是以总耗时猜测各阶段。"""
    op.add_column("request_traces", sa.Column("retrieval_ms", sa.Integer()))
    op.add_column("request_traces", sa.Column("model_ms", sa.Integer()))
    op.add_column("request_traces", sa.Column("web_ms", sa.Integer()))


def downgrade() -> None:
    """回滚只移除阶段诊断列，不影响既有总耗时与请求审计。"""
    op.drop_column("request_traces", "web_ms")
    op.drop_column("request_traces", "model_ms")
    op.drop_column("request_traces", "retrieval_ms")
