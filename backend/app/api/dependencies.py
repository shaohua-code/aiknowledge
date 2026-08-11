"""API 通用依赖：鉴权、ProjectContext 注入、Scope 校验、管理密钥、请求 ID。

对应 SubTask 5.3：实现 FastAPI 依赖函数，将 HTTP 请求头转换为 ProjectContext，
并提供 Scope 校验与管理密钥校验能力。

依赖调用链路
------------
1. 业务接口注入 ``Depends(get_project_context)`` 获取 ProjectContext
2. 需要 Scope 校验的接口注入 ``Depends(require_scopes("xxx:read"))``
3. 项目管理接口（POST /projects）注入 ``Depends(get_management_api_key)``
4. 任意接口都可注入 ``Depends(get_request_id)`` 获取/生成请求 ID 用于链路追踪

设计要点
--------
1. ProjectContext 由服务端从数据库解析，禁止请求体覆盖（详见 project_context.py）
2. API Key 校验采用"前缀定位 + argon2 逐个校验"两步法，避免全表哈希校验
3. 候选记录为空或全部不匹配时统一抛 InvalidApiKeyError，避免泄露 Key 是否存在
4. last_used_at 在校验成功后异步更新，不阻塞请求
5. **日志脱敏**（Task 23.3）：解析 API Key 时仅记录脱敏后的 key_prefix，
   禁止打印完整明文 Key；异常日志中也使用脱敏版本
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from fastapi import Depends, Header
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    InvalidApiKeyError,
    ProjectCodeMismatchError,
    ProjectDisabledError,
    ScopeNotAllowedError,
)
from app.core.project_context import ProjectContext
from app.core.redactor import redact_api_key
from app.core.security import verify_api_key
from app.db.models.project import ApiKey, Project
from app.db.session import get_db

# 应用日志器：记录鉴权事件（脱敏后的 Key 信息）
logger = logging.getLogger(__name__)

# Authorization 头中 Bearer 前缀长度（"Bearer " 共 7 字符）
_BEARER_PREFIX = "Bearer "
# key_prefix 提取长度：与 security.generate_api_key 保持一致（前 12 位）
_KEY_PREFIX_LENGTH = 12


async def get_project_context(
    authorization: str = Header(
        None,
        description="Bearer ikh_live_xxx，注意 Bearer 与 Key 之间有一个空格",
    ),
    x_project_code: str = Header(
        None,
        alias="X-Project-Code",
        description="项目编码，用于一致性校验；可选，传入则必须与 API Key 所属项目一致",
    ),
    x_request_id: str = Header(
        None,
        alias="X-Request-Id",
        description="业务方生成的唯一请求号，用于链路追踪",
    ),
    db: AsyncSession = Depends(get_db),
) -> ProjectContext:
    """解析 API Key 并构造 ProjectContext。

    本依赖是所有业务接口的鉴权入口，执行以下步骤：
    1. 校验 Authorization 头存在且以 ``Bearer `` 开头
    2. 提取明文 Key，按前缀定位数据库候选记录
    3. 对候选记录逐个 argon2 校验，匹配则得到 api_key_id / project_id / scopes
    4. 加载关联 Project，校验 status=active
    5. 校验 X-Project-Code（若提供）与 Project.code 一致
    6. 更新 api_keys.last_used_at
    7. 构造并返回 ProjectContext

    Args:
        authorization: Authorization 头，形如 ``Bearer ikh_live_xxx``。
        x_project_code: X-Project-Code 头，可选，用于一致性校验。
        x_request_id: X-Request-Id 头，仅用于日志关联，不参与鉴权。
        db: 异步数据库会话，由 ``get_db`` 注入。

    Returns:
        ProjectContext: 当前请求的项目上下文，不可变。

    Raises:
        InvalidApiKeyError: Authorization 缺失/格式错/Key 不匹配。
        ProjectDisabledError: Key 所属项目已停用。
        ProjectCodeMismatchError: X-Project-Code 与项目实际 code 不一致。
    """
    # ------------------------------------------------------------------
    # 步骤 1：校验 Authorization 头格式
    # ------------------------------------------------------------------
    if not authorization or not authorization.startswith(_BEARER_PREFIX):
        # 缺失或不是 Bearer 开头：统一抛 InvalidApiKeyError，避免泄露"Key 是否存在"
        raise InvalidApiKeyError("Authorization 头缺失或格式错误，应为 'Bearer <api_key>'")

    # 提取 Bearer 后的明文 Key，并去除两端空白以防客户端拼接错乱
    raw_key = authorization[len(_BEARER_PREFIX):].strip()
    if not raw_key:
        # Bearer 后为空，同样视为无效 Key
        raise InvalidApiKeyError("Authorization 头中未携带 API Key")

    # ------------------------------------------------------------------
    # 步骤 2：按前缀定位候选 api_keys 记录
    # ------------------------------------------------------------------
    # 从明文 Key 提取前 12 位作为 key_prefix（与 generate_api_key 保持一致）
    # 此步骤是性能关键：通过 key_prefix 索引将候选集缩到极小范围，
    # 避免对全表逐个 argon2 校验（argon2 单次约 50ms，全表会拖垮服务）
    key_prefix = raw_key[:_KEY_PREFIX_LENGTH]

    # 查询候选记录：key_prefix 匹配且状态为 active
    # status='active' 过滤已吊销的 Key，避免无效校验
    stmt_candidates = select(ApiKey).where(
        ApiKey.key_prefix == key_prefix,
        ApiKey.status == "active",
    )
    candidates = (await db.execute(stmt_candidates)).scalars().all()

    # ------------------------------------------------------------------
    # 步骤 3：逐个 argon2 校验，定位匹配的 ApiKey 记录
    # ------------------------------------------------------------------
    matched_key: ApiKey | None = None
    for candidate in candidates:
        # 对每条候选记录调用 argon2 校验
        # argon2 自带 salt 与参数，每条记录哈希不同，必须逐个校验
        if verify_api_key(raw_key, candidate.key_hash):
            matched_key = candidate
            break

    # 无匹配：统一抛 InvalidApiKeyError，不区分"前缀不存在"与"哈希不匹配"
    # 避免给攻击者提供 Key 是否存在的信号
    if matched_key is None:
        # 日志脱敏：仅记录 key_prefix（前 12 位），不记录完整明文 Key
        # 便于运维排查"为什么 Key 校验失败"而不泄露 Key 本身
        logger.warning(
            "API Key 校验失败（key_prefix=%s, 候选数=%d）",
            redact_api_key(key_prefix),
            len(candidates),
        )
        raise InvalidApiKeyError("API Key 无效或已失效")

    # 校验成功：记录脱敏后的 Key 信息，便于审计与链路追踪
    # 仅记录 key_prefix 的脱敏版本，不记录完整明文 Key
    logger.info(
        "API Key 校验成功（key_prefix=%s, project=%s）",
        redact_api_key(key_prefix),
        matched_key.project_id,
    )

    # ------------------------------------------------------------------
    # 步骤 4：加载关联 Project 并校验状态
    # ------------------------------------------------------------------
    # 通过 api_keys.project_id 查询所属项目（ProjectOwnedMixin 提供 project_id）
    stmt_project = select(Project).where(Project.id == matched_key.project_id)
    project = (await db.execute(stmt_project)).scalar_one_or_none()

    # 项目不存在（理论上不应发生，外键约束保证）：视为无效 Key
    if project is None:
        raise InvalidApiKeyError("API Key 关联的项目不存在")

    # 项目状态校验：disabled 项目拒绝所有业务请求（项目管理接口除外）
    if project.status != "active":
        raise ProjectDisabledError(f"项目 {project.code} 已停用")

    # ------------------------------------------------------------------
    # 步骤 5：校验 X-Project-Code 一致性（若提供）
    # ------------------------------------------------------------------
    # X-Project-Code 可选：客户端传入则必须与 Project.code 一致
    # 用途：防止客户端误用其他项目的 Key 调用本接口（如把 ai-fund 的 Key 用到 ai-resume）
    if x_project_code is not None:
        # Project.code 是 CIText 大小写不敏感，这里统一小写比较
        if x_project_code.lower() != project.code.lower():
            raise ProjectCodeMismatchError(
                f"X-Project-Code ({x_project_code}) 与 API Key 所属项目 ({project.code}) 不一致"
            )

    # ------------------------------------------------------------------
    # 步骤 6：异步更新 last_used_at（不阻塞请求）
    # ------------------------------------------------------------------
    # 更新最近使用时间，便于后台展示与清理过期 Key
    # 使用 UPDATE 语句直接更新，避免再次加载 ORM 对象
    now = datetime.now(timezone.utc)
    stmt_update_last_used = (
        update(ApiKey)
        .where(ApiKey.id == matched_key.id)
        .values(last_used_at=now)
    )
    await db.execute(stmt_update_last_used)
    # 提交事务：last_used_at 更新失败不应阻塞请求，但这里仍提交保证持久化
    # 若提交失败会被 get_db 的 except 分支回滚并抛出，由全局异常处理器兜底
    await db.commit()

    # ------------------------------------------------------------------
    # 步骤 7：构造并返回 ProjectContext
    # ------------------------------------------------------------------
    # scopes 转为 tuple 保证不可变（数据库 ARRAY 返回 list）
    scopes_tuple = tuple(matched_key.scopes or ())
    # allowed_tools 暂未持久化在 api_keys 表，预留为空，后续可从项目设置加载
    # TODO Task 6+: 从 project_settings 加载 allowed_tools 填充此字段
    return ProjectContext(
        project_id=project.id,
        project_code=project.code,
        environment=matched_key.environment,
        api_key_id=matched_key.id,
        scopes=scopes_tuple,
        allowed_tools=(),  # 预留：后续从 project_settings 加载
    )


def require_scopes(*required_scopes: str):
    """构造 Scope 校验依赖。

    用法：
        @router.post("/research/run", dependencies=[Depends(require_scopes("research:run"))])
        async def run_research(ctx: ProjectContext = Depends(get_project_context)):
            ...

    或在路径操作中作为依赖注入。本函数返回一个内部依赖函数，FastAPI 会先执行
    ``get_project_context`` 拿到 ctx，再调用本依赖校验 Scope。

    Args:
        *required_scopes: 该接口需要的全部 Scope（AND 语义）。
            如 ``require_scopes("retrieval:read", "research:run")`` 要求两者都具备。

    Returns:
        一个异步依赖函数 ``_check``，签名为 ``(ctx) -> ProjectContext``，
        返回校验通过的 ProjectContext，便于接口同时获取 ctx 与校验 Scope。
    """
    async def _check(
        ctx: ProjectContext = Depends(get_project_context),
    ) -> ProjectContext:
        """Scope 校验闭包：检查 ctx 是否具备全部 required_scopes。"""
        # 调用 ProjectContext.require_scopes 做实际判断（AND 语义）
        if not ctx.require_scopes(*required_scopes):
            # 缺失 Scope：抛 403，details 中列出所需 Scope 便于客户端排查
            raise ScopeNotAllowedError(
                f"当前 API Key 缺少所需 Scope: {', '.join(required_scopes)}",
                details={"required_scopes": list(required_scopes), "current_scopes": list(ctx.scopes)},
            )
        # 校验通过：返回 ctx，接口可直接使用
        return ctx

    return _check


async def get_management_api_key(
    authorization: str = Header(
        None,
        description="管理密钥，格式 'Bearer <management_api_key>'，不关联项目",
    ),
) -> str:
    """校验管理密钥，用于项目管理接口（POST/GET/PATCH /api/v1/projects）。

    管理密钥与项目 API Key 不同：
    - 不关联任何项目，不构造 ProjectContext
    - 全局唯一，由 ``settings.management_api_key`` 配置
    - 仅用于后台管理操作（创建项目、查看项目列表等）

    Args:
        authorization: Authorization 头，形如 ``Bearer <management_api_key>``。

    Returns:
        校验通过的原始密钥字符串（已去除 Bearer 前缀）。

    Raises:
        InvalidApiKeyError: 缺失/格式错/与配置不匹配。
    """
    # 校验 Authorization 头存在且为 Bearer 格式
    if not authorization or not authorization.startswith(_BEARER_PREFIX):
        raise InvalidApiKeyError("管理接口需要 Authorization 头：'Bearer <management_api_key>'")

    # 提取密文并去除两端空白
    raw_key = authorization[len(_BEARER_PREFIX):].strip()
    if not raw_key:
        raise InvalidApiKeyError("管理密钥不能为空")

    # 与 settings.management_api_key 比对
    # 注意：若配置中未设置 management_api_key，则任何密钥都不通过（最严格默认）
    if not settings.management_api_key or raw_key != settings.management_api_key:
        # 不匹配：抛 401，避免泄露管理密钥是否配置
        raise InvalidApiKeyError("管理密钥无效")

    # 返回原始密钥，调用方一般不使用返回值，仅作为依赖触发校验
    return raw_key


async def get_request_id(
    x_request_id: str = Header(
        None,
        alias="X-Request-Id",
        description="业务方生成的唯一请求号；未传则由服务端生成",
    ),
) -> str:
    """获取或生成请求 ID，用于链路追踪与日志关联。

    业务方可通过 ``X-Request-Id`` 头传入自定义请求号（如 ULID/UUID），
    服务端原样返回并在日志中使用；未传时由服务端生成 ``req_`` 前缀 + 时间戳。

    Args:
        x_request_id: X-Request-Id 头，可选。

    Returns:
        请求 ID 字符串，形如 ``req_<13位毫秒时间戳>`` 或业务方传入值。
    """
    if x_request_id:
        # 业务方已传入：去除两端空白后原样返回
        return x_request_id.strip()

    # 未传入：生成 req_ + 毫秒时间戳
    # 此处使用时间戳而非 ULID，避免引入额外依赖；
    # 单实例下毫秒级冲突概率极低，分布式场景建议业务方主动传入 X-Request-Id
    # time.time_ns() 返回纳秒，除以 1_000_000 转毫秒
    return f"req_{time.time_ns() // 1_000_000}"
