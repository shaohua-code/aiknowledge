"""为检索和回答策略新增不可变修订与发布事件。"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006_profile_revisions"
down_revision = "0005_model_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增扩展表，不改写旧 active 策略，供后续回填和双读切换使用。"""
    for table, profile_table, constraint in (
        ("retrieval_profile_revisions", "retrieval_profiles", "uq_retrieval_revision"),
        ("answer_profile_revisions", "answer_profiles", "uq_answer_revision"),
    ):
        columns = [
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("application_id", sa.Uuid(), nullable=False),
            sa.Column("environment_id", sa.Uuid(), nullable=False),
            sa.Column("profile_id", sa.Uuid(), nullable=False),
        ]
        if table == "answer_profile_revisions":
            columns.append(sa.Column("retrieval_revision_id", sa.Uuid()))
        columns.extend(
            [
                sa.Column("revision", sa.Integer(), nullable=False),
                sa.Column("state", sa.String(24), nullable=False, server_default="draft"),
                sa.Column("configuration", sa.JSON(), nullable=False),
                sa.Column("published_at", sa.DateTime(timezone=True)),
                sa.ForeignKeyConstraint(
                    ["profile_id"], [f"{profile_table}.id"], ondelete="CASCADE"
                ),
                sa.PrimaryKeyConstraint("id"),
                sa.UniqueConstraint(
                    "application_id", "environment_id", "profile_id", "revision", name=constraint
                ),
            ]
        )
        op.create_table(table, *columns)
        op.create_index(f"ix_{table}_profile_id", table, ["profile_id"])
    op.create_table(
        "profile_releases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("profile_kind", sa.String(24), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(24), nullable=False, server_default="published"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """仅撤销本批新增结构；历史策略主表和既有调用均不受影响。"""
    op.drop_table("profile_releases")
    for table in ("answer_profile_revisions", "retrieval_profile_revisions"):
        op.drop_index(f"ix_{table}_profile_id", table_name=table)
        op.drop_table(table)
