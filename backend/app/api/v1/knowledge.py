"""知识库管理接口（项目 API Key + Scope 校验）。

对应 SubTask 7.3 与 SubTask 8：
- 创建知识库：POST /api/v1/knowledge-bases（需 ``knowledge:write`` Scope）
- 列出知识库：GET /api/v1/knowledge-bases（仅需项目上下文）
- 获取知识库详情：GET /api/v1/knowledge-bases/{code}
- 编辑知识库：PATCH /api/v1/knowledge-bases/{code}（需 ``knowledge:write`` Scope）
- 删除知识库：DELETE /api/v1/knowledge-bases/{code}（需 ``knowledge:write`` Scope）
- 启停知识库：POST /api/v1/knowledge-bases/{code}/disable|enable

文档导入接口（SubTask 8）
- 文件上传：POST /api/v1/knowledge-bases/{code}/documents/file（multipart/form-data）
- 文本/URL 写入：POST /api/v1/knowledge-bases/{code}/documents（application/json）
- 文档状态查询：GET /api/v1/documents/{documentId}

设计要点（重点：跨项目隔离）
----------------------------
1. 所有查询通过 Repository 强制带 ``project_id`` 过滤，跨项目查询返回 None，
   由端点统一抛 ``KnowledgeBaseNotFoundError``（404，不泄露资源是否存在）。
2. ``code`` 仅在项目内唯一，不同项目可有相同 code，因此 Repository.get_by_code
   必须同时带 project_id 与 code 双重过滤。
3. 创建知识库时，``embeddingDimension`` 不传则从 ProjectSettings 读取默认值，
   避免不同项目配置不同维度导致向量列类型不匹配。
4. ``code`` 与 ``embeddingDimension`` 创建后不可改：
   - PATCH 接口的请求模型不含这两个字段
   - 端点显式拒绝请求体中可能携带的 ``code`` / ``embeddingDimension`` 字段
5. 删除知识库仅允许空知识库（document_count=0），非空返回 VALIDATION_ERROR，
   提示客户端先处理内部文档。
6. 跨项目伪造 project_id：请求体中即使携带其他项目的 projectId，服务端仍以
   API Key 解析得到的项目为准（ProjectContext 不可变，详见 project_context.py）。
7. 文件存储路径以 ``projects/{project_id}/{kb_id}/{document_id}{ext}`` 形式组织，
   前缀中的 ``project_id`` 在存储层实现项目隔离，即使其他项目误传相同 document_id
   也写入不同物理目录，物理隔离杜绝跨项目文件覆盖。
8. 幂等机制：所有写入接口支持 ``Idempotency-Key`` 头，相同 Key + 相同请求内容
   重放原响应；相同 Key + 不同内容抛 ``IdempotencyConflictError``（409）。
9. 去重机制：通过 ``content_hash``（SHA-256）检测同一知识库下相同内容文档，
   命中则返回已存在文档，避免重复入库与向量化。
"""
from __future__ import annotations

import hashlib
import json
import re

from fastapi import APIRouter, Body, Depends, File, Form, Header, Path, Query, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_project_context, require_scopes
from app.api.v1.schemas import (
    DocumentCreateRequest,
    DocumentImportResponse,
    DocumentResponse,
    KnowledgeBaseCreateRequest,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdateRequest,
)
from app.core.config import settings
from app.core.exceptions import (
    KnowledgeBaseNotFoundError,
    TaskNotFoundError,
    ValidationError,
)
from app.core.idempotency import (
    check_idempotency,
    compute_file_request_hash,
    compute_request_hash,
    set_idempotency_record,
)
from app.core.project_context import ProjectContext
from app.core.response import ApiResponse, build_meta
from app.core.scopes import SCOPE_KNOWLEDGE_WRITE, SCOPE_TASKS_READ
from app.db.repositories.ingestion import IngestionJobRepository
from app.db.repositories.knowledge import (
    DocumentChunkRepository,
    DocumentRepository,
    KnowledgeBaseRepository,
)
from app.db.repositories.project import ProjectSettingsRepository
from app.db.session import get_db
from app.providers.object_storage import get_object_storage
from app.workers.ingestion_tasks import process_document

# 知识库编码格式正则：与项目 code 一致，小写字母+数字+连字符，3-32 字符
# 首尾需为字母或数字，避免出现 -code- 这种边界异常
_KB_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$")

router = APIRouter(prefix="/knowledge-bases", tags=["知识库管理"])

# 文档状态查询路由器：路径为 /api/v1/documents/{documentId}，
# 不在 /knowledge-bases 前缀下，单独注册到 api_router
documents_router = APIRouter(prefix="/documents", tags=["文档处理"])


# ============================================================================
# 文件类型与大小校验常量（SubTask 8.1）
# ============================================================================

# 允许上传的文件 MIME 类型与扩展名映射
# PDF / DOCX / TXT / MD 是知识库常见文档格式
_ALLOWED_MIME_TYPES: dict[str, str] = {
    "application/pdf": ".pdf",
    # DOCX 的 MIME 类型较长，记忆口诀：office open xml wordprocessingml document
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
    "text/markdown": ".md",
}

# 允许的扩展名集合（用于 MIME 类型缺失时按扩展名兜底校验）
_ALLOWED_EXTENSIONS: set[str] = {".pdf", ".docx", ".txt", ".md"}

# 文件大小上限：20MB
# 超过此大小拒绝上传，避免单文件占用过多内存与存储
_MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024

# 处理状态映射：内部小写 → 对外大写
# 内部状态：pending / parsing / chunking / embedding / ready / failed
# 对外状态：PENDING / PARSING / CHUNKING / EMBEDDING / READY / FAILED
_PROCESSING_STATUS_MAP: dict[str, str] = {
    "pending": "PENDING",
    "parsing": "PARSING",
    "chunking": "CHUNKING",
    "embedding": "EMBEDDING",
    "ready": "READY",
    "completed": "READY",  # 历史数据兼容：completed 视为 READY
    "processing": "EMBEDDING",  # 历史数据兼容：processing 视为 EMBEDDING
    "failed": "FAILED",
}


