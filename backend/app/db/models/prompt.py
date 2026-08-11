"""提示词版本模型：PromptVersion。

对应 SubTask 4.1：研究提示词的多版本管理。

设计要点
--------
1. 每个项目可维护多个提示词版本，但同一时刻仅一个 ``is_active=true``。
2. ``is_active`` 唯一性通过 PostgreSQL 部分唯一索引实现：
   ``CREATE UNIQUE INDEX uq_prompt_active_per_project
      ON prompt_versions(project_id) WHERE is_active = true``
   该索引无法在 SQLAlchemy 模型层直接声明，由 Alembic 迁移创建。
3. ``output_schema`` 存储 JSON Schema，约束大模型输出结构，
   便于下游解析与校验。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, ProjectOwnedMixin, TimestampMixin


class PromptVersion(Base, ProjectOwnedMixin, TimestampMixin):
    """提示词版本表：研究流程的 system prompt 与输出契约。

    表名: prompt_versions

    字段说明
    --------
    version:
        版本号，项目内递增，便于回溯。
    is_active:
        是否当前启用版本。每项目仅一个为 true（由部分唯一索引保证）。
    system_prompt:
        系统提示词，定义大模型角色与行为约束。
    evidence_rules:
        证据使用规则，约束大模型如何引用与裁剪证据。
    output_schema:
        输出 JSON Schema，约束大模型返回结构（结论/建议/不确定性等）。
    prohibitions:
        禁止事项，约束大模型不可输出的内容（如投资建议、绝对结论）。
    risk_template:
        风险提示模板，附加到回答末尾。
    """

    __tablename__ = "prompt_versions"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="提示词版本 ID",
    )
    # 版本号：项目内递增
    version: Mapped[int] = mapped_column(Integer, nullable=False, comment="版本号")
    # 是否当前启用：每项目仅一个为 true（部分唯一索引保证）
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment="是否当前启用版本",
    )
    # 系统提示词：定义大模型角色与行为约束
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, comment="系统提示词")
    # 证据使用规则：约束大模型如何引用与裁剪证据
    evidence_rules: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="证据使用规则"
    )
    # 输出 JSON Schema：约束大模型返回结构
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="输出 JSON Schema"
    )
    # 禁止事项：约束大模型不可输出的内容
    prohibitions: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="禁止事项"
    )
    # 风险提示模板：附加到回答末尾
    risk_template: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="风险提示模板"
    )
