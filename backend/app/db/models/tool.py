"""业务工具模型：ToolDefinition / ProjectTool。

对应 SubTask 4.1：全局工具定义与项目级工具配置。

设计要点
--------
1. ``ToolDefinition`` 为全局表（不含 project_id），由平台维护可用工具清单；
   ``applicable_projects`` 限制工具适用项目范围（按项目 code 列表）。
2. ``ProjectTool`` 为项目级配置表，关联项目与工具，并存储项目特定的 ``config``。
3. ``input_schema`` / ``output_schema`` 使用 JSONB，约束工具入参与出参结构，
   便于研究流程编排时进行类型校验与降级。
4. ``failure_codes`` + ``degradation`` 配合实现工具失败时的降级策略。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, ProjectOwnedMixin, TimestampMixin


class ToolDefinition(Base, TimestampMixin):
    """工具定义表：平台级可用工具清单。

    全局表，不含 project_id。``applicable_projects`` 限制工具适用项目范围。

    表名: tool_definitions

    字段说明
    --------
    code:
        工具编码，全局唯一（如 fund_market、resume_score），用于研究流程编排。
    name:
        工具显示名。
    description:
        工具描述，供大模型决定是否调用。
    input_schema / output_schema:
        工具入参/出参 JSON Schema，用于类型校验。
    timeout_seconds:
        工具调用超时，默认 4s（对应 PRD 链路限制）。
    applicable_projects:
        适用项目 code 列表，空表示全部项目可用。
    failure_codes:
        失败码定义（JSONB），用于降级判断。
    degradation:
        降级策略描述，工具失败时按此策略处理。
    """

    __tablename__ = "tool_definitions"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="工具定义 ID",
    )
    # 工具编码：全局唯一，研究流程编排使用
    code: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, comment="工具编码（全局唯一）"
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False, comment="工具显示名")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="工具描述")
    # 入参 JSON Schema
    input_schema: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="入参 JSON Schema"
    )
    # 出参 JSON Schema
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="出参 JSON Schema"
    )
    # 超时秒数：默认 4s（PRD 链路限制）
    timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("4"),
        comment="工具调用超时秒数",
    )
    # 适用项目 code 列表：空表示全部项目可用
    applicable_projects: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True, comment="适用项目 code 列表"
    )
    # 失败码定义：用于降级判断
    failure_codes: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="失败码定义"
    )
    # 降级策略描述
    degradation: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="降级策略描述"
    )


class ProjectTool(Base, ProjectOwnedMixin, TimestampMixin):
    """项目工具配置表：项目启用的工具及其配置。

    表名: project_tools

    字段说明
    --------
    tool_code:
        关联 ToolDefinition.code，标识项目启用的工具。
    config:
        项目特定的工具配置（JSONB），如 API 端点、参数默认值等。
    enabled:
        是否启用，false 时研究流程不调用此工具。
    """

    __tablename__ = "project_tools"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="项目工具配置 ID",
    )
    # 工具编码：关联 ToolDefinition.code
    tool_code: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="工具编码"
    )
    # 项目特定的工具配置
    config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="项目工具配置（JSONB）"
    )
    # 是否启用
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        comment="是否启用",
    )
