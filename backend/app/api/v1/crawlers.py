"""网页采集管理接口（Task 19.1）。

对应 Task 19：/api/v1/crawl-sources、/api/v1/crawl-runs、/api/v1/crawl-pages、
/api/v1/web-materials。

设计要点（务必阅读）
--------------------
1. **跨项目隔离**
   所有查询通过 Repository 强制带 ``project_id`` 过滤，跨项目查询返回 None，
   由端点统一抛 ``TaskNotFoundError``（404，不泄露资源是否存在）。

2. **Scope 校验**
   - 读操作（GET）：``crawl:read``
   - 写操作（POST/PATCH/DELETE/pause/resume/runs/approve/reject）：``crawl:write``

3. **状态流转**
   - CrawlSource.status：active ↔ paused（通过 pause/resume 接口）
   - 仅 paused 状态可删除（避免误删正在调度的采集源）
   - 仅 active 状态可手动触发采集（POST /runs）

4. **审核流程**
   - REVIEW_REQUIRED 策略：采集结果入 WebMaterial（status='pending'）
   - POST /crawl-pages/{pageId}/approve：审核通过，创建 Document + IngestionJob 入库
   - POST /crawl-pages/{pageId}/reject：审核拒绝，WebMaterial.status='rejected'

5. **手动触发采集**
   POST /crawl-sources/{sourceId}/runs 创建 CrawlRun（status='pending'），
   投递 ``run_crawl`` 到 crawler 队列异步执行。
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_scopes
from app.api.v1.schemas import (
    CrawlSourceCreateRequest,
    CrawlSourceUpdateRequest,
)
from app.core.exceptions import (
    CrawlRuleInvalidError,
    TaskNotFoundError,
    ValidationError,
)
from app.core.project_context import ProjectContext
from app.core.response import ApiResponse, build_meta
from app.core.scopes import SCOPE_CRAWL_READ, SCOPE_CRAWL_WRITE
from app.db.repositories.crawler import (
    CrawlPageRepository,
    CrawlRunRepository,
    CrawlSourceRepository,
    WebMaterialRepository,
)
from app.db.repositories.ingestion import IngestionJobRepository
from app.db.repositories.knowledge import DocumentRepository, KnowledgeBaseRepository
from app.db.session import get_db

# 主路由：/api/v1/crawl-sources
router = APIRouter(prefix="/crawl-sources", tags=["爬虫"])

# 运行记录详情路由：/api/v1/crawl-runs/{runId}
# 单独注册到 api_router，不在 /crawl-sources 前缀下
crawl_runs_router = APIRouter(prefix="/crawl-runs", tags=["爬虫"])

# 采集页面审核路由：/api/v1/crawl-pages/{pageId}/approve|reject
crawl_pages_router = APIRouter(prefix="/crawl-pages", tags=["爬虫"])

# 网络待审核资料路由：/api/v1/web-materials
web_materials_router = APIRouter(prefix="/web-materials", tags=["爬虫"])


# ---------------------------------------------------------------------------
# 辅助函数：ORM 实例转响应字典（字段名转 camelCase，状态转大写）
# ---------------------------------------------------------------------------
def _source_to_dict(source) -> dict:
    """将 CrawlSource ORM 实例转为响应字典。

    字段命名转 camelCase（与前端约定一致），``type`` / ``import_policy`` /
    ``status`` 均转大写（对外契约大写形式，对应数据库小写存储）。

    Args:
        source: CrawlSource ORM 实例。

    Returns:
        响应字典，字段命名 camelCase。
    """
    return {
        "id": source.id,
        "code": source.code,
        "name": source.name,
        # type / import_policy / status 数据库存储为小写，对外响应转大写
        "type": source.type.upper(),
        "startUrls": source.start_urls,
        "allowedDomains": source.allowed_domains,
        "blockedPaths": source.blocked_paths,
        "destinationKnowledgeBaseId": source.destination_knowledge_base_id,
        "extractRules": source.extract_rules,
        "importPolicy": source.import_policy.upper(),
        "limits": source.limits,
        "status": source.status.upper(),
        "createdAt": source.created_at.isoformat() if source.created_at else None,
        "updatedAt": source.updated_at.isoformat() if source.updated_at else None,
    }


def _run_to_dict(run) -> dict:
    """将 CrawlRun ORM 实例转为响应字典。

    ``status`` 数据库存储为小写，对外响应转大写。

    Args:
        run: CrawlRun ORM 实例。

    Returns:
        响应字典，字段命名 camelCase。
    """
    return {
        "id": run.id,
        "crawlSourceId": run.crawl_source_id,
        # status 数据库存储为小写，对外响应转大写
        "status": run.status.upper(),
        "discoveredCount": run.discovered_count,
        "successCount": run.success_count,
        "duplicateCount": run.duplicate_count,
        "failedCount": run.failed_count,
        "importedCount": run.imported_count,
        "startedAt": run.started_at.isoformat() if run.started_at else None,
        "completedAt": run.completed_at.isoformat() if run.completed_at else None,
        "errorCode": run.error_code,
        "createdAt": run.created_at.isoformat() if run.created_at else None,
    }


def _page_to_dict(page) -> dict:
    """将 CrawlPage ORM 实例转为响应字典。

    ``status`` 数据库存储为小写，对外响应转大写。

    Args:
        page: CrawlPage ORM 实例。

    Returns:
        响应字典，字段命名 camelCase。
    """
    return {
        "id": page.id,
        "crawlSourceId": page.crawl_source_id,
        "crawlRunId": page.crawl_run_id,
        "url": page.url,
        "canonicalUrl": page.canonical_url,
        "canonicalUrlHash": page.canonical_url_hash,
        "title": page.title,
        "contentHash": page.content_hash,
        "publishedAt": page.published_at.isoformat() if page.published_at else None,
        "fetchedAt": page.fetched_at.isoformat() if page.fetched_at else None,
        "httpStatus": page.http_status,
        # status 数据库存储为小写，对外响应转大写
        "status": page.status.upper(),
        "documentId": page.document_id,
        "errorCode": page.error_code,
        "createdAt": page.created_at.isoformat() if page.created_at else None,
    }


def _material_to_dict(material) -> dict:
    """将 WebMaterial ORM 实例转为响应字典。

    ``status`` 数据库存储为小写，对外响应转大写。

    Args:
        material: WebMaterial ORM 实例。

    Returns:
        响应字典，字段命名 camelCase。
    """
    return {
        "id": material.id,
        "crawlSourceId": material.crawl_source_id,
        "crawlPageId": material.crawl_page_id,
        "title": material.title,
        "content": material.content,
        "sourceUrl": material.source_url,
        # status 数据库存储为小写，对外响应转大写
        "status": material.status.upper(),
        "knowledgeBaseId": material.knowledge_base_id,
        "reviewedAt": material.reviewed_at.isoformat() if material.reviewed_at else None,
        "createdAt": material.created_at.isoformat() if material.created_at else None,
    }


def _validate_url_format(url: str) -> bool:
    """校验 URL 格式是否合法（仅 http/https）。

    Args:
        url: 待校验的 URL 字符串。

    Returns:
        True 表示格式合法；False 表示非法。
    """
    try:
        parsed = urlparse(url)
        # scheme 必须是 http/https，且 netloc 非空
        return parsed.scheme.lower() in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


# ============================================================================
# 采集源 CRUD 接口
# ============================================================================
@router.post("")
async def create_crawl_source(
    payload: CrawlSourceCreateRequest,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_CRAWL_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """创建采集源（``POST /api/v1/crawl-sources``）。

    流程
    ----
    1. 校验 startUrls 格式合法（http/https）
    2. 校验 code 在项目内唯一
    3. 写入 crawl_sources 表，type/import_policy 转小写存储
    4. 返回完整 CrawlSource

    Args:
        payload: 请求体，含 code / name / type / startUrls 等。
        ctx: 项目上下文，由 ``require_scopes(SCOPE_CRAWL_WRITE)`` 注入。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含新建的 CrawlSource 完整信息。

    Raises:
        ValidationError: startUrls 格式非法或 code 已存在。
    """
    # ------------------------------------------------------------------
    # 步骤 1：校验 startUrls 格式
    # ------------------------------------------------------------------
    for url in payload.startUrls:
        if not _validate_url_format(url):
            raise ValidationError(
                f"startUrls 中包含非法 URL: {url}（仅允许 http/https）",
                details={"field": "startUrls", "invalidUrl": url},
            )

    # ------------------------------------------------------------------
    # 步骤 2：校验 code 项目内唯一
    # ------------------------------------------------------------------
    repo = CrawlSourceRepository(db)
    existing = await repo.get_by_code(ctx, payload.code)
    if existing is not None:
        raise ValidationError(
            f"采集源 code {payload.code} 在项目内已存在",
            details={"field": "code", "code": payload.code},
        )

    # ------------------------------------------------------------------
    # 步骤 3：写入 crawl_sources 表
    # ------------------------------------------------------------------
    # type / import_policy 转小写存储（数据库约定小写，对外响应时转大写）
    source = await repo.create(
        ctx,
        code=payload.code,
        name=payload.name,
        type=payload.type.lower(),
        start_urls=payload.startUrls,
        allowed_domains=payload.allowedDomains,
        blocked_paths=payload.blockedPaths,
        destination_knowledge_base_id=payload.destinationKnowledgeBaseId,
        extract_rules=payload.extractRules,
        import_policy=payload.importPolicy.lower(),
        limits=payload.limits,
        status="active",  # 新建默认启用
    )
    await db.commit()

    # ------------------------------------------------------------------
    # 步骤 4：返回完整 CrawlSource
    # ------------------------------------------------------------------
    return ApiResponse.success(
        data=_source_to_dict(source),
        meta=build_meta(ctx.project_code),
    )


@router.get("")
async def list_crawl_sources(
    ctx: ProjectContext = Depends(require_scopes(SCOPE_CRAWL_READ)),
    status: str | None = Query(default=None, description="按状态过滤（大写形式），可空"),
    type: str | None = Query(default=None, description="按采集类型过滤（大写形式），可空"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """列出当前项目采集源（``GET /api/v1/crawl-sources``）。

    支持按 ``status`` 与 ``type`` 过滤。``status`` 与 ``type`` 参数为大写形式
    （与对外枚举一致），查询时转小写匹配数据库存储值。

    Args:
        ctx: 项目上下文。
        status: 按状态过滤（大写形式 ACTIVE/PAUSED/DISABLED），可空。
        type: 按采集类型过滤（大写形式），可空。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含：
        - ``items``：采集源列表
        - ``total``：符合条件的采集源总数
    """
    repo = CrawlSourceRepository(db)
    # 查询全部采集源（Repository.list 已带 project_id 过滤）
    all_items = await repo.list(ctx)

    # 内存过滤：按 status 过滤（大写参数 → 小写比较）
    if status is not None:
        status_lower = status.lower()
        all_items = [s for s in all_items if s.status == status_lower]

    # 内存过滤：按 type 过滤（大写参数 → 小写比较）
    if type is not None:
        type_lower = type.lower()
        all_items = [s for s in all_items if s.type == type_lower]

    # 构造响应列表
    items = [_source_to_dict(s) for s in all_items]
    data = {
        "items": items,
        "total": len(items),
    }

    return ApiResponse.success(
        data=data,
        meta=build_meta(ctx.project_code),
    )


@router.get("/{source_id}")
async def get_crawl_source(
    source_id: str,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_CRAWL_READ)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取采集源详情（``GET /api/v1/crawl-sources/{sourceId}``）。

    跨项目隔离
    ----------
    查询时强制带 project_id 过滤，跨项目查询返回 None，统一抛
    ``TaskNotFoundError``（404），不泄露资源是否存在。

    Args:
        source_id: 采集源 ID（路径参数）。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含完整 CrawlSource 信息。

    Raises:
        TaskNotFoundError: 采集源不存在或不属于当前项目（404）。
    """
    repo = CrawlSourceRepository(db)
    # 查询时强制带 project_id 过滤
    source = await repo.get_by_id(ctx, source_id)
    if source is None:
        # 跨项目查询统一返回 404，不泄露采集源是否存在
        raise TaskNotFoundError(
            f"采集源 {source_id} 不存在",
            details={"crawlSourceId": source_id},
        )

    return ApiResponse.success(
        data=_source_to_dict(source),
        meta=build_meta(ctx.project_code),
    )


