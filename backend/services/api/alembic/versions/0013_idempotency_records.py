"""为异步控制面写请求增加按应用环境隔离的幂等记录。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# 版本号保持短小，兼容既有 alembic_version.varchar(32) 约束。
revision = "0013_idempotency_records"
down_revision = "0012_embedding_space"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建幂等键、请求指纹和结果回放字段，不保存上传原文或敏感凭证。"""
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(length=120), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "application_id",
            "environment_id",
            "operation",
            "idempotency_key",
            name="uq_idempotency_context_operation_key",
        ),
    )
    op.create_index(
        "ix_idempotency_records_application_id",
        "idempotency_records",
        ["application_id"],
    )
    op.create_index(
        "ix_idempotency_records_environment_id",
        "idempotency_records",
        ["environment_id"],
    )
    op.create_index(
        "ix_idempotency_expiry",
        "idempotency_records",
        ["application_id", "environment_id", "expires_at"],
    )


def downgrade() -> None:
    """回退幂等表本身，不触碰已经创建的文档、版本、任务或对象。"""
    op.drop_index("ix_idempotency_expiry", table_name="idempotency_records")
    op.drop_index(
        "ix_idempotency_records_environment_id", table_name="idempotency_records"
    )
    op.drop_index(
        "ix_idempotency_records_application_id", table_name="idempotency_records"
    )
    op.drop_table("idempotency_records")
