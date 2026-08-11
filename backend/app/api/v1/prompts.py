"""提示词版本管理接口（Task 14）。

对应 SubTask 14.1：提示词 CRUD API。

路由设计
--------
1. ``GET /api/v1/prompts``
   - 列出当前项目所有版本（仅需项目上下文）
   - 返回精简列表（systemPrompt 截断 50 字）
2. ``GET /api/v1/prompts/active``
   - 获取当前启用版本（仅需项目上下文）
   - 无启用版本返回 null
3. ``GET /api/v1/prompts/{versionId}``
   - 获取版本详情（仅需项目上下文）
   - 跨项目返回 404 TASK_NOT_FOUND
4. ``POST /api/v1/prompts``
   - 创建新版本（需 ``knowledge:write`` Scope）
   - 版本号自动递增，``activateImmediately=true`` 时创建后激活
5. ``PATCH /api/v1/prompts/{versionId}``
   - 编辑版本（需 ``knowledge:write`` Scope）
   - active 版本不允许直接编辑，需创建新版本迭代
6. ``POST /api/v1/prompts/{versionId}/activate``
   - 激活版本（需 ``knowledge:write`` Scope）
   - 事务内先取消其他 active 再激活
7. ``DELETE /api/v1/prompts/{versionId}``
   - 删除版本（需 ``knowledge:write`` Scope）
   - 仅允许删除非 active 且未被历史任务引用的版本

设计要点（重点：版本管理业务规则）
----------------------------------
1. 每项目仅一个 active 版本
   ``is_active`` 唯一性由 PostgreSQL 部分唯一索引保证，
   ``set_active`` 在事务内先取消其他 active 再激活，避免索引冲突。

2. 版本号自动递增
   版本号由 Repository 在事务内查询 ``MAX(version)`` 并 +1 生成，
   客户端无法指定，保证单调递增与唯一性。

3. active 版本不可编辑、不可删除
   active 版本可能正在被研究任务使用，直接修改会破坏历史任务的复现性，
   删除会丢失可追溯链路。迭代 active 版本应通过"创建新版本 → 激活新版本"实现。

4. 历史任务保留版本号的意义
   ``research_tasks.prompt_version_id`` 记录每个任务使用的版本，
   用于复现、审计与追溯。被任务引用的版本不应删除（保留版本号 = 保留追溯链路）。
   本接口的删除校验中，历史引用检查标注 TODO，当前仅校验 active 状态。

5. 跨项目隔离
   所有查询通过 Repository 强制带 ``project_id`` 过滤，
   跨项目查询返回 None，由端点统一抛 ``TaskNotFoundError``（404，不泄露存在性）。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_project_context, require_scopes
from app.api.v1.schemas import (
    PromptCreateRequest,
    PromptListItemResponse,
    PromptResponse,
    PromptUpdateRequest,
)
from app.core.exceptions import TaskNotFoundError, ValidationError
from app.core.project_context import ProjectContext
from app.core.response import ApiResponse, build_meta
from app.core.scopes import SCOPE_KNOWLEDGE_WRITE
from app.db.repositories.prompt import PromptRepository
from app.db.session import get_db
from app.modules.prompts.service import PromptService

# 列表接口中 systemPrompt 截断长度（字符数）
# 50 字足以让用户在列表中识别版本内容主题，详情需调用详情接口
_PROMPT_SUMMARY_LENGTH = 50

router = APIRouter(prefix="/prompts", tags=["提示词版本管理"])


def _truncate(text: str, max_length: int) -> str:
    """截断文本到指定长度，超长时追加省略号。

    用于列表接口的 systemPrompt 摘要，避免返回完整提示词占用带宽。

    Args:
        text: 原始文本。
        max_length: 最大字符数（不含省略号）。

    Returns:
        截断后的文本；超长时末尾追加 ``...``。
    """
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."


def _build_prompt_response(prompt) -> dict:
    """构造完整的提示词版本响应体。

    Args:
        prompt: PromptVersion ORM 实例。

    Returns:
        PromptResponse 序列化后的 dict（JSON 可序列化）。
    """
    return PromptResponse(
        id=prompt.id,
        version=prompt.version,
        isActive=prompt.is_active,
        systemPrompt=prompt.system_prompt,
        evidenceRules=prompt.evidence_rules or "",
        outputSchema=prompt.output_schema or {},
        prohibitions=prompt.prohibitions or "",
        riskTemplate=prompt.risk_template or "",
        createdAt=prompt.created_at,
    ).model_dump(mode="json")


# ============================================================================
# 列出当前项目所有版本
# ============================================================================
@router.get(
    "",
    summary="列出当前项目提示词版本",
    description="列出当前 API Key 所属项目下所有提示词版本，按版本号倒序，systemPrompt 截断 50 字。",
    response_model=None,
)
async def list_prompts(
    ctx: ProjectContext = Depends(get_project_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """列出当前项目所有提示词版本。

    业务流程：
        1. 调用 Repository.list_versions 查询当前项目所有版本（强制 project_id 过滤）
        2. 对每个版本的 systemPrompt 截断 50 字
        3. 返回精简列表，按版本号倒序（最新在前）

    为什么仅需 get_project_context 而非 require_scopes？
        列表查询是只读操作，任何有效 API Key 都可查看自己项目的提示词版本列表，
        不需要特定 Scope。但 project_id 过滤依然强制，杜绝跨项目查看。

    Args:
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应体，data.items 为版本精简列表。
    """
    repo = PromptRepository(db)
    # 按 version 倒序，便于查看最新版本
    versions = await repo.list_versions(ctx)

    # 构造精简列表：systemPrompt 截断 50 字
    items = [
        PromptListItemResponse(
            id=p.id,
            version=p.version,
            isActive=p.is_active,
            systemPrompt=_truncate(p.system_prompt, _PROMPT_SUMMARY_LENGTH),
            createdAt=p.created_at,
        ).model_dump(mode="json")
        for p in versions
    ]
    return ApiResponse.success({"items": items}, build_meta(ctx.project_code))


# ============================================================================
# 获取当前启用版本
# ============================================================================
@router.get(
    "/active",
    summary="获取当前启用版本",
    description="获取当前项目的启用版本（is_active=true）。无启用版本返回 null。",
    response_model=None,
)
async def get_active_prompt(
    ctx: ProjectContext = Depends(get_project_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取当前启用版本。

    业务流程：
        1. 调用 Repository.get_active 查询启用版本（强制 project_id 过滤）
        2. 无启用版本返回 data=null，提示用户创建并启用版本
        3. 有启用版本返回完整版本详情

    为什么无启用版本返回 null 而非 404？
        "无启用版本"是合法的业务状态（如项目刚创建、用户主动取消激活），
        非错误状态。返回 null 让客户端展示"请创建并启用版本"的引导。

    Args:
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 为完整版本详情或 null。
    """
    repo = PromptRepository(db)
    # 查询启用版本（每项目至多一条 is_active=true，由部分唯一索引保证）
    active = await repo.get_active(ctx)
    if active is None:
        # 无启用版本：返回 null，客户端应引导用户创建并激活
        return ApiResponse.success(None, build_meta(ctx.project_code))
    # 返回完整版本详情
    return ApiResponse.success(_build_prompt_response(active), build_meta(ctx.project_code))


