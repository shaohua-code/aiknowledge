"""为可靠任务投递新增事务 Outbox。"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_transactional_outbox"
down_revision = "0001_v2_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """保存待派发消息，使数据库提交与任务投递可以恢复地解耦。"""
    op.create_table(
        "transactional_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("topic", sa.String(120), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outbox_pending", "transactional_outbox", ["published_at", "created_at"])


def downgrade() -> None:
    """仅回滚本迁移创建的消息表与索引。"""
    op.drop_index("ix_outbox_pending", table_name="transactional_outbox")
    op.drop_table("transactional_outbox")
