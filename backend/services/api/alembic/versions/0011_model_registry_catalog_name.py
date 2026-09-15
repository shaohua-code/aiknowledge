"""补齐模型目录显示名称，修复迁移与 ORM/回填工具的结构不一致。"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011_model_registry_catalog_name"
down_revision = "0010_request_trace_phase_timing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为已有目录记录安全补名称；历史未知名称回退为 code，不读取任何密钥。"""
    # 先允许空值，确保升级包含任意既有目录记录时不会因非空约束中断。
    op.add_column("model_catalog_records", sa.Column("name", sa.String(120), nullable=True))
    # code 已受唯一约束且是不可变识别符，可作为历史记录的保守显示名称。
    op.execute("UPDATE model_catalog_records SET name = code WHERE name IS NULL")
    op.alter_column("model_catalog_records", "name", nullable=False)


def downgrade() -> None:
    """回退仅删除本迁移新增的显示字段，不影响目录引用、绑定或凭证环境变量。"""
    op.drop_column("model_catalog_records", "name")