def _map_processing_status(internal_status: str) -> str:
    """将内部处理状态映射为对外大写形式。

    内部数据库存储小写状态（pending/parsing/.../failed），
    对外 API 返回大写形式（PENDING/PARSING/.../FAILED），
    便于客户端按枚举值处理。

    Args:
        internal_status: 内部状态字符串。

    Returns:
        对外大写状态字符串；未知状态默认返回 ``PENDING``。
    """
    return _PROCESSING_STATUS_MAP.get(internal_status, "PENDING")


def _parse_tags(tags_str: str | None) -> list[str] | None:
    """解析表单中的 tags 字段（JSON 字符串数组）。

    文件上传接口使用 multipart/form-data，tags 以 JSON 字符串形式传输，
    需在此解析为 list[str]。

    Args:
        tags_str: tags 字段原始值，形如 ``["基金", "投资"]``。

    Returns:
        解析后的标签列表；输入为 None 或空字符串返回 None。

    Raises:
        ValidationError: tags 不是合法 JSON 数组。
    """
    if not tags_str:
        return None
    try:
        tags = json.loads(tags_str)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            "tags 字段不是合法 JSON",
            details={"field": "tags", "value": tags_str},
        ) from exc
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise ValidationError(
            "tags 字段必须是字符串数组",
            details={"field": "tags", "value": tags_str},
        )
    return tags


def _parse_metadata(metadata_str: str | None) -> dict | None:
    """解析表单中的 metadata 字段（JSON 对象）。

    文件上传接口使用 multipart/form-data，metadata 以 JSON 字符串形式传输，
    需在此解析为 dict。

    Args:
        metadata_str: metadata 字段原始值，形如 ``{"author": "A"}``。

    Returns:
        解析后的元数据 dict；输入为 None 或空字符串返回 None。

    Raises:
        ValidationError: metadata 不是合法 JSON 对象。
    """
    if not metadata_str:
        return None
    try:
        metadata = json.loads(metadata_str)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            "metadata 字段不是合法 JSON",
            details={"field": "metadata", "value": metadata_str},
        ) from exc
    if not isinstance(metadata, dict):
        raise ValidationError(
            "metadata 字段必须是 JSON 对象",
            details={"field": "metadata", "value": metadata_str},
        )
    return metadata


def _build_storage_key(
    project_id: str,
    kb_id: str,
    document_id: str,
    ext: str,
) -> str:
    """构造对象存储路径（storage_key）。

    路径格式：``projects/{project_id}/{kb_id}/{document_id}{ext}``

    为什么以 ``projects/{project_id}/`` 为前缀？
        1. 项目隔离：不同项目的文件物理隔离到不同目录，
           即使其他项目误传相同 document_id 也写入不同路径，杜绝覆盖。
        2. 便于运维：按项目目录批量清理（如项目下线时删除 ``projects/{pid}/`` 全部文件）。
        3. 适配 S3：S3 桶内按前缀分片，项目前缀天然契合 S3 分区策略。

    Args:
        project_id: 项目 ID（已由 ProjectContext 提供，可信）。
        kb_id: 知识库 ID。
        document_id: 文档 ID（数据库 gen_random_uuid 生成）。
        ext: 文件扩展名（含点，如 ``.pdf``）。

    Returns:
        相对存储路径，如 ``projects/abc/kb1/doc1.pdf``。
    """
    # 三级目录：项目 → 知识库 → 文档
    return f"projects/{project_id}/{kb_id}/{document_id}{ext}"


def _build_import_response(
    document_id: str,
    ingestion_job_id: str,
) -> dict:
    """构造文档导入响应体。

    Args:
        document_id: 文档 ID。
        ingestion_job_id: 入库任务 ID。

    Returns:
        DocumentImportResponse 序列化后的 dict。
    """
    return DocumentImportResponse(
        documentId=document_id,
        ingestionTaskId=ingestion_job_id,
        status="PENDING",  # 初始状态固定为 PENDING
    ).model_dump(mode="json")


