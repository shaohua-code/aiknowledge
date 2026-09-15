"""为评测集的应用环境编码加入数据库级唯一约束。"""

from __future__ import annotations

from alembic import op

revision = "0009_evaluation_set_code_unique"
down_revision = "0008_evaluation_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """阻止并发创建同一环境同编码的评测集，保持样本和运行引用唯一。"""
    op.create_unique_constraint(
        "uq_evaluation_set_context_code",
        "evaluation_sets",
        ["application_id", "environment_id", "code"],
    )


def downgrade() -> None:
    """回滚时仅移除编码唯一性，不删除既有评测集、样本或运行结果。"""
    op.drop_constraint("uq_evaluation_set_context_code", "evaluation_sets", type_="unique")
