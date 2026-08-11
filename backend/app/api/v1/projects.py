"""项目管理接口（管理密钥保护，不含 project_id 过滤）。

对应 SubTask 7.1 与 7.2：
- 项目 CRUD：POST / GET / PATCH /api/v1/projects，以及 enable/disable
- 项目级 API Key 管理：POST/GET /api/v1/projects/{projectId}/api-keys，
  DELETE /api/v1/projects/{projectId}/api-keys/{keyId}，
  POST /api/v1/projects/{projectId}/api-keys/{keyId}/rotate

设计要点
--------
1. 所有接口通过 ``Depends(get_management_api_key)`` 校验管理密钥，
   不构造 ProjectContext（项目管理接口不关联具体项目身份）。
2. ``code`` 创建后不可改：PATCH 接口的请求模型中不含 ``code`` 字段，
   端点也显式拒绝任何对 code 的修改尝试。
3. 创建项目时自动创建 ``ProjectSettings``（默认值），保证后续业务接口能读取到设置。
4. API Key 明文仅创建/轮换时返回一次，列表查询不返回明文与哈希。
5. 创建 API Key 时使用 ``generate_api_key()`` 生成明文+前缀+哈希，仅存哈希。
6. 轮换 API Key 时旧 Key 设为 revoked，生成新 Key，新明文仅此一次返回。
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_management_api_key
from app.api.v1.schemas import (
    ApiKeyCreateRequest,
    ApiKeyResponse,
    ProjectCreateRequest,
    ProjectResponse,
    ProjectUpdateRequest,
)
from app.core.exceptions import ValidationError
from app.core.project_context import ProjectContext
from app.core.response import ApiResponse, build_meta
from app.core.security import generate_api_key
from app.db.repositories.project import (
    ApiKeyRepository,
    ProjectRepository,
    ProjectSettingsRepository,
)
from app.db.session import get_db

# 项目编码格式正则：小写字母+数字+连字符，3-32 字符，首尾需为字母或数字
# 用于 POST /projects 创建时的 code 格式校验
_PROJECT_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$")

router = APIRouter(prefix="/projects", tags=["项目管理"])


# ============================================================================
# 工具函数：构造 ProjectContext（管理接口专用）
# ============================================================================
def _build_management_ctx(project_id: str, project_code: str) -> ProjectContext:
    """为管理接口构造临时 ProjectContext。

    管理接口使用管理密钥（不关联具体项目），但 ApiKeyRepository / ProjectSettingsRepository
    的方法签名要求传入 ProjectContext。这里构造一个临时 ctx 仅填充 project_id 与 project_code，
    environment 与 scopes 留默认值（管理操作不涉及这些字段）。

    Args:
        project_id: 目标项目 ID（已从数据库查询确认存在）。
        project_code: 目标项目编码。

    Returns:
        临时 ProjectContext，仅用于 Repository 调用。
    """
    return ProjectContext(
        project_id=project_id,
        project_code=project_code,
    )


# ============================================================================
# SubTask 7.1：项目管理接口
# ============================================================================
@router.post(
    "",
    summary="创建项目",
    description=(
        "创建新项目，并自动初始化 ProjectSettings（默认值）。"
        "仅管理密钥可调用，code 创建后不可改。"
    ),
    response_model=None,
)
async def create_project(
    payload: ProjectCreateRequest = Body(...),
    _: str = Depends(get_management_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """创建项目。

    业务流程：
        1. 校验 code 格式（正则：小写字母+数字+连字符，3-32 字符）
        2. 通过 ProjectRepository.create 落库（flush 触发 UNIQUE 约束检查）
        3. 若 code 重复，IntegrityError 转为 VALIDATION_ERROR + details
        4. 创建关联 ProjectSettings（使用默认值，便于后续业务读取）
        5. 提交事务并返回项目数据

    Args:
        payload: 请求体，包含 code / name / description。
        _: 管理密钥依赖（仅触发校验，不使用返回值）。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 为新建项目信息。
    """
    # ------------------------------------------------------------------
    # 步骤 1：校验 code 格式
    # ------------------------------------------------------------------
    if not _PROJECT_CODE_PATTERN.match(payload.code):
        # 格式非法：返回 VALIDATION_ERROR，details 列出规则便于客户端修正
        raise ValidationError(
            "项目编码格式非法：需为 3-32 字符的小写字母+数字+连字符，首尾需为字母或数字",
            details={"field": "code", "value": payload.code},
        )

    # ------------------------------------------------------------------
    # 步骤 2：落库项目（flush 触发 UNIQUE 约束）
    # ------------------------------------------------------------------
    project_repo = ProjectRepository(db)
    try:
        project = await project_repo.create(
            code=payload.code,
            name=payload.name,
            description=payload.description,
        )
        # 创建关联 ProjectSettings（默认值），保证后续业务接口能读到设置
        # ProjectSettings 字段在 ORM 中有 server_default，这里只需传 project_id
        ctx = _build_management_ctx(project.id, project.code)
        settings_repo = ProjectSettingsRepository(db)
        await settings_repo.upsert(ctx)
        # 提交事务：项目与设置一并落库
        await db.commit()
    except IntegrityError as exc:
        # UNIQUE 约束冲突：code 已存在，回滚并返回 VALIDATION_ERROR
        await db.rollback()
        raise ValidationError(
            f"项目编码已存在：{payload.code}",
            details={"field": "code", "value": payload.code},
        ) from exc

    # ------------------------------------------------------------------
    # 步骤 3：构造响应（管理接口无项目上下文，meta.projectCode 为 None）
    # ------------------------------------------------------------------
    data = ProjectResponse(
        id=project.id,
        code=project.code,
        name=project.name,
        description=project.description,
        status=project.status,
        createdAt=project.created_at,
    ).model_dump(mode="json")
    return ApiResponse.success(data, build_meta(None))


@router.get(
    "",
    summary="列出所有项目",
    description="按状态过滤列出全部项目，仅管理密钥可调用。",
    response_model=None,
)
async def list_projects(
    status: str | None = Query(default=None, description="状态过滤：active / disabled"),
    _: str = Depends(get_management_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """列出所有项目。

    业务流程：
        1. 校验 status 参数（若传入）为 active / disabled
        2. 调用 ProjectRepository.list_all 查询
        3. 返回项目列表（精简字段：id / code / name / status / createdAt）

    Args:
        status: 状态过滤，可空。
        _: 管理密钥依赖。
        db: 异步数据库会话。

    Returns:
        标准响应体，data.items 为项目列表。
    """
    # 校验 status 枚举值
    if status is not None and status not in ("active", "disabled"):
        raise ValidationError(
            "status 参数非法，仅允许 active 或 disabled",
            details={"field": "status", "value": status},
        )

    # 查询项目列表
    project_repo = ProjectRepository(db)
    projects = await project_repo.list_all(status_filter=status)

    # 构造精简响应（列表不需要 description，减少响应体积）
    items = [
        {
            "id": p.id,
            "code": p.code,
            "name": p.name,
            "status": p.status,
            "createdAt": p.created_at.isoformat() if p.created_at else None,
        }
        for p in projects
    ]
    return ApiResponse.success({"items": items}, build_meta(None))


@router.get(
    "/{projectId}",
    summary="获取项目详情",
    description="返回项目基本信息与 ProjectSettings 配置。",
    response_model=None,
)
async def get_project(
    projectId: str = Path(..., description="项目 ID（UUID）"),
    _: str = Depends(get_management_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取项目详情（含 settings）。

    业务流程：
        1. 按 ID 查询项目，不存在返回 404
        2. 查询关联 ProjectSettings，可能为 None（理论上不应，创建时已建）
        3. 返回项目 + settings

    Args:
        projectId: 路径参数，项目 ID。
        _: 管理密钥依赖。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 包含 project 与 settings 字段。
    """
    project_repo = ProjectRepository(db)
    project = await project_repo.get_by_id(projectId)
    if project is None:
        # 项目不存在：复用 KnowledgeBaseNotFoundError 不合适，这里用 ValidationError 兜底
        # 实际生产环境应定义 ProjectNotFoundError，本任务范围内使用通用错误
        raise ValidationError(
            f"项目不存在：{projectId}",
            details={"field": "projectId", "value": projectId},
        )

    # 查询关联 ProjectSettings
    ctx = _build_management_ctx(project.id, project.code)
    settings_repo = ProjectSettingsRepository(db)
    settings = await settings_repo.get_by_project(ctx)

    # 构造响应
    data = {
        "project": ProjectResponse(
            id=project.id,
            code=project.code,
            name=project.name,
            description=project.description,
            status=project.status,
            createdAt=project.created_at,
        ).model_dump(mode="json"),
        "settings": _serialize_settings(settings),
    }
    return ApiResponse.success(data, build_meta(None))


