"""为模型目录、绑定和平台默认值建立结构化非密钥存储。"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_model_registry"
down_revision = "0004_embedding_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """只新增结构，不读取或删除旧 JSON，切换权威来源前仍可安全回退。"""
    op.create_table(
        "model_catalog_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("capability", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(160), nullable=False, server_default=""),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("api_key_env", sa.String(120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("note", sa.Text()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_model_catalog_records_code"),
    )
    op.create_index(
        "ix_model_catalog_records_capability", "model_catalog_records", ["capability"]
    )
    op.create_table(
        "application_model_binding_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid()),
        sa.Column("catalog_entry_id", sa.Uuid(), nullable=False),
        sa.Column("capability", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["application_id", "environment_id"],
            ["application_environments.application_id", "application_environments.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["catalog_entry_id"], ["model_catalog_records.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_application_model_binding_records_application_id",
        "application_model_binding_records",
        ["application_id"],
    )
    op.create_index(
        "ix_application_model_binding_records_environment_id",
        "application_model_binding_records",
        ["environment_id"],
    )
    op.create_index(
        "ix_application_model_binding_records_catalog_entry_id",
        "application_model_binding_records",
        ["catalog_entry_id"],
    )
    op.create_index(
        "uq_model_binding_application_capability",
        "application_model_binding_records",
        ["application_id", "capability"],
        unique=True,
        postgresql_where=sa.text("environment_id IS NULL"),
    )
    op.create_index(
        "uq_model_binding_environment_capability",
        "application_model_binding_records",
        ["environment_id", "capability"],
        unique=True,
        postgresql_where=sa.text("environment_id IS NOT NULL"),
    )
    op.create_table(
        "platform_model_default_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("capability", sa.String(32), nullable=False),
        sa.Column("catalog_entry_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(
            ["catalog_entry_id"], ["model_catalog_records.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("capability", name="uq_platform_model_default_records_capability"),
    )


def downgrade() -> None:
    """仅移除本迁移新建的空白结构，不触碰旧 JSON 或业务知识数据。"""
    op.drop_table("platform_model_default_records")
    op.drop_index(
        "uq_model_binding_environment_capability",
        table_name="application_model_binding_records",
    )
    op.drop_index(
        "uq_model_binding_application_capability",
        table_name="application_model_binding_records",
    )
    op.drop_index(
        "ix_application_model_binding_records_catalog_entry_id",
        table_name="application_model_binding_records",
    )
    op.drop_index(
        "ix_application_model_binding_records_environment_id",
        table_name="application_model_binding_records",
    )
    op.drop_index(
        "ix_application_model_binding_records_application_id",
        table_name="application_model_binding_records",
    )
    op.drop_table("application_model_binding_records")
    op.drop_index("ix_model_catalog_records_capability", table_name="model_catalog_records")
    op.drop_table("model_catalog_records")
