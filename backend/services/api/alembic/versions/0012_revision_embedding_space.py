"""为文档版本保存 Embedding 空间指纹，阻止模型切换后混用向量索引。"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# 版本号保持简短，兼容既有 alembic_version.varchar(32) 约束。
revision = "0012_embedding_space"
down_revision = "0011_model_registry_catalog_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增可为空的指纹列，历史版本无法复原指纹时安全地暂不参与向量召回。"""
    op.add_column(
        "document_revisions",
        sa.Column("embedding_fingerprint", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    """回退本迁移只移除指纹列，不删除文档版本、片段或对象。"""
    op.drop_column("document_revisions", "embedding_fingerprint")