# ============================================================================
# 创建知识库
# ============================================================================
@router.post(
    "",
    summary="创建知识库",
    description=(
        "在当前项目下创建知识库。``code`` 项目内唯一（大小写不敏感），"
        "不同项目可有相同 code。``embeddingDimension`` 不传则用项目设置默认值。"
    ),
    response_model=None,
)
async def create_knowledge_base(
    payload: KnowledgeBaseCreateRequest = Body(...),
    ctx: ProjectContext = Depends(require_scopes(SCOPE_KNOWLEDGE_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """创建知识库。

    业务流程：
        1. 校验 code 格式（正则）
        2. 若 embeddingDimension 未传，从 ProjectSettings 读取默认值
        3. 调用 KnowledgeBaseRepository.create 落库（project_id 由 ctx 注入）
        4. 复合唯一约束冲突（项目内 code 重复）返回 VALIDATION_ERROR
        5. 返回新建知识库信息

    为什么需要 project_id 过滤？
        ``code`` 仅在项目内唯一，不同项目可有相同 code（如 ai-fund 与 ai-resume
        都可创建名为 ``research`` 的知识库）。Repository.create 通过 ctx.project_id
        强制归属，复合唯一约束 ``uq_knowledge_bases_project_code`` 在数据库层
        杜绝同项目内 code 重复。

    Args:
        payload: 请求体，包含 code / name / description / embeddingModel / embeddingDimension。
        ctx: 项目上下文（由 require_scopes 依赖校验 Scope 后返回）。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 为新建知识库信息。
    """
    # ------------------------------------------------------------------
    # 步骤 1：校验 code 格式
    # ------------------------------------------------------------------
    if not _KB_CODE_PATTERN.match(payload.code):
        raise ValidationError(
            "知识库编码格式非法：需为 3-32 字符的小写字母+数字+连字符，首尾需为字母或数字",
            details={"field": "code", "value": payload.code},
        )

    # ------------------------------------------------------------------
    # 步骤 2：解析 embeddingDimension，未传则从项目设置读取默认值
    # ------------------------------------------------------------------
    embedding_dimension = payload.embeddingDimension
    if embedding_dimension is None:
        # 从 ProjectSettings 读取，若未配置则使用全局 settings.embedding_dimension
        settings_repo = ProjectSettingsRepository(db)
        project_settings = await settings_repo.get_by_project(ctx)
        if project_settings is not None and project_settings.embedding_model is not None:
            # 项目设置了 embedding_model，但未单独存 dimension，使用全局默认
            embedding_dimension = settings.embedding_dimension
        else:
            # 项目未配置 embedding_model，使用全局默认维度
            embedding_dimension = settings.embedding_dimension

    # ------------------------------------------------------------------
    # 步骤 3：落库知识库（project_id 由 ctx 注入）
    # ------------------------------------------------------------------
    kb_repo = KnowledgeBaseRepository(db)
    try:
        kb = await kb_repo.create(
            ctx=ctx,
            name=payload.name,
            code=payload.code,
            description=payload.description,
            embedding_model=payload.embeddingModel,
            embedding_dimension=embedding_dimension,
        )
        await db.commit()
    except IntegrityError as exc:
        # 复合唯一约束冲突：同项目内 code 已存在
        await db.rollback()
        raise ValidationError(
            f"知识库编码在当前项目内已存在：{payload.code}",
            details={"field": "code", "value": payload.code},
        ) from exc

    # ------------------------------------------------------------------
    # 步骤 4：构造响应
    # ------------------------------------------------------------------
    data = KnowledgeBaseResponse(
        id=kb.id,
        code=kb.code,
        name=kb.name,
        description=kb.description,
        embeddingModel=kb.embedding_model,
        embeddingDimension=kb.embedding_dimension,
        status=kb.status,
        documentCount=None,  # 创建时无需返回文档数
        createdAt=kb.created_at,
    ).model_dump(mode="json")
    return ApiResponse.success(data, build_meta(ctx.project_code))


# ============================================================================
# 列出当前项目知识库
# ============================================================================
@router.get(
    "",
    summary="列出当前项目知识库",
    description="列出当前 API Key 所属项目下所有知识库，可按状态过滤。",
    response_model=None,
)
async def list_knowledge_bases(
    status: str | None = Query(default=None, description="状态过滤：active / disabled"),
    ctx: ProjectContext = Depends(get_project_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """列出当前项目知识库。

    业务流程：
        1. 校验 status 参数（若传入）
        2. 调用 KnowledgeBaseRepository.list 查询（强制 project_id 过滤）
        3. 对每个知识库统计文档数（documentCount）
        4. 返回列表

    为什么仅需 get_project_context 而非 require_scopes？
        列表查询是只读操作，任何有效 API Key 都可查看自己项目的知识库列表，
        不需要特定 Scope。但 project_id 过滤依然强制，杜绝跨项目查看。

    Args:
        status: 状态过滤，可空。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应体，data.items 为知识库列表（含 documentCount）。
    """
    # 校验 status 枚举值
    if status is not None and status not in ("active", "disabled"):
        raise ValidationError(
            "status 参数非法，仅允许 active 或 disabled",
            details={"field": "status", "value": status},
        )

    kb_repo = KnowledgeBaseRepository(db)
    kbs = await kb_repo.list(ctx, status_filter=status)

    # 批量查询文档数（每个知识库单独 COUNT，知识库数量通常 < 100，可接受）
    items = []
    for kb in kbs:
        doc_count = await kb_repo.count_documents(ctx, kb.id)
        items.append(
            KnowledgeBaseResponse(
                id=kb.id,
                code=kb.code,
                name=kb.name,
                description=kb.description,
                embeddingModel=kb.embedding_model,
                embeddingDimension=kb.embedding_dimension,
                status=kb.status,
                documentCount=doc_count,
                createdAt=kb.created_at,
            ).model_dump(mode="json")
        )
    return ApiResponse.success({"items": items}, build_meta(ctx.project_code))


# ============================================================================
# 按 code 获取知识库
# ============================================================================
@router.get(
    "/{code}",
    summary="按 code 获取知识库",
    description="在当前项目下按 code 查询知识库。跨项目查询返回 404，不泄露存在性。",
    response_model=None,
)
async def get_knowledge_base(
    code: str = Path(..., description="知识库编码"),
    ctx: ProjectContext = Depends(get_project_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """按 code 获取知识库详情。

    业务流程：
        1. 调用 KnowledgeBaseRepository.get_by_code（强制 project_id + code 双重过滤）
        2. 不存在或属于其他项目返回 404 KNOWLEDGE_BASE_NOT_FOUND
        3. 返回知识库信息（含 documentCount）

    为什么跨项目查询返回 404 而非 403？
        返回 403 会泄露"该 code 在其他项目中存在"，给攻击者提供枚举线索。
        统一返回 404 不区分"不存在"与"属于其他项目"，符合最小信息泄露原则。

    Args:
        code: 路径参数，知识库编码。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 为知识库信息。
    """
    kb_repo = KnowledgeBaseRepository(db)
    # 双重过滤：project_id + code，跨项目访问返回 None
    kb = await kb_repo.get_by_code(ctx, code)
    if kb is None:
        # 不存在或属于其他项目：统一抛 404，不泄露存在性
        raise KnowledgeBaseNotFoundError(
            f"知识库不存在：{code}",
            details={"field": "code", "value": code},
        )

    # 统计文档数
    doc_count = await kb_repo.count_documents(ctx, kb.id)
    data = KnowledgeBaseResponse(
        id=kb.id,
        code=kb.code,
        name=kb.name,
        description=kb.description,
        embeddingModel=kb.embedding_model,
        embeddingDimension=kb.embedding_dimension,
        status=kb.status,
        documentCount=doc_count,
        createdAt=kb.created_at,
    ).model_dump(mode="json")
    return ApiResponse.success(data, build_meta(ctx.project_code))


# ============================================================================
# 编辑知识库
# ============================================================================
@router.patch(
    "/{code}",
    summary="编辑知识库",
    description="修改知识库 name/description/status，不允许改 code 与 embeddingDimension。",
    response_model=None,
)
async def update_knowledge_base(
    code: str = Path(..., description="知识库编码"),
    payload: KnowledgeBaseUpdateRequest = Body(...),
    ctx: ProjectContext = Depends(require_scopes(SCOPE_KNOWLEDGE_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """编辑知识库。

    业务流程：
        1. 按 code 查询知识库（带 project_id 过滤），不存在返回 404
        2. 收集请求体中非空字段（PATCH 语义）
        3. 显式拒绝修改 code 与 embeddingDimension（请求模型不含这两个字段）
        4. 调用 Repository.update 更新
        5. 返回更新后的知识库

    为什么不允许改 code 与 embeddingDimension？
        - code 是知识库的稳定标识，外部系统通过 code 引用，修改会破坏引用链
        - embeddingDimension 决定向量列类型，修改需重建所有 chunk 的向量，
          等价于重建知识库，应通过删除+新建实现

    Args:
        code: 路径参数，知识库编码。
        payload: 请求体，含 name/description/status 中至少一个。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 为更新后的知识库信息。
    """
    kb_repo = KnowledgeBaseRepository(db)
    kb = await kb_repo.get_by_code(ctx, code)
    if kb is None:
        raise KnowledgeBaseNotFoundError(
            f"知识库不存在：{code}",
            details={"field": "code", "value": code},
        )

    # 收集待更新字段：仅取非 None 值（PATCH 语义）
    fields: dict = {}
    if payload.name is not None:
        fields["name"] = payload.name
    if payload.description is not None:
        fields["description"] = payload.description
    if payload.status is not None:
        fields["status"] = payload.status

    if not fields:
        # 无字段更新：直接返回当前知识库（幂等）
        data = KnowledgeBaseResponse(
            id=kb.id,
            code=kb.code,
            name=kb.name,
            description=kb.description,
            embeddingModel=kb.embedding_model,
            embeddingDimension=kb.embedding_dimension,
            status=kb.status,
            documentCount=None,
            createdAt=kb.created_at,
        ).model_dump(mode="json")
        return ApiResponse.success(data, build_meta(ctx.project_code))

    # 执行更新（code 与 embeddingDimension 不在 fields 中，物理上无法被修改）
    updated = await kb_repo.update(ctx, kb.id, **fields)
    await db.commit()

    data = KnowledgeBaseResponse(
        id=updated.id,
        code=updated.code,
        name=updated.name,
        description=updated.description,
        embeddingModel=updated.embedding_model,
        embeddingDimension=updated.embedding_dimension,
        status=updated.status,
        documentCount=None,
        createdAt=updated.created_at,
    ).model_dump(mode="json")
    return ApiResponse.success(data, build_meta(ctx.project_code))


# ============================================================================
# 删除知识库
# ============================================================================
@router.delete(
    "/{code}",
    summary="删除知识库",
    description="删除空知识库（document_count=0）。非空知识库需先处理内部文档。",
    response_model=None,
)
async def delete_knowledge_base(
    code: str = Path(..., description="知识库编码"),
    ctx: ProjectContext = Depends(require_scopes(SCOPE_KNOWLEDGE_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """删除知识库。

    业务流程：
        1. 按 code 查询知识库，不存在返回 404
        2. 统计文档数，非空返回 VALIDATION_ERROR + "需先处理内部文档"
        3. 调用 Repository.delete 物理删除（级联删除文档与 chunk）
        4. 返回删除结果

    为什么仅允许删除空知识库？
        非空知识库直接删除会导致文档与向量丢失，可能是误操作。
        强制客户端先处理内部文档（迁移或删除），避免数据意外丢失。

    Args:
        code: 路径参数，知识库编码。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 包含 deleted 字段与原知识库 id。
    """
    kb_repo = KnowledgeBaseRepository(db)
    kb = await kb_repo.get_by_code(ctx, code)
    if kb is None:
        raise KnowledgeBaseNotFoundError(
            f"知识库不存在：{code}",
            details={"field": "code", "value": code},
        )

    # 统计文档数：非空知识库禁止删除
    doc_count = await kb_repo.count_documents(ctx, kb.id)
    if doc_count > 0:
        raise ValidationError(
            f"知识库非空，需先处理内部文档（当前 {doc_count} 篇）",
            details={
                "field": "code",
                "value": code,
                "documentCount": doc_count,
            },
        )

    # 执行删除（Repository 内部带 project_id 过滤）
    deleted = await kb_repo.delete(ctx, kb.id)
    await db.commit()

    data = {
        "deleted": deleted,
        "id": kb.id,
        "code": kb.code,
    }
    return ApiResponse.success(data, build_meta(ctx.project_code))


# ============================================================================
# 停用知识库
# ============================================================================
@router.post(
    "/{code}/disable",
    summary="停用知识库",
    description="将知识库 status 设为 disabled，停用后不参与检索。",
    response_model=None,
)
async def disable_knowledge_base(
    code: str = Path(..., description="知识库编码"),
    ctx: ProjectContext = Depends(require_scopes(SCOPE_KNOWLEDGE_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """停用知识库。

    业务流程：
        1. 按 code 查询知识库，不存在返回 404
        2. 设置 status=disabled
        3. 返回更新后的知识库

    停用与删除的区别：
        - 停用：status=disabled，知识库与文档保留，仅不参与检索
        - 删除：物理删除，知识库与文档永久丢失

    Args:
        code: 路径参数，知识库编码。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 为更新后的知识库信息。
    """
    kb_repo = KnowledgeBaseRepository(db)
    kb = await kb_repo.get_by_code(ctx, code)
    if kb is None:
        raise KnowledgeBaseNotFoundError(
            f"知识库不存在：{code}",
            details={"field": "code", "value": code},
        )

    # 设置 status=disabled
    updated = await kb_repo.set_status(ctx, kb.id, "disabled")
    await db.commit()

    data = KnowledgeBaseResponse(
        id=updated.id,
        code=updated.code,
        name=updated.name,
        description=updated.description,
        embeddingModel=updated.embedding_model,
        embeddingDimension=updated.embedding_dimension,
        status=updated.status,
        documentCount=None,
        createdAt=updated.created_at,
    ).model_dump(mode="json")
    return ApiResponse.success(data, build_meta(ctx.project_code))


# ============================================================================
# 启用知识库
# ============================================================================
@router.post(
    "/{code}/enable",
    summary="启用知识库",
    description="将知识库 status 设为 active，恢复参与检索。",
    response_model=None,
)
async def enable_knowledge_base(
    code: str = Path(..., description="知识库编码"),
    ctx: ProjectContext = Depends(require_scopes(SCOPE_KNOWLEDGE_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """启用知识库。

    业务流程：
        1. 按 code 查询知识库，不存在返回 404
        2. 设置 status=active
        3. 返回更新后的知识库

    Args:
        code: 路径参数，知识库编码。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 为更新后的知识库信息。
    """
    kb_repo = KnowledgeBaseRepository(db)
    kb = await kb_repo.get_by_code(ctx, code)
    if kb is None:
        raise KnowledgeBaseNotFoundError(
            f"知识库不存在：{code}",
            details={"field": "code", "value": code},
        )

    # 设置 status=active
    updated = await kb_repo.set_status(ctx, kb.id, "active")
    await db.commit()

    data = KnowledgeBaseResponse(
        id=updated.id,
        code=updated.code,
        name=updated.name,
        description=updated.description,
        embeddingModel=updated.embedding_model,
        embeddingDimension=updated.embedding_dimension,
        status=updated.status,
        documentCount=None,
        createdAt=updated.created_at,
    ).model_dump(mode="json")
    return ApiResponse.success(data, build_meta(ctx.project_code))


# ============================================================================
# SubTask 8.1：文件上传接口
# ============================================================================
@router.post(
    "/{code}/documents/file",
    summary="上传文件到知识库",
    description=(
        "将 PDF/DOCX/TXT/MD 文件上传到指定知识库，触发异步入库流程。"
        "支持 Idempotency-Key 头实现 24h 幂等；相同内容（content_hash）已存在时直接返回。"
    ),
    response_model=None,
)
async def upload_document_file(
    code: str = Path(..., description="知识库编码"),
    file: UploadFile = File(..., description="待上传文件，支持 PDF/DOCX/TXT/MD，≤20MB"),
    title: str | None = Form(default=None, description="文档标题，未传时用文件名"),
    tags: str | None = Form(default=None, description='标签 JSON 字符串数组，如 ["基金","投资"]'),
    externalId: str | None = Form(default=None, description="业务项目稳定资源 ID"),
    metadata: str | None = Form(default=None, description="元数据 JSON 对象"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: ProjectContext = Depends(require_scopes(SCOPE_KNOWLEDGE_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """上传文件到知识库（multipart/form-data）。

    业务流程：
        1. 校验知识库存在且属于当前项目
        2. 校验文件类型（MIME 或扩展名）与大小（≤20MB）
        3. 边读文件流边计算 SHA-256（content_hash），避免重复读文件
        4. 幂等校验：若提供 Idempotency-Key，对比 request_hash
           - 相同 Key + 相同内容 → 重放原响应
           - 相同 Key + 不同内容 → 抛 IdempotencyConflictError（409）
        5. 去重查询：相同 content_hash 已存在且未删除 → 返回已存在文档
        6. 写入对象存储：``projects/{project_id}/{kb_id}/{doc_id}{ext}``
        7. 创建 Document 记录（source_type='file'）
        8. 创建 IngestionJob 记录（status='pending'）
        9. 触发 Celery 任务 ``process_document.delay(...)``
        10. 返回 ``{ documentId, ingestionTaskId, status: "PENDING" }``

    为什么边读边算 SHA-256？
        文件可能较大（≤20MB），若先读完再算哈希需双倍内存；
        边读边算用流式哈希，内存占用恒定（仅一个 chunk 缓冲区）。

    为什么文件存储路径以 ``projects/{project_id}/`` 为前缀？
        存储层项目隔离：即使其他项目误传相同 document_id 也写入不同物理目录，
        物理隔离杜绝跨项目文件覆盖，详见 ``_build_storage_key`` 注释。

    Args:
        code: 路径参数，知识库编码。
        file: 上传文件对象。
        title: 文档标题，未传时用文件名（去扩展名）。
        tags: 标签 JSON 字符串，如 ``["基金","投资"]``。
        externalId: 业务项目稳定资源 ID，可空。
        metadata: 元数据 JSON 对象，可空。
        idempotency_key: 幂等键，可空；提供时启用 24h 幂等校验。
        ctx: 项目上下文（由 require_scopes 校验 Scope 后返回）。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 为 ``{ documentId, ingestionTaskId, status }``。
    """
    # ------------------------------------------------------------------
    # 步骤 1：校验知识库存在且属于当前项目
    # ------------------------------------------------------------------
    kb_repo = KnowledgeBaseRepository(db)
    # 双重过滤：project_id + code，跨项目访问返回 None
    kb = await kb_repo.get_by_code(ctx, code)
    if kb is None:
        # 不存在或属于其他项目：统一抛 404，不泄露存在性
        raise KnowledgeBaseNotFoundError(
            f"知识库不存在：{code}",
            details={"field": "code", "value": code},
        )

    # ------------------------------------------------------------------
    # 步骤 2：校验文件类型与大小
    # ------------------------------------------------------------------
    # 优先用 MIME 类型判断，缺失时按扩展名兜底
    mime_type = (file.content_type or "").lower()
    filename = file.filename or "untitled"
    # 取小写扩展名（含点），如 ``.pdf``
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if mime_type in _ALLOWED_MIME_TYPES:
        # MIME 命中：以映射值为准（部分客户端可能传 text/plain 但实际是 .md）
        canonical_ext = _ALLOWED_MIME_TYPES[mime_type]
        if not ext:
            ext = canonical_ext
    elif ext in _ALLOWED_EXTENSIONS:
        # MIME 缺失或不命中但扩展名合法：保留 ext
        pass
    else:
        # 类型不支持：返回 422，列出允许的类型便于客户端排查
        raise ValidationError(
            f"不支持的文件类型：{mime_type or ext or '未知'}，仅支持 PDF/DOCX/TXT/MD",
            details={
                "field": "file",
                "filename": filename,
                "mime_type": mime_type,
                "ext": ext,
                "allowed": list(_ALLOWED_MIME_TYPES.keys()),
            },
        )

    # ------------------------------------------------------------------
    # 步骤 3：边读文件流边计算 SHA-256 与累计大小
    # ------------------------------------------------------------------
    sha = hashlib.sha256()
    file_bytes = bytearray()
    total_size = 0
    # 64KB 缓冲区：平衡内存占用与 IO 次数
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        sha.update(chunk)  # 流式更新哈希
        file_bytes.extend(chunk)
        total_size += len(chunk)
        # 大小超限：提前拒绝，避免读完整个文件
        if total_size > _MAX_FILE_SIZE_BYTES:
            raise ValidationError(
                f"文件大小超过限制（{_MAX_FILE_SIZE_BYTES // 1024 // 1024}MB）",
                details={
                    "field": "file",
                    "filename": filename,
                    "size": total_size,
                    "limit": _MAX_FILE_SIZE_BYTES,
                },
            )

    content_hash = sha.hexdigest()
    file_content = bytes(file_bytes)

    # ------------------------------------------------------------------
    # 步骤 4：解析 tags / metadata JSON 字符串
    # ------------------------------------------------------------------
    parsed_tags = _parse_tags(tags)
    parsed_metadata = _parse_metadata(metadata)

    # 标题未传时用文件名（去扩展名）作为标题
    doc_title = title or (filename.rsplit(".", 1)[0] if "." in filename else filename)

    # ------------------------------------------------------------------
    # 步骤 5：幂等校验
    # ------------------------------------------------------------------
    # 计算文件上传请求哈希：含文件名/大小/content_hash/title/tags
    request_hash = compute_file_request_hash(
        filename=filename,
        file_size=total_size,
        content_hash=content_hash,
        title=doc_title,
        tags=parsed_tags,
    )
    # check_idempotency 返回 None 表示首次或未提供 Key；
    # 返回 dict 表示重放原响应，直接返回
    replayed = await check_idempotency(idempotency_key, request_hash)
    if replayed is not None:
        # 重放：直接返回缓存的响应，不再处理
        return ApiResponse.success(replayed, build_meta(ctx.project_code))

    # ------------------------------------------------------------------
    # 步骤 6：去重查询（content_hash）
    # ------------------------------------------------------------------
    doc_repo = DocumentRepository(db)
    existing_doc = await doc_repo.get_by_content_hash(ctx, kb.id, content_hash)
    if existing_doc is not None:
        # 已存在相同内容文档：构造响应并写入幂等记录后返回
        # 查询关联的 IngestionJob（取最新一个）
        ingestion_repo = IngestionJobRepository(db)
        jobs = await ingestion_repo.get_by_document(ctx, existing_doc.id)
        ingestion_job_id = jobs[0].id if jobs else ""
        data = _build_import_response(existing_doc.id, ingestion_job_id)
        # 写入幂等记录，便于后续重放
        if idempotency_key is not None:
            await set_idempotency_record(idempotency_key, request_hash, data)
        return ApiResponse.success(data, build_meta(ctx.project_code))

    # ------------------------------------------------------------------
    # 步骤 7：写入对象存储
    # ------------------------------------------------------------------
    # 先创建 Document 拿到 document_id（数据库 gen_random_uuid 生成），
    # 再用 document_id 拼接 storage_key 写入对象存储，
    # 最后回填 storage_key 到 Document 记录。
    doc = await doc_repo.create(
        ctx,
        knowledge_base_id=kb.id,
        source_type="file",
        title=doc_title,
        mime_type=mime_type or None,
        content_hash=content_hash,
        processing_status="pending",  # 初始状态：待处理
        external_id=externalId,
        # metadata_ 是 SQLAlchemy 列名（避开保留字 metadata）
        **{"metadata_": _merge_tags_to_metadata(parsed_tags, parsed_metadata)},
    )

    # 构造存储路径：projects/{project_id}/{kb_id}/{document_id}{ext}
    storage_key = _build_storage_key(ctx.project_id, kb.id, doc.id, ext)
    storage = get_object_storage()
    try:
        storage.save(storage_key, file_content)
    except Exception as exc:
        # 存储失败：回滚事务（Document 记录一并回滚），抛 500
        await db.rollback()
        raise RuntimeError(f"文件存储失败：{exc}") from exc

    # 回填 storage_key
    await doc_repo.update(ctx, doc.id, storage_key=storage_key)

    # ------------------------------------------------------------------
    # 步骤 8：创建 IngestionJob（status=pending）
    # ------------------------------------------------------------------
    ingestion_repo = IngestionJobRepository(db)
    job = await ingestion_repo.create(ctx, document_id=doc.id)

    # 提交事务：Document + storage_key 回填 + IngestionJob 一起落库
    await db.commit()

    # ------------------------------------------------------------------
    # 步骤 9：触发 Celery 任务（异步处理）
    # ------------------------------------------------------------------
    # 注意：触发 Celery 任务在 commit 之后，确保 Worker 能查询到 Document 与 Job
    # 若触发失败，Document 已落库，Worker 可通过扫描 pending 文档补偿处理
    process_document.delay(ctx.project_id, doc.id, job.id)

    # ------------------------------------------------------------------
    # 步骤 10：构造响应并写入幂等记录
    # ------------------------------------------------------------------
    data = _build_import_response(doc.id, job.id)
    if idempotency_key is not None:
        # 处理成功后写入幂等记录，便于后续重放
        await set_idempotency_record(idempotency_key, request_hash, data)

    return ApiResponse.success(data, build_meta(ctx.project_code))


# ============================================================================
# SubTask 8.2：文本/URL 写入接口
# ============================================================================
@router.post(
    "/{code}/documents",
    summary="写入文本或 URL 到知识库",
    description=(
        "将 TEXT 文本或 URL 写入知识库，触发异步入库流程。"
        "TEXT 直接以 content 作为正文；URL 在 ingestion 任务中抓取。"
        "支持 Idempotency-Key 头实现 24h 幂等；相同内容（content_hash）已存在时直接返回。"
    ),
    response_model=None,
)
async def create_document(
    code: str = Path(..., description="知识库编码"),
    payload: DocumentCreateRequest = Body(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: ProjectContext = Depends(require_scopes(SCOPE_KNOWLEDGE_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """写入文本或 URL 到知识库（application/json）。

    业务流程：
        1. 校验知识库存在且属于当前项目
        2. 校验请求体：type=TEXT 必填 content；type=URL 必填 url
        3. 计算 content_hash：
           - TEXT：对 content 计算 SHA-256
           - URL：对 url 计算 SHA-256（暂存，真正抓取在 ingestion 任务中）
        4. 幂等校验（同 8.1）
        5. 去重查询（同 8.1）
        6. 创建 Document（source_type='manual' 或 'url'）
        7. 创建 IngestionJob
        8. 触发 Celery 任务
        9. 返回 ``{ documentId, ingestionTaskId, status: "PENDING" }``

    为什么 URL 不立即抓取？
        抓取可能耗时（网络 IO、解析），同步等待会拖慢 API 响应。
        将抓取放到 ingestion 任务中异步执行，API 仅创建记录并立即返回，
        客户端通过文档状态查询接口轮询处理进度。

    Args:
        code: 路径参数，知识库编码。
        payload: 请求体，含 type/title/content/url/externalId/tags/metadata。
        idempotency_key: 幂等键，可空。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 为 ``{ documentId, ingestionTaskId, status }``。
    """
    # ------------------------------------------------------------------
    # 步骤 1：校验知识库
    # ------------------------------------------------------------------
    kb_repo = KnowledgeBaseRepository(db)
    kb = await kb_repo.get_by_code(ctx, code)
    if kb is None:
        raise KnowledgeBaseNotFoundError(
            f"知识库不存在：{code}",
            details={"field": "code", "value": code},
        )

    # ------------------------------------------------------------------
    # 步骤 2：校验请求体字段
    # ------------------------------------------------------------------
    if payload.type == "TEXT":
        if not payload.content or not payload.content.strip():
            raise ValidationError(
                "type=TEXT 时 content 必填且不能为空",
                details={"field": "content"},
            )
    elif payload.type == "URL":
        if not payload.url or not payload.url.strip():
            raise ValidationError(
                "type=URL 时 url 必填且不能为空",
                details={"field": "url"},
            )

    # ------------------------------------------------------------------
    # 步骤 3：计算 content_hash
    # ------------------------------------------------------------------
    if payload.type == "TEXT":
        # TEXT：对 content 计算 SHA-256
        content_bytes = payload.content.encode("utf-8")
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        source_type = "manual"  # 手动录入
        source_url = None
    else:
        # URL：暂存 url 哈希，真正内容哈希在抓取后由 Worker 更新
        url_bytes = payload.url.encode("utf-8")
        content_hash = hashlib.sha256(url_bytes).hexdigest()
        source_type = "url"
        source_url = payload.url

    # ------------------------------------------------------------------
    # 步骤 4：幂等校验
    # ------------------------------------------------------------------
    # 文本/URL 写入用 JSON 请求体计算哈希
    request_body = payload.model_dump(mode="json")
    request_hash = compute_request_hash(request_body)
    replayed = await check_idempotency(idempotency_key, request_hash)
    if replayed is not None:
        return ApiResponse.success(replayed, build_meta(ctx.project_code))

    # ------------------------------------------------------------------
    # 步骤 5：去重查询
    # ------------------------------------------------------------------
    doc_repo = DocumentRepository(db)
    existing_doc = await doc_repo.get_by_content_hash(ctx, kb.id, content_hash)
    if existing_doc is not None:
        ingestion_repo = IngestionJobRepository(db)
        jobs = await ingestion_repo.get_by_document(ctx, existing_doc.id)
        ingestion_job_id = jobs[0].id if jobs else ""
        data = _build_import_response(existing_doc.id, ingestion_job_id)
        if idempotency_key is not None:
            await set_idempotency_record(idempotency_key, request_hash, data)
        return ApiResponse.success(data, build_meta(ctx.project_code))

    # ------------------------------------------------------------------
    # 步骤 6：创建 Document
    # ------------------------------------------------------------------
    # TEXT 类型不写 storage_key（无文件），URL 类型 storage_key 在抓取后填充
    # TEXT 类型把原始正文存入 metadata_.content，供 Worker 解析阶段读取
    doc_metadata = _merge_tags_to_metadata(payload.tags, payload.metadata)
    if payload.type == "TEXT":
        doc_metadata["content"] = payload.content
    doc = await doc_repo.create(
        ctx,
        knowledge_base_id=kb.id,
        source_type=source_type,
        title=payload.title,
        source_url=source_url,
        content_hash=content_hash,
        processing_status="pending",
        external_id=payload.externalId,
        # tags 与 metadata 合并存储到 metadata_ 字段
        **{"metadata_": doc_metadata},
    )

    # ------------------------------------------------------------------
    # 步骤 7：创建 IngestionJob
    # ------------------------------------------------------------------
    ingestion_repo = IngestionJobRepository(db)
    job = await ingestion_repo.create(ctx, document_id=doc.id)

    # 提交事务：Document + IngestionJob 一起落库
    await db.commit()

    # ------------------------------------------------------------------
    # 步骤 8：触发 Celery 任务
    # ------------------------------------------------------------------
    process_document.delay(ctx.project_id, doc.id, job.id)

    # ------------------------------------------------------------------
    # 步骤 9：构造响应并写入幂等记录
    # ------------------------------------------------------------------
    data = _build_import_response(doc.id, job.id)
    if idempotency_key is not None:
        await set_idempotency_record(idempotency_key, request_hash, data)

    return ApiResponse.success(data, build_meta(ctx.project_code))


# ============================================================================
# SubTask 8.3：文档状态查询接口
# ============================================================================
@documents_router.get(
    "/{documentId}",
    summary="查询文档处理状态",
    description=(
        "查询文档当前处理状态、所属知识库、分块数等信息。"
        "跨项目查询返回 404 TASK_NOT_FOUND，不泄露文档是否存在。"
    ),
    response_model=None,
)
async def get_document_status(
    documentId: str = Path(..., description="文档 ID"),
    ctx: ProjectContext = Depends(require_scopes(SCOPE_TASKS_READ)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """查询文档处理状态。

    业务流程：
        1. 按 documentId 查询文档（强制 project_id 过滤）
        2. 不存在或属于其他项目 → 返回 404 TASK_NOT_FOUND
        3. 查询关联的最新 IngestionJob（取 created_at 最新）
        4. 统计文档的 chunk 数量
        5. 查询文档所属知识库的 code（便于客户端关联）
        6. 返回文档详情（含状态映射为大写形式）

    为什么跨项目查询返回 404 而非 403？
        返回 403 会泄露"该 documentId 在其他项目中存在"，给攻击者提供枚举线索。
        统一返回 404 不区分"不存在"与"属于其他项目"，符合最小信息泄露原则。

    状态映射：
        内部状态 pending/parsing/chunking/embedding/ready/failed
        → 对外 PENDING/PARSING/CHUNKING/EMBEDDING/READY/FAILED

    Args:
        documentId: 路径参数，文档 ID。
        ctx: 项目上下文（需 tasks:read Scope）。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 为 DocumentResponse 序列化结果。
    """
    # ------------------------------------------------------------------
    # 步骤 1：查询文档（强制 project_id 过滤）
    # ------------------------------------------------------------------
    doc_repo = DocumentRepository(db)
    # 双重过滤：id + project_id，跨项目访问返回 None
    doc = await doc_repo.get_by_id(ctx, documentId)
    if doc is None:
        # 不存在或属于其他项目：统一抛 404 TASK_NOT_FOUND
        raise TaskNotFoundError(
            f"文档不存在：{documentId}",
            details={"field": "documentId", "value": documentId},
        )

    # ------------------------------------------------------------------
    # 步骤 2：查询关联的最新 IngestionJob
    # ------------------------------------------------------------------
    ingestion_repo = IngestionJobRepository(db)
    # get_by_document 返回按 created_at 倒序的列表，取第一个即最新
    jobs = await ingestion_repo.get_by_document(ctx, doc.id)
    latest_job = jobs[0] if jobs else None
    # ingestion_task_id 可空（文档创建后 Job 尚未创建的边界情况）
    ingestion_task_id = latest_job.id if latest_job else None

    # ------------------------------------------------------------------
    # 步骤 3：统计 chunk 数量
    # ------------------------------------------------------------------
    chunk_repo = DocumentChunkRepository(db)
    chunks = await chunk_repo.list_by_document(ctx, doc.id)
    chunk_count = len(chunks)

    # ------------------------------------------------------------------
    # 步骤 4：查询所属知识库 code
    # ------------------------------------------------------------------
    kb_repo = KnowledgeBaseRepository(db)
    # 强制 project_id 过滤，避免查到其他项目的知识库
    kb = await kb_repo.get_by_id(ctx, doc.knowledge_base_id)
    # 知识库可能已被删除（理论上外键约束保证不会），此处兜底
    kb_code = kb.code if kb else ""

    # ------------------------------------------------------------------
    # 步骤 5：构造响应（状态映射为大写形式）
    # ------------------------------------------------------------------
    data = DocumentResponse(
        documentId=doc.id,
        title=doc.title,
        sourceType=doc.source_type,
        processingStatus=_map_processing_status(doc.processing_status),
        ingestionTaskId=ingestion_task_id,
        knowledgeBaseCode=kb_code,
        chunkCount=chunk_count,
        enabled=doc.enabled,
        createdAt=doc.created_at,
        updatedAt=doc.updated_at,
    ).model_dump(mode="json")
    return ApiResponse.success(data, build_meta(ctx.project_code))


def _merge_tags_to_metadata(
    tags: list[str] | None,
    metadata: dict | None,
) -> dict | None:
    """将 tags 与 metadata 合并为单个 dict 存入 Document.metadata_。

    Document 模型只有 ``metadata_`` 一个 JSONB 字段，
    tags 单独存储需新增列，为简化表结构，将 tags 嵌入 metadata 中：
    ``{"tags": ["基金","投资"], ...其他元数据}``

    Args:
        tags: 标签列表，可空。
        metadata: 元数据 dict，可空。

    Returns:
        合并后的 dict；两者都为空时返回 None。
    """
    if not tags and not metadata:
        return None
    merged: dict = {}
    if metadata:
        merged.update(metadata)
    if tags:
        merged["tags"] = tags
    return merged or None
