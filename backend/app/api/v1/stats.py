"""平台与项目运行统计接口。

统计只聚合当前调用方有权查看的范围：平台总览使用管理密钥，项目总览从 API Key
解析 ProjectContext 并按 project_id 过滤。页面不再通过 404 容错拼接不一致的数据。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_management_api_key, require_scopes
from app.core.project_context import ProjectContext
from app.core.response import ApiResponse, build_meta
from app.core.scopes import SCOPE_TASKS_READ
from app.db.models.knowledge import Document, KnowledgeBase
from app.db.models.project import Project
from app.db.models.research import ResearchTask
from app.db.session import get_db

router = APIRouter(prefix="/stats", tags=["运行统计"])


def _today_start() -> datetime:
    """返回 UTC 当日零点，确保统计窗口在服务端一致而非依赖浏览器时区。"""
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


@router.get("/overview")
async def get_platform_overview(
    _: str = Depends(get_management_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """汇总平台项目和研究任务状态，仅提供给平台管理控制台。"""
    today_start = _today_start()
    total_projects = await db.scalar(select(func.count()).select_from(Project)) or 0
    active_projects = await db.scalar(
        select(func.count()).select_from(Project).where(Project.status == "active")
    ) or 0
    today_calls = await db.scalar(
        select(func.count())
        .select_from(ResearchTask)
        .where(ResearchTask.created_at >= today_start)
    ) or 0
    error_count = await db.scalar(
        select(func.count())
        .select_from(ResearchTask)
        .where(
            ResearchTask.created_at >= today_start,
            ResearchTask.status.in_(("failed", "timeout")),
        )
    ) or 0
    return ApiResponse.success(
        data={
            "totalProjects": total_projects,
            "activeProjects": active_projects,
            "todayCalls": today_calls,
            "errorCount": error_count,
        },
        meta=build_meta(None),
    )


@router.get("/project")
async def get_project_overview(
    ctx: ProjectContext = Depends(require_scopes(SCOPE_TASKS_READ)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """聚合单一项目的知识资产和研究运行指标，严格限制在当前项目上下文。"""
    today_start = _today_start()
    knowledge_base_count = await db.scalar(
        select(func.count())
        .select_from(KnowledgeBase)
        .where(KnowledgeBase.project_id == ctx.project_id)
    ) or 0
    total_documents = await db.scalar(
        select(func.count())
        .select_from(Document)
        .where(Document.project_id == ctx.project_id)
    ) or 0
    today_calls = await db.scalar(
        select(func.count())
        .select_from(ResearchTask)
        .where(
            ResearchTask.project_id == ctx.project_id,
            ResearchTask.created_at >= today_start,
        )
    ) or 0
    avg_duration = await db.scalar(
        select(func.avg(ResearchTask.total_duration_ms)).where(
            ResearchTask.project_id == ctx.project_id,
            ResearchTask.total_duration_ms.is_not(None),
        )
    )
    return ApiResponse.success(
        data={
            "knowledgeBaseCount": knowledge_base_count,
            "totalDocuments": total_documents,
            "todayCalls": today_calls,
            "avgDurationMs": round(float(avg_duration)) if avg_duration is not None else 0,
        },
        meta=build_meta(ctx.project_code),
    )
