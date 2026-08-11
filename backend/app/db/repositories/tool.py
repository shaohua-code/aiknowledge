"""工具 Repository：ToolDefinition（全局表） / ProjectTool（项目级）。

对应 SubTask 6.1：全局工具定义与项目级工具配置。

设计要点
--------
1. ``ToolDefinitionRepository`` 操作全局表 ``tool_definitions``，不强制 project_id
   过滤（全局表无 project_id 列）。
2. ``ProjectToolRepository`` 操作 ``project_tools``，强制 project_id 过滤，
   每个项目可独立配置工具的启用状态与 config。
3. ``ProjectTool.tool_code`` 关联 ``ToolDefinition.code``（逻辑外键，非物理外键），
   Service 层在创建时应先校验 ``ToolDefinition`` 存在且 ``applicable_projects``
   允许当前项目使用。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.db.models.tool import ProjectTool, ToolDefinition
from app.db.repositories.base import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.project_context import ProjectContext


# ---------------------------------------------------------------------------
# ToolDefinitionRepository：全局表，不强制 project_id 过滤
# ---------------------------------------------------------------------------
class ToolDefinitionRepository:
    """工具定义 Repository：操作全局表 ``tool_definitions``。

    不继承 ``BaseRepository``，因为 ``ToolDefinition`` 是全局表，无 project_id 列。
    所有方法不接收 ``ProjectContext``，由 Service 层直接调用。
    """

    def __init__(self, session: "AsyncSession") -> None:
        """初始化 Repository。

        Args:
            session: 异步数据库会话。
        """
        self.session = session

    async def create(
        self,
        code: str,
        name: str,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        timeout_seconds: int = 4,
        applicable_projects: list[str] | None = None,
        failure_codes: dict[str, Any] | None = None,
        degradation: str | None = None,
    ) -> ToolDefinition:
        """创建工具定义。

        Args:
            code: 工具编码，全局唯一（如 ``fund_market``）。
            name: 工具显示名。
            description: 工具描述，供大模型决定是否调用。
            input_schema: 入参 JSON Schema。
            output_schema: 出参 JSON Schema。
            timeout_seconds: 调用超时秒数，默认 4s（PRD 链路限制）。
            applicable_projects: 适用项目 code 列表，None 表示全部项目可用。
            failure_codes: 失败码定义（JSONB）。
            degradation: 降级策略描述。

        Returns:
            创建后的 ToolDefinition 实例。
        """
        tool_def = ToolDefinition(
            code=code,
            name=name,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            timeout_seconds=timeout_seconds,
            applicable_projects=applicable_projects,
            failure_codes=failure_codes,
            degradation=degradation,
        )
        self.session.add(tool_def)
        await self.session.flush()
        return tool_def

    async def get_by_code(self, code: str) -> ToolDefinition | None:
        """按 code 查询工具定义（全局唯一）。

        Args:
            code: 工具编码。

        Returns:
            ToolDefinition 实例；不存在返回 None。
        """
        # 全局查询：仅按 code 过滤
        stmt = select(ToolDefinition).where(ToolDefinition.code == code)
        return await self.session.scalar(stmt)

    async def list_all(self) -> list[ToolDefinition]:
        """列出所有工具定义，按创建时间倒序。

        Returns:
            ToolDefinition 列表。
        """
        stmt = select(ToolDefinition).order_by(ToolDefinition.created_at.desc())
        return list((await self.session.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# ProjectToolRepository：项目级配置，强制 project_id 过滤
# ---------------------------------------------------------------------------
class ProjectToolRepository(BaseRepository):
    """项目工具配置 Repository：操作 ``project_tools`` 表。

    每个项目可独立配置工具的启用状态与 config。强制带 project_id 过滤。
    """

    model = ProjectTool

    async def create(
        self,
        ctx: "ProjectContext",
        tool_code: str,
        config: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> ProjectTool:
        """为当前项目启用一个工具。

        Args:
            ctx: 项目上下文。
            tool_code: 工具编码（关联 ToolDefinition.code）。
            config: 项目特定的工具配置（JSONB）。
            enabled: 是否启用。

        Returns:
            创建后的 ProjectTool 实例。
        """
        # project_id 由 ctx 注入
        project_tool = ProjectTool(
            project_id=ctx.project_id,
            tool_code=tool_code,
            config=config,
            enabled=enabled,
        )
        self.session.add(project_tool)
        await self.session.flush()
        return project_tool

    async def list_by_project(self, ctx: "ProjectContext") -> list[ProjectTool]:
        """列出当前项目启用的所有工具配置。

        Args:
            ctx: 项目上下文。

        Returns:
            ProjectTool 列表，按创建时间倒序。
        """
        # 强制 project_id 过滤
        stmt = (
            select(ProjectTool)
            .where(ProjectTool.project_id == ctx.project_id)
            .order_by(ProjectTool.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_by_code(
        self,
        ctx: "ProjectContext",
        tool_code: str,
    ) -> ProjectTool | None:
        """按 tool_code 查询当前项目的工具配置。

        Args:
            ctx: 项目上下文。
            tool_code: 工具编码。

        Returns:
            ProjectTool 实例；不存在或属于其他项目返回 None。
        """
        # 双重过滤：project_id + tool_code
        stmt = select(ProjectTool).where(
            ProjectTool.project_id == ctx.project_id,
            ProjectTool.tool_code == tool_code,
        )
        return await self.session.scalar(stmt)

    async def update(
        self,
        ctx: "ProjectContext",
        id: str,
        **fields: Any,
    ) -> ProjectTool | None:
        """按 ID 更新项目工具配置（强制 project_id 过滤）。

        Args:
            ctx: 项目上下文。
            id: ProjectTool 主键。
            **fields: 待更新字段，如 ``config`` / ``enabled``。

        Returns:
            更新后的 ProjectTool 实例；不存在或属于其他项目返回 None。
        """
        # 复用基类 update
        return await super().update(ctx, id, **fields)

    async def delete(self, ctx: "ProjectContext", id: str) -> bool:
        """删除项目工具配置（取消启用某工具）。

        Args:
            ctx: 项目上下文。
            id: ProjectTool 主键。

        Returns:
            True 表示删除成功；False 表示不存在或属于其他项目。
        """
        # 复用基类 delete
        return await super().delete(ctx, id)
