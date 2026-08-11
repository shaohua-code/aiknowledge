"""提示词版本管理业务服务：PromptService。

对应 SubTask 14.3：封装提示词版本的业务逻辑，供 API 层调用。

设计理念（务必阅读）
--------------------
1. 每项目仅一个主版本（is_active=true）
   研究任务执行时只读取 active 版本的提示词，保证同一项目在同一时刻
   使用统一的提示词配置，避免版本混乱。``is_active`` 唯一性由 PostgreSQL
   部分唯一索引 ``uq_prompt_active_per_project`` 在数据库层强制保证。

2. 版本切换必须事务化
   ``set_active`` 在事务内按"先取消所有 active，再激活指定版本"顺序执行，
   避免中间状态触发唯一索引冲突或被并发请求观察到不一致状态。
   本 Service 不直接管理事务边界（commit/rollback 由 API 层统一控制），
   仅保证方法内的多步操作在 flush 前完成，由 API 层统一提交。

3. 历史任务保留版本号的意义
   ``research_tasks.prompt_version_id`` 记录每个任务使用的提示词版本 ID，
   用于：
   - 复现性：相同问题用相同版本提示词可复现结果
   - 审计：追溯历史任务使用的是哪版提示词，便于排查异常输出
   - 不可变性：被任务引用的版本不应被删除（保留版本号 = 保留可追溯链路）
   因此本 Service 的删除接口会校验版本未被 active 且未被任务引用
   （任务引用校验在 API 层调用时执行，本 Service 聚焦核心版本管理逻辑）。

4. 默认模板自动初始化
   项目首次访问提示词时（``ensure_default_prompt``），若无任何版本，
   使用 ``templates.get_default_template(project_code)`` 创建默认版本并激活，
   确保每个项目开箱即用，避免"无 active 版本导致研究任务无提示词"的边界问题。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.db.models.prompt import PromptVersion
from app.db.repositories.prompt import PromptRepository
from app.modules.prompts.templates import get_default_template

if TYPE_CHECKING:
    # 仅类型检查时导入，避免运行时循环导入
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.project_context import ProjectContext


class PromptService:
    """提示词版本管理业务服务。

    封装版本创建、激活、默认模板初始化等业务逻辑。
    Repository 仅负责数据访问，本 Service 负责业务规则编排
    （如默认模板选择、版本号递增由 Repository 保证）。

    使用方式：
        service = PromptService(db)
        await service.ensure_default_prompt(ctx, db)
        version = await service.create_version(ctx, db, system_prompt=..., ...)

    Note:
        本 Service 不调用 ``session.commit()`` / ``session.rollback()``，
        事务边界由 API 层统一管理，便于多步操作在同一事务内原子提交。
    """

    def __init__(self, session: "AsyncSession") -> None:
        """初始化服务，创建内部 Repository。

        Args:
            session: 异步数据库会话，由 API 层通过 ``Depends(get_db)`` 注入。
        """
        self.session = session
        # 复用同一会话创建 Repository，保证事务一致性
        self.repo = PromptRepository(session)

    async def ensure_default_prompt(
        self,
        ctx: "ProjectContext",
        session: "AsyncSession",
    ) -> None:
        """项目首次访问时若无任何版本，创建默认模板版本并激活。

        业务流程：
            1. 查询当前项目是否有 active 版本
            2. 有 active 版本：直接返回，无需初始化
            3. 无 active 版本：
               a. 查询当前项目是否有任意版本（可能存在但未激活的版本）
               b. 已有版本但未激活：不创建新版本，仅提示用户去激活
                  （本方法仅负责"完全空白"场景的初始化）
               c. 完全无版本：使用默认模板创建版本 1 并激活

        为什么仅处理"完全空白"场景？
            若项目已有版本但未激活（如用户主动取消了 active），
            可能是用户有意的状态（如配置调整中），不应擅自创建新版本覆盖。
            此时 ``ensure_default_prompt`` 直接返回，由 API 层提示用户去激活现有版本。

        Args:
            ctx: 项目上下文，提供 project_id 与 project_code。
            session: 异步数据库会话（与 __init__ 传入的 session 相同，
                显式传入便于 Service 方法独立调用，不依赖实例状态）。
        """
        # 步骤 1：查询当前项目是否有 active 版本
        active = await self.repo.get_active(ctx)
        if active is not None:
            # 已有 active 版本：无需初始化
            return

        # 步骤 2：查询当前项目是否有任意版本
        versions = await self.repo.list_versions(ctx)
        if versions:
            # 已有版本但无 active：不擅自创建，由 API 层提示用户激活
            return

        # 步骤 3：完全无版本，使用默认模板创建并激活
        # 按项目 code 选择对应场景的默认模板
        template = get_default_template(ctx.project_code)
        # 创建版本 1（Repository 自动递增版本号，首次为 1）
        prompt = await self.repo.create(
            ctx=ctx,
            system_prompt=template["system_prompt"],
            evidence_rules=template["evidence_rules"],
            output_schema=template["output_schema"],
            prohibitions=template["prohibitions"],
            risk_template=template["risk_template"],
        )
        # 立即激活该默认版本，保证项目开箱即用
        await self.repo.set_active(ctx, prompt.id)

    async def create_version(
        self,
        ctx: "ProjectContext",
        session: "AsyncSession",
        **fields: Any,
    ) -> PromptVersion:
        """创建新的提示词版本。

        版本号由 Repository 自动递增（当前项目 MAX(version) + 1），
        新版本默认 ``is_active=false``。如需创建后立即激活，
        由调用方在创建后调用 ``activate_version``（或 API 层根据
        ``activateImmediately`` 参数决定）。

        为什么不在本方法内支持 ``activate_immediately``？
            保持单一职责：本方法仅负责"创建"，激活是独立的业务动作。
            API 层根据请求参数决定是否串联调用，便于复用与测试。

        Args:
            ctx: 项目上下文。
            session: 异步数据库会话。
            **fields: 提示词字段，必须包含 ``system_prompt``，可选
                ``evidence_rules`` / ``output_schema`` / ``prohibitions``
                / ``risk_template``。

        Returns:
            创建后的 PromptVersion 实例（含自动生成的 version，is_active=False）。
        """
        return await self.repo.create(ctx=ctx, **fields)

    async def activate_version(
        self,
        ctx: "ProjectContext",
        session: "AsyncSession",
        version_id: str,
    ) -> PromptVersion:
        """激活指定版本（事务内先取消其他 active，再激活）。

        版本切换的事务处理（重点）：
            ``prompt_versions`` 上有部分唯一索引 ``uq_prompt_active_per_project``：
            ``CREATE UNIQUE INDEX ... ON prompt_versions(project_id) WHERE is_active = true``
            该索引保证每个项目至多一条 ``is_active=true`` 记录。
            若直接 UPDATE 目标版本为 active，可能与其他 active 记录冲突触发唯一约束。
            因此 ``set_active`` 在事务内按以下顺序执行：
                1. UPDATE 取消当前项目所有 ``is_active=true`` 的记录
                2. UPDATE 激活指定版本为 ``is_active=true``
            两步在同一事务内，避免中间状态被并发请求观察到"无 active"或"双 active"。

        Args:
            ctx: 项目上下文。
            session: 异步数据库会话。
            version_id: 待激活的提示词版本 ID。

        Returns:
            激活后的 PromptVersion 实例。

        Raises:
            ValueError: 指定版本不存在或不属于当前项目（由 Repository 抛出）。
        """
        return await self.repo.set_active(ctx, version_id)
