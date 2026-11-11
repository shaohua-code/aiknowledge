"""平台维护操作路由。

提供孤儿对象清理、幂等记录清理等平台级维护功能的管理入口。
这些操作由 Celery Beat 定时执行，管理员也可手动触发诊断。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.config import settings
from knowledge_core.control.auth import require_admin, require_control_permission
from knowledge_core.control.permissions import Permission
from knowledge_core.infrastructure.database import get_session
from knowledge_core.infrastructure.models import Document, DocumentRevision, IdempotencyRecord
from knowledge_core.infrastructure.storage import storage
from knowledge_core.shared.response import success
from knowledge_core.shared.schema import ApiSchema, ApiSuccess

router = APIRouter(
    prefix="/control/v1/maintenance",
    tags=["平台维护"],
    dependencies=[
        Depends(require_admin),
        Depends(
            require_control_permission(
                Permission.PLATFORM_MAINTENANCE,
                Permission.PLATFORM_MAINTENANCE,
            )
        ),
    ],
)


class OrphanCleanupStatusResponse(ApiSchema):
    """孤儿对象清理状态响应。"""

    orphan_retention_hours: int
    document_storage_keys: int
    revision_storage_keys: int
    total_referenced_keys: int


class OrphanCleanupPreviewResponse(ApiSchema):
    """孤儿对象清理预览响应，列出即将清理的文件数量。"""

    would_delete_count: int
    cutoff_time: datetime
    preview_keys: list[str]


class OrphanCleanupExecuteResponse(ApiSchema):
    """孤儿对象清理执行结果。"""

    deleted_count: int
    expired_idempotency_records: int


@router.get("/orphan-cleanup/status", response_model=ApiSuccess[OrphanCleanupStatusResponse])
async def get_orphan_cleanup_status(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """查询当前孤儿清理配置和引用统计，用于诊断清理效果。"""
    document_key_count = await session.scalar(
        select(func.count(Document.id)).where(Document.storage_key.is_not(None))
    ) or 0
    revision_key_count = await session.scalar(
        select(func.count(DocumentRevision.id)).where(DocumentRevision.storage_key.is_not(None))
    ) or 0
    return success({
        "orphanRetentionHours": settings.orphan_object_retention_hours,
        "documentStorageKeys": document_key_count,
        "revisionStorageKeys": revision_key_count,
        "totalReferencedKeys": document_key_count + revision_key_count,
    })


@router.get("/orphan-cleanup/preview", response_model=ApiSuccess[OrphanCleanupPreviewResponse])
async def preview_orphan_cleanup(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """预览将被清理的孤儿对象，不执行实际删除。"""
    document_keys = set(
        await session.scalars(
            select(Document.storage_key).where(Document.storage_key.is_not(None))
        )
    )
    revision_keys = set(
        await session.scalars(
            select(DocumentRevision.storage_key).where(DocumentRevision.storage_key.is_not(None))
        )
    )
    referenced_keys = document_keys | revision_keys
    cutoff_time = datetime.now(UTC) - timedelta(hours=settings.orphan_object_retention_hours)

    # 预览模式：遍历但不删除，只收集候选文件
    application_root = storage.root / "app"
    preview_keys: list[str] = []
    if application_root.is_dir():
        for path in application_root.rglob("*"):
            if not path.is_file():
                continue
            key = path.relative_to(storage.root).as_posix()
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if key in referenced_keys or modified_at >= cutoff_time:
                continue
            preview_keys.append(key)
            if len(preview_keys) >= limit:
                break

    return success({
        "wouldDeleteCount": len(preview_keys),
        "cutoffTime": cutoff_time.isoformat(),
        "previewKeys": preview_keys,
    })


@router.post("/orphan-cleanup/execute", response_model=ApiSuccess[OrphanCleanupExecuteResponse])
async def execute_orphan_cleanup(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """手动触发孤儿清理；与定时任务使用相同逻辑。

    清理操作包括：
    1. 删除已过期的幂等记录
    2. 删除超过保留窗口且无数据库引用的对象存储文件
    """
    # 同步调用 Celery 任务的核心逻辑，无需等待异步队列
    from knowledge_core.workers.tasks import _cleanup_orphan_objects
    result = await _cleanup_orphan_objects()
    return success({
        "deletedCount": result.get("deleted", 0),
        "expiredIdempotencyRecords": result.get("expiredIdempotencyRecords", 0),
    })


@router.get("/idempotency/stats", response_model=ApiSuccess[dict])
async def get_idempotency_stats(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """查询当前幂等记录统计，用于诊断存储增长。"""
    total_count = await session.scalar(select(func.count(IdempotencyRecord.id))) or 0
    expired_count = await session.scalar(
        select(func.count(IdempotencyRecord.id)).where(
            IdempotencyRecord.expires_at < datetime.now(UTC)
        )
    ) or 0
    pending_count = await session.scalar(
        select(func.count(IdempotencyRecord.id)).where(IdempotencyRecord.state == "pending")
    ) or 0
    completed_count = await session.scalar(
        select(func.count(IdempotencyRecord.id)).where(IdempotencyRecord.state == "completed")
    ) or 0

    return success({
        "totalRecords": total_count,
        "expiredRecords": expired_count,
        "pendingRecords": pending_count,
        "completedRecords": completed_count,
    })
