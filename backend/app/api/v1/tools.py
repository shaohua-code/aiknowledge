"""工具管理 API（Task 13.4）。

路由设计
--------
1. ``GET /api/v1/tools``
   - 列出所有工具定义（全局表），由管理密钥保护
2. ``GET /api/v1/project-tools``
   - 列出当前项目白名单，需项目 API Key + ``scopes:read``（此处复用 knowledge:write）
3. ``POST /api/v1/project-tools``
   - 添加工具到项目白名单（需 knowledge:write）
4. ``PATCH /api/v1/project-tools/{toolCode}``
   - 更新配置（config / enabled）
5. ``DELETE /api/v1/project-tools/{toolCode}``
   - 从白名单移除
6. ``POST /api/v1/project-tools/{toolCode}/test``
   - 测试工具调用（传入 inputs，返回 ToolExecutionResult）

设计要点
--------
1. 全局工具列表（GET /tools）由管理密钥保护，不关联项目
2. 项目白名单接口（/project-tools*）由项目 API Key + Scope 校验保护
3. 写操作（POST/PATCH/DELETE/test）统一要求 ``knowledge:write`` Scope
   说明：本任务范围内复用 knowledge:write，后续可定义 SCOPE_TOOLS_WRITE
4. 添加白名单时校验 tool_code 在 ToolDefinition 中存在，且 applicable_projects 允许
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Path
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_management_api_key, require_scopes
from app.core.exceptions import ToolNotAllowedError, ValidationError
from app.core.project_context import ProjectContext
from app.core.response import ApiResponse, build_meta
from app.core.scopes import SCOPE_KNOWLEDGE_WRITE
from app.db.repositories.tool import (
    ProjectToolRepository,
    ToolDefinitionRepository,
)
from app.db.session import get_db
from app.modules.tools.executor import ToolExecutor

# 全局工具管理路由：管理密钥保护
tools_router = APIRouter(prefix="/tools", tags=["工具管理"])

# 项目工具白名单路由：项目 API Key + Scope 校验
project_tools_router = APIRouter(prefix="/project-tools", tags=["项目工具白名单"])


# ============================================================================
# 请求/响应模型
# ============================================================================
class ProjectToolCreateRequest(BaseModel):
    """添加工具到项目白名单请求体。

    Attributes:
        toolCode: 工具编码（必须存在于 ToolDefinition 表）。
        config: 项目级配置，可空。可包含 API 端点、参数默认值等。
        enabled: 是否启用，默认 True。
    """

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "toolCode": "fund_market",
            "config": {"api_endpoint": "https://api.example.com/fund"},
            "enabled": True,
        }
    })

    toolCode: str = Field(..., min_length=1, max_length=64, description="工具编码")
    config: dict[str, Any] | None = Field(default=None, description="项目级配置")
    enabled: bool = Field(default=True, description="是否启用")


class ProjectToolUpdateRequest(BaseModel):
    """更新项目工具配置请求体。

    用于 ``PATCH /api/v1/project-tools/{toolCode}``。
    所有字段可选，PATCH 语义仅更新传入字段。

    Attributes:
        config: 项目级配置，可空。
        enabled: 是否启用，可空。
    """

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "config": {"api_endpoint": "https://api.example.com/v2/fund"},
            "enabled": False,
        }
    })

    config: dict[str, Any] | None = Field(default=None, description="项目级配置")
    enabled: bool | None = Field(default=None, description="是否启用")


class ToolTestRequest(BaseModel):
    """测试工具调用请求体。

    用于 ``POST /api/v1/project-tools/{toolCode}/test``。
    传入 inputs 调用工具，返回 ToolExecutionResult。

    Attributes:
        inputs: 工具入参 dict，需符合 ToolDefinition.input_schema。
    """

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "inputs": {"fund_codes": ["000001", "000002"]},
        }
    })

    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="工具入参，需符合 input_schema",
    )


# ============================================================================
# 全局工具列表接口（管理密钥保护）
# ============================================================================
@tools_router.get(
    "",
    summary="列出所有工具定义",
    description="列出平台全部工具定义（全局表），仅管理密钥可调用。",
    response_model=None,
)
async def list_tool_definitions(
    _: str = Depends(get_management_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """列出所有工具定义（全局）。

    Returns:
        标准响应体，``data.items`` 为工具定义列表。
    """
    repo = ToolDefinitionRepository(db)
    tools = await repo.list_all()

    items = [
        {
            "id": t.id,
            "code": t.code,
            "name": t.name,
            "description": t.description,
            "inputSchema": t.input_schema,
            "outputSchema": t.output_schema,
            "timeoutSeconds": t.timeout_seconds,
            "applicableProjects": t.applicable_projects or [],
            "failureCodes": t.failure_codes or {},
            "degradation": t.degradation,
            "createdAt": t.created_at.isoformat() if t.created_at else None,
        }
        for t in tools
    ]
    return ApiResponse.success({"items": items}, build_meta(None))


# ============================================================================
# 项目工具白名单接口（项目 API Key + Scope 校验）
# ============================================================================
@project_tools_router.get(
    "",
    summary="列出当前项目工具白名单",
    description="列出当前项目启用的工具列表，需 knowledge:write Scope。",
    response_model=None,
)
async def list_project_tools(
    ctx: ProjectContext = Depends(require_scopes(SCOPE_KNOWLEDGE_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """列出当前项目工具白名单。

    Returns:
        标准响应体，``data.items`` 为项目工具配置列表。
    """
    repo = ProjectToolRepository(db)
    project_tools = await repo.list_by_project(ctx)

    items = [
        {
            "id": pt.id,
            "toolCode": pt.tool_code,
            "config": pt.config or {},
            "enabled": pt.enabled,
            "createdAt": pt.created_at.isoformat() if pt.created_at else None,
            "updatedAt": pt.updated_at.isoformat() if pt.updated_at else None,
        }
        for pt in project_tools
    ]
    return ApiResponse.success({"items": items}, build_meta(ctx.project_code))


@project_tools_router.post(
    "",
    summary="添加工具到项目白名单",
    description="为当前项目启用一个工具，需 knowledge:write Scope。",
    response_model=None,
)
async def create_project_tool(
    payload: ProjectToolCreateRequest = Body(...),
    ctx: ProjectContext = Depends(require_scopes(SCOPE_KNOWLEDGE_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """添加工具到项目白名单。

    业务流程：
        1. 校验 tool_code 在 ToolDefinition 中存在
        2. 校验 ToolDefinition.applicable_projects 包含当前项目 code
        3. 创建 ProjectTool 记录（project_id 由 ctx 注入）
        4. UNIQUE 冲突时返回 ValidationError（同项目同 tool_code 重复）

    Args:
        payload: 请求体，含 toolCode / config / enabled。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 为新建项目工具配置。

    Raises:
        ValidationError: tool_code 不存在或重复添加。
        ToolNotAllowedError: 工具不适用于当前项目。
    """
    # 校验工具定义存在
    tool_def_repo = ToolDefinitionRepository(db)
    tool_def = await tool_def_repo.get_by_code(payload.toolCode)
    if tool_def is None:
        raise ValidationError(
            f"工具定义不存在：{payload.toolCode}",
            details={"field": "toolCode", "value": payload.toolCode},
        )

    # 校验工具适用于当前项目
    if (
        tool_def.applicable_projects
        and ctx.project_code not in tool_def.applicable_projects
    ):
        raise ToolNotAllowedError(
            f"工具 {payload.toolCode} 不适用于项目 {ctx.project_code}",
            details={
                "toolCode": payload.toolCode,
                "projectCode": ctx.project_code,
                "applicableProjects": tool_def.applicable_projects,
            },
        )

    # 创建白名单记录
    repo = ProjectToolRepository(db)
    try:
        project_tool = await repo.create(
            ctx=ctx,
            tool_code=payload.toolCode,
            config=payload.config,
            enabled=payload.enabled,
        )
        await db.commit()
    except IntegrityError as exc:
        # 同项目同 tool_code 重复（UNIQUE 约束）
        await db.rollback()
        raise ValidationError(
            f"工具 {payload.toolCode} 已在项目 {ctx.project_code} 白名单中",
            details={"field": "toolCode", "value": payload.toolCode},
        ) from exc

    data = {
        "id": project_tool.id,
        "toolCode": project_tool.tool_code,
        "config": project_tool.config or {},
        "enabled": project_tool.enabled,
        "createdAt": project_tool.created_at.isoformat() if project_tool.created_at else None,
    }
    return ApiResponse.success(data, build_meta(ctx.project_code))


@project_tools_router.patch(
    "/{toolCode}",
    summary="更新项目工具配置",
    description="更新 config 或 enabled，需 knowledge:write Scope。",
    response_model=None,
)
async def update_project_tool(
    toolCode: str = Path(..., description="工具编码"),
    payload: ProjectToolUpdateRequest = Body(...),
    ctx: ProjectContext = Depends(require_scopes(SCOPE_KNOWLEDGE_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """更新项目工具配置（PATCH 语义）。

    Args:
        toolCode: 路径参数，工具编码。
        payload: 请求体，含 config / enabled。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 为更新后的项目工具配置。

    Raises:
        ValidationError: 工具不在白名单中。
    """
    repo = ProjectToolRepository(db)
    # 查询当前项目的工具配置
    project_tool = await repo.get_by_code(ctx, toolCode)
    if project_tool is None:
        raise ValidationError(
            f"工具 {toolCode} 不在项目 {ctx.project_code} 白名单中",
            details={"field": "toolCode", "value": toolCode},
        )

    # 收集待更新字段
    fields: dict[str, Any] = {}
    if payload.config is not None:
        fields["config"] = payload.config
    if payload.enabled is not None:
        fields["enabled"] = payload.enabled

    if fields:
        updated = await repo.update(ctx, project_tool.id, **fields)
        await db.commit()
        project_tool = updated or project_tool

    data = {
        "id": project_tool.id,
        "toolCode": project_tool.tool_code,
        "config": project_tool.config or {},
        "enabled": project_tool.enabled,
        "updatedAt": project_tool.updated_at.isoformat() if project_tool.updated_at else None,
    }
    return ApiResponse.success(data, build_meta(ctx.project_code))


@project_tools_router.delete(
    "/{toolCode}",
    summary="从白名单移除工具",
    description="从当前项目白名单移除工具，需 knowledge:write Scope。",
    response_model=None,
)
async def delete_project_tool(
    toolCode: str = Path(..., description="工具编码"),
    ctx: ProjectContext = Depends(require_scopes(SCOPE_KNOWLEDGE_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """从白名单移除工具。

    Args:
        toolCode: 路径参数，工具编码。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 含 deleted 字段。

    Raises:
        ValidationError: 工具不在白名单中。
    """
    repo = ProjectToolRepository(db)
    project_tool = await repo.get_by_code(ctx, toolCode)
    if project_tool is None:
        raise ValidationError(
            f"工具 {toolCode} 不在项目 {ctx.project_code} 白名单中",
            details={"field": "toolCode", "value": toolCode},
        )

    deleted = await repo.delete(ctx, project_tool.id)
    await db.commit()

    data = {
        "deleted": deleted,
        "toolCode": toolCode,
    }
    return ApiResponse.success(data, build_meta(ctx.project_code))


@project_tools_router.post(
    "/{toolCode}/test",
    summary="测试工具调用",
    description="传入 inputs 调用工具，返回 ToolExecutionResult，需 knowledge:write Scope。",
    response_model=None,
)
async def test_project_tool(
    toolCode: str = Path(..., description="工具编码"),
    payload: ToolTestRequest = Body(...),
    ctx: ProjectContext = Depends(require_scopes(SCOPE_KNOWLEDGE_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """测试工具调用。

    业务流程：
        1. 通过 ToolExecutor 执行工具
        2. Executor 内部校验白名单、超时、降级
        3. 返回 ToolExecutionResult（包含 success / data / error_code 等）

    注意：
        - 超时与失败时 Executor 会抛 ExternalSourceTimeoutError /
          ExternalSourceFailedError，由全局异常处理器转换为标准错误响应。
        - 如果希望测试接口返回 degraded 结果而非抛异常，可在 Executor 中
          实现"测试模式"，本任务范围内按标准异常处理。

    Args:
        toolCode: 路径参数，工具编码。
        payload: 请求体，含 inputs。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 为 ToolExecutionResult 序列化结果。
    """
    executor = ToolExecutor(db)
    # 执行工具调用（Executor 内部校验白名单、超时等）
    result = await executor.execute(ctx, toolCode, payload.inputs)

    data = {
        "success": result.success,
        "data": result.data,
        "errorCode": result.error_code,
        "errorMessage": result.error_message,
        "degraded": result.degraded,
        "degradedReason": result.degraded_reason,
    }
    return ApiResponse.success(data, build_meta(ctx.project_code))