@router.patch("/{source_id}")
async def update_crawl_source(
    source_id: str,
    payload: CrawlSourceUpdateRequest,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_CRAWL_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """编辑采集源（``PATCH /api/v1/crawl-sources/{sourceId}``）。

    仅允许修改 name / startUrls / allowedDomains / blockedPaths /
    destinationKnowledgeBaseId / extractRules / importPolicy / limits。
    ``code`` / ``type`` 创建后不可改（type 决定采集流程分发逻辑）。
    ``status`` 通过 pause/resume 接口控制，不在此处修改。

    Args:
        source_id: 采集源 ID。
        payload: 编辑请求体，全部字段可选（PATCH 语义）。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含更新后的 CrawlSource。

    Raises:
        TaskNotFoundError: 采集源不存在或不属于当前项目。
        ValidationError: startUrls 格式非法。
    """
    repo = CrawlSourceRepository(db)
    # 先查询采集源（带 project_id 过滤），不存在则 404
    source = await repo.get_by_id(ctx, source_id)
    if source is None:
        raise TaskNotFoundError(
            f"采集源 {source_id} 不存在",
            details={"crawlSourceId": source_id},
        )

    # ------------------------------------------------------------------
    # 构造更新字段字典，仅包含传入的字段（PATCH 语义）
    # ------------------------------------------------------------------
    update_fields: dict = {}

    if payload.name is not None:
        update_fields["name"] = payload.name

    if payload.startUrls is not None:
        # 校验 startUrls 格式
        for url in payload.startUrls:
            if not _validate_url_format(url):
                raise ValidationError(
                    f"startUrls 中包含非法 URL: {url}（仅允许 http/https）",
                    details={"field": "startUrls", "invalidUrl": url},
                )
        update_fields["start_urls"] = payload.startUrls

    if payload.allowedDomains is not None:
        update_fields["allowed_domains"] = payload.allowedDomains

    if payload.blockedPaths is not None:
        update_fields["blocked_paths"] = payload.blockedPaths

    if payload.destinationKnowledgeBaseId is not None:
        update_fields["destination_knowledge_base_id"] = payload.destinationKnowledgeBaseId

    if payload.extractRules is not None:
        update_fields["extract_rules"] = payload.extractRules

    if payload.importPolicy is not None:
        # import_policy 转小写存储
        update_fields["import_policy"] = payload.importPolicy.lower()

    if payload.limits is not None:
        update_fields["limits"] = payload.limits

    # ------------------------------------------------------------------
    # 执行更新（update 方法带 project_id 过滤，双重保险）
    # ------------------------------------------------------------------
    if update_fields:
        updated = await repo.update(ctx, source_id, **update_fields)
        await db.commit()
        source = updated if updated is not None else source

    return ApiResponse.success(
        data=_source_to_dict(source),
        meta=build_meta(ctx.project_code),
    )


@router.delete("/{source_id}")
async def delete_crawl_source(
    source_id: str,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_CRAWL_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """删除采集源（``DELETE /api/v1/crawl-sources/{sourceId}``）。

    仅允许删除 ``status='paused'`` 的采集源（避免误删正在调度的采集源）。
    删除后历史运行记录与页面记录保留（审计追溯）。

    Args:
        source_id: 采集源 ID。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含 ``deleted: true``。

    Raises:
        TaskNotFoundError: 采集源不存在或不属于当前项目。
        ValidationError: 采集源非 paused 状态，需先暂停再删除。
    """
    repo = CrawlSourceRepository(db)
    source = await repo.get_by_id(ctx, source_id)
    if source is None:
        raise TaskNotFoundError(
            f"采集源 {source_id} 不存在",
            details={"crawlSourceId": source_id},
        )

    # 仅允许删除 paused 状态的采集源，避免误删正在调度的采集源
    if source.status != "paused":
        raise ValidationError(
            "采集源当前状态非 paused，请先暂停（POST /crawl-sources/{id}/pause）再删除",
            details={"crawlSourceId": source_id, "status": source.status.upper()},
        )

    # 执行删除（带 project_id 过滤）
    deleted = await repo.delete(ctx, source_id)
    await db.commit()

    return ApiResponse.success(
        data={"deleted": deleted, "crawlSourceId": source_id},
        meta=build_meta(ctx.project_code),
    )


# ============================================================================
# 暂停 / 恢复 / 手动触发接口
# ============================================================================
@router.post("/{source_id}/pause")
async def pause_crawl_source(
    source_id: str,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_CRAWL_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """暂停采集源（``POST /api/v1/crawl-sources/{sourceId}/pause``）。

    将 ``status`` 置为 ``paused``，禁止后续手动触发采集。
    已在运行中的采集不受影响（Celery 任务继续执行完成）。

    Args:
        source_id: 采集源 ID。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含更新后的 CrawlSource。

    Raises:
        TaskNotFoundError: 采集源不存在或不属于当前项目。
    """
    repo = CrawlSourceRepository(db)
    source = await repo.get_by_id(ctx, source_id)
    if source is None:
        raise TaskNotFoundError(
            f"采集源 {source_id} 不存在",
            details={"crawlSourceId": source_id},
        )

    # set_status 仅修改 status 字段（带 project_id 过滤）
    updated = await repo.set_status(ctx, source_id, "paused")
    await db.commit()
    source = updated if updated is not None else source

    return ApiResponse.success(
        data=_source_to_dict(source),
        meta=build_meta(ctx.project_code),
    )


@router.post("/{source_id}/resume")
async def resume_crawl_source(
    source_id: str,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_CRAWL_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """恢复采集源（``POST /api/v1/crawl-sources/{sourceId}/resume``）。

    将 ``status`` 置为 ``active``，允许后续手动触发采集。

    Args:
        source_id: 采集源 ID。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含更新后的 CrawlSource。

    Raises:
        TaskNotFoundError: 采集源不存在或不属于当前项目。
    """
    repo = CrawlSourceRepository(db)
    source = await repo.get_by_id(ctx, source_id)
    if source is None:
        raise TaskNotFoundError(
            f"采集源 {source_id} 不存在",
            details={"crawlSourceId": source_id},
        )

    # set_status 仅修改 status 字段（带 project_id 过滤）
    updated = await repo.set_status(ctx, source_id, "active")
    await db.commit()
    source = updated if updated is not None else source

    return ApiResponse.success(
        data=_source_to_dict(source),
        meta=build_meta(ctx.project_code),
    )


@router.post("/{source_id}/runs")
async def trigger_crawl_run(
    source_id: str,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_CRAWL_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """手动触发采集（``POST /api/v1/crawl-sources/{sourceId}/runs``）。

    立即创建 CrawlRun（status='pending'）并投递 ``run_crawl`` 到 crawler 队列。
    手动触发不受调度控制，立即执行一次。

    流程
    ----
    1. 校验采集源存在且属于当前项目
    2. 校验采集源状态为 active（paused 状态不允许触发）
    3. 创建 CrawlRun（status='pending'）
    4. 投递 ``run_crawl`` 到 crawler 队列
    5. 回写 queue_job_id（Celery 任务 ID）

    Args:
        source_id: 采集源 ID。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含：
        - ``runId``：新建的运行记录 ID
        - ``queueJobId``：Celery 任务 ID
        - ``status``：固定为 ``PENDING``

    Raises:
        TaskNotFoundError: 采集源不存在或不属于当前项目。
        ValidationError: 采集源非 active 状态，需先恢复再触发。
    """
    # ------------------------------------------------------------------
    # 步骤 1 & 2：校验采集源归属与状态
    # ------------------------------------------------------------------
    source_repo = CrawlSourceRepository(db)
    source = await source_repo.get_by_id(ctx, source_id)
    if source is None:
        raise TaskNotFoundError(
            f"采集源 {source_id} 不存在",
            details={"crawlSourceId": source_id},
        )

    # 防御性校验：paused 状态不允许触发采集
    if source.status != "active":
        raise ValidationError(
            "采集源当前状态非 active，请先恢复（POST /crawl-sources/{id}/resume）再触发采集",
            details={"crawlSourceId": source_id, "status": source.status.upper()},
        )

    # ------------------------------------------------------------------
    # 步骤 3：创建运行记录（status='pending'）
    # ------------------------------------------------------------------
    run_repo = CrawlRunRepository(db)
    run = await run_repo.create(ctx, source_id)
    await db.commit()

    # ------------------------------------------------------------------
    # 步骤 4：投递采集任务到 crawler 队列
    # ------------------------------------------------------------------
    # 延迟导入避免循环依赖
    from app.workers.crawl_tasks import run_crawl

    # delay() 将任务序列化后投递到 crawler 队列，立即返回
    async_result = run_crawl.delay(
        ctx.project_id,
        ctx.project_code,
        source_id,
        run.id,
    )

    # ------------------------------------------------------------------
    # 步骤 5：回写 queue_job_id（Celery 任务 ID）
    # ------------------------------------------------------------------
    # CrawlRun 模型无 queue_job_id 字段，此处仅记录日志
    # 任务执行进度通过 CrawlRun.status 跟踪
    await db.commit()

    return ApiResponse.success(
        data={
            "runId": run.id,
            "queueJobId": async_result.id,
            "status": "PENDING",
        },
        meta=build_meta(ctx.project_code),
    )


# ============================================================================
# 运行记录查询接口
# ============================================================================
@router.get("/{source_id}/runs")
async def list_crawl_runs(
    source_id: str,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_CRAWL_READ)),
    offset: int = Query(default=0, ge=0, description="分页偏移量"),
    limit: int = Query(default=20, ge=1, le=100, description="每页条数，默认 20"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """列出采集源的运行记录（``GET /api/v1/crawl-sources/{sourceId}/runs``）。

    先校验采集源归属当前项目（防止跨项目枚举运行记录），
    再分页查询关联的 CrawlRun。

    Args:
        source_id: 采集源 ID。
        ctx: 项目上下文。
        offset: 分页偏移量。
        limit: 每页条数。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含：
        - ``items``：运行记录列表
        - ``total``：符合条件的运行记录总数
        ``meta`` 字段包含分页信息。

    Raises:
        TaskNotFoundError: 采集源不存在或不属于当前项目。
    """
    # 先校验采集源归属，防止跨项目枚举运行记录
    source_repo = CrawlSourceRepository(db)
    source = await source_repo.get_by_id(ctx, source_id)
    if source is None:
        raise TaskNotFoundError(
            f"采集源 {source_id} 不存在",
            details={"crawlSourceId": source_id},
        )

    # 分页查询运行记录（list_by_source 已带 project_id + crawl_source_id 过滤）
    run_repo = CrawlRunRepository(db)
    items, total = await run_repo.list_by_source(ctx, source_id, offset, limit)

    data = {
        "items": [_run_to_dict(r) for r in items],
        "total": total,
    }
    meta = build_meta(ctx.project_code)
    meta["pagination"] = {
        "offset": offset,
        "limit": limit,
        "total": total,
    }

    return ApiResponse.success(data=data, meta=meta)


@crawl_runs_router.get("/{run_id}")
async def get_crawl_run(
    run_id: str,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_CRAWL_READ)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """运行记录详情（``GET /api/v1/crawl-runs/{runId}``）。

    跨项目隔离
    ----------
    查询时强制带 project_id 过滤，跨项目查询返回 None，统一抛
    ``TaskNotFoundError``（404）。

    Args:
        run_id: 运行记录 ID（路径参数）。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含完整运行记录信息。

    Raises:
        TaskNotFoundError: 运行记录不存在或不属于当前项目（404）。
    """
    run_repo = CrawlRunRepository(db)
    # 查询时强制带 project_id 过滤
    run = await run_repo.get_by_id(ctx, run_id)
    if run is None:
        # 跨项目查询统一返回 404，不泄露运行记录是否存在
        raise TaskNotFoundError(
            f"运行记录 {run_id} 不存在",
            details={"runId": run_id},
        )

    return ApiResponse.success(
        data=_run_to_dict(run),
        meta=build_meta(ctx.project_code),
    )


@crawl_runs_router.get("/{run_id}/pages")
async def list_crawl_pages(
    run_id: str,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_CRAWL_READ)),
    status: str | None = Query(default=None, description="按页面状态过滤（大写形式），可空"),
    offset: int = Query(default=0, ge=0, description="分页偏移量"),
    limit: int = Query(default=50, ge=1, le=200, description="每页条数，默认 50"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """列出运行记录的页面列表（``GET /api/v1/crawl-runs/{runId}/pages``）。

    先校验运行记录归属当前项目，再分页查询关联的 CrawlPage。
    支持按 ``status`` 过滤（如只看待审核或失败的页面）。

    Args:
        run_id: 运行记录 ID。
        ctx: 项目上下文。
        status: 按页面状态过滤（大写形式），可空。
        offset: 分页偏移量。
        limit: 每页条数，默认 50。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含：
        - ``items``：页面列表
        - ``total``：符合条件的页面总数
        ``meta`` 字段包含分页信息。

    Raises:
        TaskNotFoundError: 运行记录不存在或不属于当前项目。
    """
    # 先校验运行记录归属，防止跨项目枚举页面
    run_repo = CrawlRunRepository(db)
    run = await run_repo.get_by_id(ctx, run_id)
    if run is None:
        raise TaskNotFoundError(
            f"运行记录 {run_id} 不存在",
            details={"runId": run_id},
        )

    # 分页查询页面记录（list_by_run 已带 project_id + crawl_run_id 过滤）
    page_repo = CrawlPageRepository(db)
    items, total = await page_repo.list_by_run(ctx, run_id, offset, limit)

    # 内存过滤：按 status 过滤（大写参数 → 小写比较）
    if status is not None:
        status_lower = status.lower()
        items = [p for p in items if p.status == status_lower]
        total = len(items)

    data = {
        "items": [_page_to_dict(p) for p in items],
        "total": total,
    }
    meta = build_meta(ctx.project_code)
    meta["pagination"] = {
        "offset": offset,
        "limit": limit,
        "total": total,
    }

    return ApiResponse.success(data=data, meta=meta)


# ============================================================================
# 采集页面审核接口
# ============================================================================
@crawl_pages_router.post("/{page_id}/approve")
async def approve_crawl_page(
    page_id: str,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_CRAWL_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """审核通过采集页面（``POST /api/v1/crawl-pages/{pageId}/approve``）。

    将关联的 WebMaterial 状态改为 ``adopted``，并触发文档入库
    （创建 Document + IngestionJob，触发向量化）。

    流程
    ----
    1. 查询 CrawlPage（带 project_id 过滤）
    2. 查询关联的 WebMaterial（status='pending'）
    3. 创建 Document（source_type='crawler'）
    4. 创建 IngestionJob（触发向量化入库）
    5. 更新 WebMaterial.status='adopted'，knowledge_base_id，reviewed_at
    6. 更新 CrawlPage.status='imported'，document_id

    Args:
        page_id: 采集页面 ID。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含：
        - ``materialId``：审核的资料 ID
        - ``documentId``：创建的文档 ID
        - ``status``：固定为 ``ADOPTED``

    Raises:
        TaskNotFoundError: 页面不存在或不属于当前项目。
        ValidationError: 页面无关联的待审核资料，或采集源未配置目标知识库。
    """
    # ------------------------------------------------------------------
    # 步骤 1：查询 CrawlPage
    # ------------------------------------------------------------------
    page_repo = CrawlPageRepository(db)
    page = await page_repo.get_by_id(ctx, page_id)
    if page is None:
        raise TaskNotFoundError(
            f"采集页面 {page_id} 不存在",
            details={"pageId": page_id},
        )

    # ------------------------------------------------------------------
    # 步骤 2：查询关联的 WebMaterial
    # ------------------------------------------------------------------
    material_repo = WebMaterialRepository(db)
    # 通过 crawl_page_id 查询关联的待审核资料
    # WebMaterialRepository 未提供按 crawl_page_id 查询的方法，使用 list + 过滤
    all_materials, _ = await material_repo.list(ctx, status_filter="pending")
    material = next(
        (m for m in all_materials if m.crawl_page_id == page_id), None
    )
    if material is None:
        raise ValidationError(
            f"采集页面 {page_id} 无关联的待审核资料",
            details={"pageId": page_id},
        )

    # ------------------------------------------------------------------
    # 步骤 3：确定入库目标知识库
    # ------------------------------------------------------------------
    # 优先使用 WebMaterial.knowledge_base_id，其次查询采集源的 destination_knowledge_base_id
    target_kb_id = material.knowledge_base_id
    if not target_kb_id:
        # 查询采集源配置
        source_repo = CrawlSourceRepository(db)
        source = await source_repo.get_by_id(ctx, page.crawl_source_id)
        if source is not None and source.destination_knowledge_base_id:
            target_kb_id = source.destination_knowledge_base_id

    if not target_kb_id:
        raise ValidationError(
            "无法确定入库目标知识库（采集源未配置 destinationKnowledgeBaseId，"
            "且 WebMaterial.knowledge_base_id 为空）",
            details={"pageId": page_id, "crawlSourceId": page.crawl_source_id},
        )

    # 校验知识库存在且属于当前项目
    kb_repo = KnowledgeBaseRepository(db)
    kb = await kb_repo.get_by_id(ctx, target_kb_id)
    if kb is None:
        raise ValidationError(
            f"目标知识库 {target_kb_id} 不存在或不属于当前项目",
            details={"knowledgeBaseId": target_kb_id},
        )

    # ------------------------------------------------------------------
    # 步骤 4：创建 Document + IngestionJob
    # ------------------------------------------------------------------
    doc_repo = DocumentRepository(db)
    job_repo = IngestionJobRepository(db)

    doc = await doc_repo.create(
        ctx,
        knowledge_base_id=kb.id,
        source_type="crawler",  # 标记为爬虫采集来源
        title=material.title,
        source_url=material.source_url,
        content_hash=page.content_hash,
        processing_status="pending",
        metadata_={
            "crawl_source_id": page.crawl_source_id,
            "crawl_page_id": page.id,
            "web_material_id": material.id,
        },
    )

    # 创建 IngestionJob（触发向量化入库流程）
    await job_repo.create(ctx, doc.id)

    # 延迟导入避免循环依赖
    # 触发向量化入库任务（投递到 ingestion 队列）
    try:
        from app.workers.ingestion_tasks import process_document

        process_document.delay(ctx.project_id, doc.id, None)
    except Exception:
        # 投递失败不阻塞审核流程，IngestionJob 保留 pending 状态
        pass

    # ------------------------------------------------------------------
    # 步骤 5：更新 WebMaterial 状态
    # ------------------------------------------------------------------
    now = datetime.now(timezone.utc)
    await material_repo.update(
        ctx,
        material.id,
        status="adopted",
        knowledge_base_id=kb.id,
        reviewed_at=now,
    )

    # ------------------------------------------------------------------
    # 步骤 6：更新 CrawlPage 状态
    # ------------------------------------------------------------------
    await page_repo.update(
        ctx,
        page.id,
        status="imported",
        document_id=doc.id,
    )

    await db.commit()

    return ApiResponse.success(
        data={
            "materialId": material.id,
            "documentId": doc.id,
            "status": "ADOPTED",
        },
        meta=build_meta(ctx.project_code),
    )


@crawl_pages_router.post("/{page_id}/reject")
async def reject_crawl_page(
    page_id: str,
    ctx: ProjectContext = Depends(require_scopes(SCOPE_CRAWL_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """审核拒绝采集页面（``POST /api/v1/crawl-pages/{pageId}/reject``）。

    将关联的 WebMaterial 状态改为 ``rejected``，不入库到知识库。

    Args:
        page_id: 采集页面 ID。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含：
        - ``materialId``：审核的资料 ID
        - ``status``：固定为 ``REJECTED``

    Raises:
        TaskNotFoundError: 页面不存在或不属于当前项目。
        ValidationError: 页面无关联的待审核资料。
    """
    # ------------------------------------------------------------------
    # 步骤 1：查询 CrawlPage
    # ------------------------------------------------------------------
    page_repo = CrawlPageRepository(db)
    page = await page_repo.get_by_id(ctx, page_id)
    if page is None:
        raise TaskNotFoundError(
            f"采集页面 {page_id} 不存在",
            details={"pageId": page_id},
        )

    # ------------------------------------------------------------------
    # 步骤 2：查询关联的 WebMaterial
    # ------------------------------------------------------------------
    material_repo = WebMaterialRepository(db)
    all_materials, _ = await material_repo.list(ctx, status_filter="pending")
    material = next(
        (m for m in all_materials if m.crawl_page_id == page_id), None
    )
    if material is None:
        raise ValidationError(
            f"采集页面 {page_id} 无关联的待审核资料",
            details={"pageId": page_id},
        )

    # ------------------------------------------------------------------
    # 步骤 3：更新 WebMaterial 状态为 rejected
    # ------------------------------------------------------------------
    now = datetime.now(timezone.utc)
    await material_repo.update(
        ctx,
        material.id,
        status="rejected",
        reviewed_at=now,
    )
    await db.commit()

    return ApiResponse.success(
        data={
            "materialId": material.id,
            "status": "REJECTED",
        },
        meta=build_meta(ctx.project_code),
    )


# ============================================================================
# 待审核资料查询接口
# ============================================================================
@web_materials_router.get("")
async def list_web_materials(
    ctx: ProjectContext = Depends(require_scopes(SCOPE_CRAWL_READ)),
    status: str | None = Query(
        default=None,
        description="按审核状态过滤（大写形式 PENDING/ADOPTED/REJECTED/EXPIRED），可空",
    ),
    offset: int = Query(default=0, ge=0, description="分页偏移量"),
    limit: int = Query(default=20, ge=1, le=100, description="每页条数，默认 20"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """列出待审核资料（``GET /api/v1/web-materials``）。

    分页列出当前项目的网络待审核资料，可按审核状态过滤。
    默认列出所有状态，传入 ``status=PENDING`` 仅看待审核的。

    Args:
        ctx: 项目上下文。
        status: 按审核状态过滤（大写形式），可空。
        offset: 分页偏移量。
        limit: 每页条数。
        db: 异步数据库会话。

    Returns:
        标准响应字典，``data`` 字段包含：
        - ``items``：资料列表
        - ``total``：符合条件的资料总数
        ``meta`` 字段包含分页信息。
    """
    # status 参数转小写匹配数据库存储值
    status_filter = status.lower() if status is not None else None

    material_repo = WebMaterialRepository(db)
    items, total = await material_repo.list(ctx, status_filter, offset, limit)

    data = {
        "items": [_material_to_dict(m) for m in items],
        "total": total,
    }
    meta = build_meta(ctx.project_code)
    meta["pagination"] = {
        "offset": offset,
        "limit": limit,
        "total": total,
    }

    return ApiResponse.success(data=data, meta=meta)