@router.patch(
    "/{projectId}",
    summary="编辑项目",
    description="修改项目 name/description/status，不允许改 code。",
    response_model=None,
)
async def update_project(
    projectId: str = Path(..., description="项目 ID（UUID）"),
    payload: ProjectUpdateRequest = Body(...),
    _: str = Depends(get_management_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """编辑项目。

    业务流程：
        1. 查询项目是否存在，不存在返回 404
        2. 收集请求体中非空字段（PATCH 语义：仅更新传入字段）
        3. 显式拒绝任何对 code 的修改（请求模型不含 code，双保险）
        4. 调用 ProjectRepository.update 更新
        5. 返回更新后的项目

    Args:
        projectId: 路径参数，项目 ID。
        payload: 请求体，包含 name/description/status 中至少一个。
        _: 管理密钥依赖。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 为更新后的项目信息。
    """
    # 查询项目是否存在
    project_repo = ProjectRepository(db)
    project = await project_repo.get_by_id(projectId)
    if project is None:
        raise ValidationError(
            f"项目不存在：{projectId}",
            details={"field": "projectId", "value": projectId},
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
        # 无字段更新：直接返回当前项目（幂等）
        data = ProjectResponse(
            id=project.id,
            code=project.code,
            name=project.name,
            description=project.description,
            status=project.status,
            createdAt=project.created_at,
        ).model_dump(mode="json")
        return ApiResponse.success(data, build_meta(None))

    # 执行更新（code 不在 fields 中，物理上无法被修改）
    updated = await project_repo.update(projectId, **fields)
    await db.commit()

    data = ProjectResponse(
        id=updated.id,
        code=updated.code,
        name=updated.name,
        description=updated.description,
        status=updated.status,
        createdAt=updated.created_at,
    ).model_dump(mode="json")
    return ApiResponse.success(data, build_meta(None))


@router.post(
    "/{projectId}/disable",
    summary="停用项目",
    description="将项目 status 设为 disabled，停用后 API Key 调用业务接口返回 403。",
    response_model=None,
)
async def disable_project(
    projectId: str = Path(..., description="项目 ID（UUID）"),
    _: str = Depends(get_management_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """停用项目。

    业务流程：
        1. 查询项目是否存在
        2. 设置 status=disabled
        3. 返回更新后的项目

    Args:
        projectId: 路径参数，项目 ID。
        _: 管理密钥依赖。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 为更新后的项目信息。
    """
    project_repo = ProjectRepository(db)
    project = await project_repo.get_by_id(projectId)
    if project is None:
        raise ValidationError(
            f"项目不存在：{projectId}",
            details={"field": "projectId", "value": projectId},
        )

    # 设置 status=disabled
    updated = await project_repo.set_status(projectId, "disabled")
    await db.commit()

    data = ProjectResponse(
        id=updated.id,
        code=updated.code,
        name=updated.name,
        description=updated.description,
        status=updated.status,
        createdAt=updated.created_at,
    ).model_dump(mode="json")
    return ApiResponse.success(data, build_meta(None))


@router.post(
    "/{projectId}/enable",
    summary="启用项目",
    description="将项目 status 设为 active，恢复 API Key 调用能力。",
    response_model=None,
)
async def enable_project(
    projectId: str = Path(..., description="项目 ID（UUID）"),
    _: str = Depends(get_management_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """启用项目。

    业务流程：
        1. 查询项目是否存在
        2. 设置 status=active
        3. 返回更新后的项目

    Args:
        projectId: 路径参数，项目 ID。
        _: 管理密钥依赖。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 为更新后的项目信息。
    """
    project_repo = ProjectRepository(db)
    project = await project_repo.get_by_id(projectId)
    if project is None:
        raise ValidationError(
            f"项目不存在：{projectId}",
            details={"field": "projectId", "value": projectId},
        )

    # 设置 status=active
    updated = await project_repo.set_status(projectId, "active")
    await db.commit()

    data = ProjectResponse(
        id=updated.id,
        code=updated.code,
        name=updated.name,
        description=updated.description,
        status=updated.status,
        createdAt=updated.created_at,
    ).model_dump(mode="json")
    return ApiResponse.success(data, build_meta(None))


# ============================================================================
# SubTask 7.2：API Key 管理接口（项目子路由）
# ============================================================================
@router.post(
    "/{projectId}/api-keys",
    summary="创建 API Key",
    description=(
        "为指定项目生成新 API Key。明文 Key 仅在此响应返回一次，"
        "客户端必须立即保存。服务端仅存哈希。"
    ),
    response_model=None,
)
async def create_api_key(
    projectId: str = Path(..., description="项目 ID（UUID）"),
    payload: ApiKeyCreateRequest = Body(...),
    _: str = Depends(get_management_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """创建 API Key。

    业务流程：
        1. 查询项目是否存在
        2. 调用 generate_api_key() 生成明文 + 前缀 + 哈希
        3. 通过 ApiKeyRepository.create 落库（仅存哈希）
        4. 返回 Key 信息，明文 Key 字段名 plaintextKey，仅展示一次

    安全说明：
        - 明文 Key 不入库，仅在响应中返回一次
        - 哈希使用 Argon2id（参见 core/security.py）
        - key_prefix 用于后台识别与候选定位，不参与鉴权

    Args:
        projectId: 路径参数，项目 ID。
        payload: 请求体，包含 name / environment / scopes / expiresAt。
        _: 管理密钥依赖。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 含 plaintextKey（明文，仅展示一次）。
    """
    # 查询项目是否存在
    project_repo = ProjectRepository(db)
    project = await project_repo.get_by_id(projectId)
    if project is None:
        raise ValidationError(
            f"项目不存在：{projectId}",
            details={"field": "projectId", "value": projectId},
        )

    # 生成明文 Key + 前缀 + 哈希
    # 返回三元组 (raw_key, key_prefix, key_hash)
    raw_key, key_prefix, key_hash = generate_api_key()

    # 构造管理用的临时 ProjectContext（仅填充 project_id / project_code）
    ctx = _build_management_ctx(project.id, project.code)

    # 落库 API Key（仅存哈希，明文不入库）
    api_key_repo = ApiKeyRepository(db)
    api_key = await api_key_repo.create(
        ctx=ctx,
        name=payload.name,
        environment=payload.environment,
        key_prefix=key_prefix,
        key_hash=key_hash,
        scopes=payload.scopes,
        expires_at=payload.expiresAt,
    )
    await db.commit()

    # 构造响应：明文 Key 仅此一次返回
    data = ApiKeyResponse(
        id=api_key.id,
        name=api_key.name,
        environment=api_key.environment,
        scopes=api_key.scopes or [],
        keyPrefix=api_key.key_prefix,
        plaintextKey=raw_key,  # 明文 Key：仅展示一次，客户端必须立即保存
        lastUsedAt=api_key.last_used_at,
        expiresAt=api_key.expires_at,
        status=api_key.status,
        createdAt=api_key.created_at,
    ).model_dump(mode="json")
    return ApiResponse.success(data, build_meta(project.code))


@router.get(
    "/{projectId}/api-keys",
    summary="列出项目 API Key",
    description="列出指定项目下所有 API Key，不返回明文与哈希。",
    response_model=None,
)
async def list_api_keys(
    projectId: str = Path(..., description="项目 ID（UUID）"),
    _: str = Depends(get_management_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """列出项目 API Key。

    业务流程：
        1. 查询项目是否存在
        2. 调用 ApiKeyRepository.list_by_project 查询
        3. 返回列表，不含明文与哈希（plaintextKey 始终为 None）

    Args:
        projectId: 路径参数，项目 ID。
        _: 管理密钥依赖。
        db: 异步数据库会话。

    Returns:
        标准响应体，data.items 为 Key 列表（不含明文与哈希）。
    """
    project_repo = ProjectRepository(db)
    project = await project_repo.get_by_id(projectId)
    if project is None:
        raise ValidationError(
            f"项目不存在：{projectId}",
            details={"field": "projectId", "value": projectId},
        )

    # 构造管理用 ProjectContext
    ctx = _build_management_ctx(project.id, project.code)
    api_key_repo = ApiKeyRepository(db)
    keys = await api_key_repo.list_by_project(ctx)

    # 构造响应：列表查询不返回明文与哈希
    items = [
        ApiKeyResponse(
            id=k.id,
            name=k.name,
            environment=k.environment,
            scopes=k.scopes or [],
            keyPrefix=k.key_prefix,
            plaintextKey=None,  # 列表查询不返回明文
            lastUsedAt=k.last_used_at,
            expiresAt=k.expires_at,
            status=k.status,
            createdAt=k.created_at,
        ).model_dump(mode="json")
        for k in keys
    ]
    return ApiResponse.success({"items": items}, build_meta(project.code))


@router.delete(
    "/{projectId}/api-keys/{keyId}",
    summary="停用 API Key",
    description="将 API Key status 设为 revoked，不可恢复。",
    response_model=None,
)
async def revoke_api_key(
    projectId: str = Path(..., description="项目 ID（UUID）"),
    keyId: str = Path(..., description="API Key ID（UUID）"),
    _: str = Depends(get_management_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """停用 API Key。

    业务流程：
        1. 查询项目是否存在
        2. 调用 ApiKeyRepository.revoke 吊销 Key（status=revoked）
        3. 不存在或属于其他项目返回 404
        4. 返回更新后的 Key（不含明文）

    Args:
        projectId: 路径参数，项目 ID。
        keyId: 路径参数，API Key ID。
        _: 管理密钥依赖。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 为更新后的 Key 信息。
    """
    project_repo = ProjectRepository(db)
    project = await project_repo.get_by_id(projectId)
    if project is None:
        raise ValidationError(
            f"项目不存在：{projectId}",
            details={"field": "projectId", "value": projectId},
        )

    ctx = _build_management_ctx(project.id, project.code)
    api_key_repo = ApiKeyRepository(db)
    # 吊销 Key：Repository 内部带 project_id 过滤，跨项目访问返回 None
    revoked = await api_key_repo.revoke(ctx, keyId)
    if revoked is None:
        # Key 不存在或属于其他项目：返回 404（不泄露存在性）
        raise ValidationError(
            f"API Key 不存在或不属于该项目：{keyId}",
            details={"field": "keyId", "value": keyId},
        )
    await db.commit()

    data = ApiKeyResponse(
        id=revoked.id,
        name=revoked.name,
        environment=revoked.environment,
        scopes=revoked.scopes or [],
        keyPrefix=revoked.key_prefix,
        plaintextKey=None,  # 停用不返回明文
        lastUsedAt=revoked.last_used_at,
        expiresAt=revoked.expires_at,
        status=revoked.status,
        createdAt=revoked.created_at,
    ).model_dump(mode="json")
    return ApiResponse.success(data, build_meta(project.code))


@router.post(
    "/{projectId}/api-keys/{keyId}/rotate",
    summary="轮换 API Key",
    description=(
        "将旧 Key 设为 revoked，生成新 Key。新明文仅此一次返回，"
        "旧明文已无法获取。"
    ),
    response_model=None,
)
async def rotate_api_key(
    projectId: str = Path(..., description="项目 ID（UUID）"),
    keyId: str = Path(..., description="待轮换的 API Key ID（UUID）"),
    _: str = Depends(get_management_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """轮换 API Key。

    业务流程：
        1. 查询项目是否存在
        2. 查询旧 Key（带 project_id 过滤），不存在返回 404
        3. 将旧 Key 设为 revoked（保留 name / environment / scopes / expiresAt）
        4. 生成新 Key，复用旧 Key 的 name / environment / scopes / expiresAt
        5. 落库新 Key，返回新明文（仅展示一次）

    设计说明：
        轮换保留旧 Key 的元数据（name / scopes 等），仅替换密钥本体，
        便于客户端无感迁移。客户端拿到新明文后需立即更新本地配置。

    Args:
        projectId: 路径参数，项目 ID。
        keyId: 路径参数，待轮换的 Key ID。
        _: 管理密钥依赖。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 为新 Key 信息（含新明文，仅展示一次）。
    """
    project_repo = ProjectRepository(db)
    project = await project_repo.get_by_id(projectId)
    if project is None:
        raise ValidationError(
            f"项目不存在：{projectId}",
            details={"field": "projectId", "value": projectId},
        )

    ctx = _build_management_ctx(project.id, project.code)
    api_key_repo = ApiKeyRepository(db)

    # 查询旧 Key（带 project_id 过滤）
    old_key = await api_key_repo.get_by_id(ctx, keyId)
    if old_key is None:
        raise ValidationError(
            f"API Key 不存在或不属于该项目：{keyId}",
            details={"field": "keyId", "value": keyId},
        )

    # 步骤 1：吊销旧 Key
    await api_key_repo.revoke(ctx, keyId)

    # 步骤 2：生成新 Key，复用旧 Key 的元数据
    raw_key, key_prefix, key_hash = generate_api_key()
    new_key = await api_key_repo.create(
        ctx=ctx,
        name=old_key.name,
        environment=old_key.environment,
        key_prefix=key_prefix,
        key_hash=key_hash,
        scopes=old_key.scopes or [],
        expires_at=old_key.expires_at,
    )
    await db.commit()

    # 构造响应：新明文仅展示一次
    data = ApiKeyResponse(
        id=new_key.id,
        name=new_key.name,
        environment=new_key.environment,
        scopes=new_key.scopes or [],
        keyPrefix=new_key.key_prefix,
        plaintextKey=raw_key,  # 新明文 Key：仅展示一次
        lastUsedAt=new_key.last_used_at,
        expiresAt=new_key.expires_at,
        status=new_key.status,
        createdAt=new_key.created_at,
    ).model_dump(mode="json")
    return ApiResponse.success(data, build_meta(project.code))


# ============================================================================
# 工具函数：序列化 ProjectSettings
# ============================================================================
def _serialize_settings(settings) -> dict:
    """将 ProjectSettings ORM 实例序列化为字典。

    管理接口返回项目详情时需附带 settings，本函数将可空字段统一处理为
    None 或默认值，便于客户端解析。

    Args:
        settings: ProjectSettings ORM 实例，可能为 None。

    Returns:
        settings 字典；若 settings 为 None，返回空字典。
    """
    if settings is None:
        # 理论上不应发生（创建项目时已建 settings），兜底返回空字典
        return {}
    return {
        "chatModel": settings.chat_model,
        "embeddingModel": settings.embedding_model,
        "webSearchEnabled": settings.web_search_enabled,
        "allowedDomains": settings.allowed_domains or [],
        "blockedDomains": settings.blocked_domains or [],
        "maxEvidence": settings.max_evidence,
        "maxTokens": settings.max_tokens,
        "timeoutSeconds": settings.timeout_seconds,
    }