# ============================================================================
# 获取版本详情
# ============================================================================
@router.get(
    "/{versionId}",
    summary="获取提示词版本详情",
    description="按版本 ID 获取详情。跨项目查询返回 404 TASK_NOT_FOUND，不泄露存在性。",
    response_model=None,
)
async def get_prompt(
    versionId: str = Path(..., description="提示词版本 ID"),
    ctx: ProjectContext = Depends(get_project_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取提示词版本详情。

    业务流程：
        1. 按 versionId 查询版本（强制 project_id 过滤）
        2. 不存在或属于其他项目返回 404 TASK_NOT_FOUND
        3. 返回完整版本详情

    为什么跨项目查询返回 404 而非 403？
        返回 403 会泄露"该版本在其他项目中存在"，给攻击者提供枚举线索。
        统一返回 404 不区分"不存在"与"属于其他项目"，符合最小信息泄露原则。

    Args:
        versionId: 路径参数，提示词版本 ID。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 为完整版本详情。
    """
    repo = PromptRepository(db)
    # 双重过滤：id + project_id，跨项目访问返回 None
    prompt = await repo.get_by_id(ctx, versionId)
    if prompt is None:
        # 不存在或属于其他项目：统一抛 404，不泄露存在性
        raise TaskNotFoundError(
            f"提示词版本不存在：{versionId}",
            details={"field": "versionId", "value": versionId},
        )
    return ApiResponse.success(_build_prompt_response(prompt), build_meta(ctx.project_code))


# ============================================================================
# 创建新版本
# ============================================================================
@router.post(
    "",
    summary="创建提示词版本",
    description=(
        "创建新提示词版本，版本号自动递增（当前项目最大版本 + 1）。"
        "activateImmediately=true 时创建后立即激活该版本。需 knowledge:write Scope。"
    ),
    response_model=None,
)
async def create_prompt(
    payload: PromptCreateRequest = Body(...),
    ctx: ProjectContext = Depends(require_scopes(SCOPE_KNOWLEDGE_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """创建提示词版本。

    业务流程：
        1. 调用 Service.create_version 创建版本（版本号由 Repository 自动递增）
        2. 若 activateImmediately=true，调用 Service.activate_version 激活该版本
        3. 提交事务
        4. 返回完整版本详情

    版本号递增机制（重点）：
        Repository.create 在事务内查询 ``SELECT MAX(version) FROM prompt_versions
        WHERE project_id = ?``，+1 作为新版本号。并发创建时依赖事务隔离，
        若两个请求同时读到相同 MAX(version)，第二个提交时会因版本号冲突失败
        （版本号无唯一约束，但应用层应保证递增；当前简化处理，并发冲突概率极低）。

    激活事务处理（重点）：
        ``set_active`` 在事务内按"先取消所有 active，再激活指定版本"顺序执行，
        避免 ``uq_prompt_active_per_project`` 部分唯一索引冲突。
        本接口在创建后激活时，两步操作在同一事务内，由 ``db.commit()`` 原子提交。

    Args:
        payload: 请求体，含 systemPrompt / evidenceRules / outputSchema /
            prohibitions / riskTemplate / activateImmediately。
        ctx: 项目上下文（由 require_scopes 校验 Scope 后返回）。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 为新建版本详情（activateImmediately=true 时 isActive=true）。
    """
    service = PromptService(db)
    # 步骤 1：创建版本（版本号由 Repository 自动递增，is_active=False）
    prompt = await service.create_version(
        ctx=ctx,
        session=db,
        system_prompt=payload.systemPrompt,
        evidence_rules=payload.evidenceRules,
        output_schema=payload.outputSchema,
        prohibitions=payload.prohibitions,
        risk_template=payload.riskTemplate,
    )

    # 步骤 2：若 activateImmediately=true，激活该版本
    if payload.activateImmediately:
        # 激活操作在事务内先取消其他 active，再激活当前版本
        prompt = await service.activate_version(ctx=ctx, session=db, version_id=prompt.id)

    # 步骤 3：提交事务（创建 + 激活原子提交）
    await db.commit()

    # 步骤 4：返回完整版本详情
    # 重新查询以拿到激活后的 is_active 状态与 created_at
    return ApiResponse.success(_build_prompt_response(prompt), build_meta(ctx.project_code))


# ============================================================================
# 编辑版本
# ============================================================================
@router.patch(
    "/{versionId}",
    summary="编辑提示词版本",
    description=(
        "编辑非 active 版本字段。active 版本不允许直接编辑，需创建新版本迭代。"
        "需 knowledge:write Scope。"
    ),
    response_model=None,
)
async def update_prompt(
    versionId: str = Path(..., description="提示词版本 ID"),
    payload: PromptUpdateRequest = Body(...),
    ctx: ProjectContext = Depends(require_scopes(SCOPE_KNOWLEDGE_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """编辑提示词版本（仅非 active）。

    业务流程：
        1. 按 versionId 查询版本（强制 project_id 过滤），不存在返回 404
        2. 校验版本非 active，active 版本返回 VALIDATION_ERROR 提示创建新版本
        3. 收集请求体中非空字段（PATCH 语义）
        4. 调用 Repository.update 更新
        5. 提交事务
        6. 返回更新后的版本详情

    为什么 active 版本不允许直接编辑？（重点）
        active 版本可能正在被研究任务使用，直接修改会导致：
        1. 历史任务的复现性被破坏（同样问题再次运行得到不同结果）
        2. 正在进行的任务读到部分更新的字段，状态不一致
        3. 审计追溯链路断裂（无法确定历史任务用的是哪版内容）
        因此 active 版本应通过"创建新版本 → 激活新版本"的方式迭代，
        保留旧版本用于历史任务的版本号追溯。本接口仅允许编辑非 active 版本，
        便于用户在版本"草稿态"时修正内容。

    Args:
        versionId: 路径参数，提示词版本 ID。
        payload: 请求体，含待更新字段（PATCH 语义，仅更新传入字段）。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 为更新后的版本详情。
    """
    repo = PromptRepository(db)
    # 步骤 1：查询版本（强制 project_id 过滤）
    prompt = await repo.get_by_id(ctx, versionId)
    if prompt is None:
        # 不存在或属于其他项目：统一抛 404
        raise TaskNotFoundError(
            f"提示词版本不存在：{versionId}",
            details={"field": "versionId", "value": versionId},
        )

    # 步骤 2：校验非 active，active 版本禁止直接编辑
    if prompt.is_active:
        raise ValidationError(
            "不允许直接编辑 active 版本，请创建新版本并激活以迭代提示词",
            details={
                "field": "versionId",
                "value": versionId,
                "isActive": True,
                "hint": "调用 POST /api/v1/prompts 创建新版本，"
                "并设置 activateImmediately=true 完成迭代",
            },
        )

    # 步骤 3：收集待更新字段（PATCH 语义，仅取非 None 值）
    # version 与 is_active 不在可更新字段中，物理上无法被修改
    fields: dict = {}
    if payload.systemPrompt is not None:
        fields["system_prompt"] = payload.systemPrompt
    if payload.evidenceRules is not None:
        fields["evidence_rules"] = payload.evidenceRules
    if payload.outputSchema is not None:
        fields["output_schema"] = payload.outputSchema
    if payload.prohibitions is not None:
        fields["prohibitions"] = payload.prohibitions
    if payload.riskTemplate is not None:
        fields["risk_template"] = payload.riskTemplate

    if not fields:
        # 无字段更新：直接返回当前版本（幂等）
        return ApiResponse.success(_build_prompt_response(prompt), build_meta(ctx.project_code))

    # 步骤 4：执行更新
    updated = await repo.update(ctx, versionId, **fields)
    # 步骤 5：提交事务
    await db.commit()

    # 步骤 6：返回更新后的版本详情
    return ApiResponse.success(_build_prompt_response(updated), build_meta(ctx.project_code))


# ============================================================================
# 激活版本
# ============================================================================
@router.post(
    "/{versionId}/activate",
    summary="激活提示词版本",
    description=(
        "激活指定版本，事务内先取消其他 active 版本再激活。需 knowledge:write Scope。"
    ),
    response_model=None,
)
async def activate_prompt(
    versionId: str = Path(..., description="提示词版本 ID"),
    ctx: ProjectContext = Depends(require_scopes(SCOPE_KNOWLEDGE_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """激活提示词版本。

    业务流程：
        1. 调用 Repository.set_active 激活指定版本
           - 事务内先 UPDATE 取消当前项目所有 is_active=true
           - 再 UPDATE 激活指定版本为 is_active=true
        2. 提交事务
        3. 返回激活后的版本详情

    版本切换的事务处理（重点）：
        ``prompt_versions`` 上有部分唯一索引 ``uq_prompt_active_per_project``：
        ``CREATE UNIQUE INDEX ... ON prompt_versions(project_id) WHERE is_active = true``
        若直接 UPDATE 目标版本为 active，可能与其他 active 记录冲突触发唯一约束。
        因此 ``set_active`` 在事务内按以下顺序执行：
            1. UPDATE 取消当前项目所有 ``is_active=true`` 的记录
            2. UPDATE 激活指定版本为 ``is_active=true``
        两步在同一事务内，避免中间状态被并发请求观察到"无 active"或"双 active"。
        本接口通过 ``db.commit()`` 保证两步原子提交。

    Args:
        versionId: 路径参数，提示词版本 ID。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 为激活后的版本详情（isActive=true）。

    Raises:
        TaskNotFoundError: 指定版本不存在或不属于当前项目。
    """
    repo = PromptRepository(db)
    try:
        # 激活操作在事务内先取消其他 active，再激活指定版本
        activated = await repo.set_active(ctx, versionId)
    except ValueError as exc:
        # Repository 抛 ValueError 表示版本不存在或不属于当前项目
        raise TaskNotFoundError(
            f"提示词版本不存在：{versionId}",
            details={"field": "versionId", "value": versionId},
        ) from exc

    # 提交事务（取消其他 active + 激活当前版本 原子提交）
    await db.commit()

    return ApiResponse.success(_build_prompt_response(activated), build_meta(ctx.project_code))


# ============================================================================
# 删除版本
# ============================================================================
@router.delete(
    "/{versionId}",
    summary="删除提示词版本",
    description=(
        "删除非 active 且未被历史任务引用的版本。active 版本不可删。"
        "需 knowledge:write Scope。"
    ),
    response_model=None,
)
async def delete_prompt(
    versionId: str = Path(..., description="提示词版本 ID"),
    ctx: ProjectContext = Depends(require_scopes(SCOPE_KNOWLEDGE_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """删除提示词版本（仅非 active 且未被历史任务引用）。

    业务流程：
        1. 按 versionId 查询版本（强制 project_id 过滤），不存在返回 404
        2. 校验版本非 active，active 版本禁止删除
        3. 校验版本未被历史任务引用（research_tasks.prompt_version_id）
           - 当前简化：仅校验 active 状态，历史引用检查标注 TODO
        4. 调用 Repository.delete 物理删除
        5. 提交事务
        6. 返回删除结果

    为什么 active 版本不可删？
        active 版本可能正在被研究任务使用，删除会导致正在进行的任务
        读到已删除的提示词，且丢失可追溯链路。应先激活其他版本，
        再删除原版本。

    为什么被历史任务引用的版本不可删？
        ``research_tasks.prompt_version_id`` 记录任务使用的版本，
        用于复现、审计与追溯。若删除被引用的版本，历史任务的版本号
        将指向不存在的记录，追溯链路断裂。
        （本接口当前简化处理，历史引用检查标注 TODO，仅校验 active 状态）

    Args:
        versionId: 路径参数，提示词版本 ID。
        ctx: 项目上下文。
        db: 异步数据库会话。

    Returns:
        标准响应体，data 包含 deleted 字段与原版本 id。
    """
    repo = PromptRepository(db)
    # 步骤 1：查询版本（强制 project_id 过滤）
    prompt = await repo.get_by_id(ctx, versionId)
    if prompt is None:
        # 不存在或属于其他项目：统一抛 404
        raise TaskNotFoundError(
            f"提示词版本不存在：{versionId}",
            details={"field": "versionId", "value": versionId},
        )

    # 步骤 2：校验非 active，active 版本禁止删除
    if prompt.is_active:
        raise ValidationError(
            "不允许删除 active 版本，请先激活其他版本再删除本版本",
            details={
                "field": "versionId",
                "value": versionId,
                "isActive": True,
                "hint": "调用 POST /api/v1/prompts/{otherVersionId}/activate 激活其他版本",
            },
        )

    # 步骤 3：校验未被历史任务引用
    # TODO Task 14+: 校验 research_tasks.prompt_version_id 是否引用本版本
    #   若被引用，抛 ValidationError("版本被历史任务引用，不可删除，建议保留用于追溯")
    #   当前简化：仅校验 active 状态，历史引用检查暂未实现
    #   实现时需在 ResearchTaskRepository 增加 count_by_prompt_version(ctx, version_id) 方法

    # 步骤 4：执行物理删除（Repository 内部带 project_id 过滤）
    deleted = await repo.delete(ctx, versionId)
    # 步骤 5：提交事务
    await db.commit()

    # 步骤 6：返回删除结果
    data = {
        "deleted": deleted,
        "id": prompt.id,
        "version": prompt.version,
    }
    return ApiResponse.success(data, build_meta(ctx.project_code))
