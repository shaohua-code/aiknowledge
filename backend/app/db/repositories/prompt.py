"""提示词 Repository：PromptVersion。

对应 SubTask 6.1：研究提示词的多版本管理。

设计要点
--------
1. 每个项目可维护多个提示词版本，但同一时刻仅一个 ``is_active=true``
   （由 PostgreSQL 部分唯一索引 ``uq_prompt_active_per_project`` 保证）。
2. ``create`` 自动递增版本号：先查询当前项目最大版本号，+1 作为新版本号。
3. ``set_active`` 必须在事务内执行：先 UPDATE 取消其他 active，再激活指定版本，
   否则部分唯一索引会冲突。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, update

from app.db.models.prompt import PromptVersion
from app.db.repositories.base import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.project_context import ProjectContext


class PromptRepository(BaseRepository):
    """提示词版本 Repository：操作 ``prompt_versions`` 表。

    强制带 project_id 过滤。版本号在项目内递增，``is_active`` 在项目内唯一
    （由部分唯一索引保证）。
    """

    model = PromptVersion

    async def create(self, ctx: "ProjectContext", **fields: Any) -> PromptVersion:
        """创建新提示词版本，自动递增版本号。

        版本号生成逻辑：
            1. 查询当前项目最大版本号 ``MAX(version)``
            2. 新版本号 = 最大版本号 + 1（若无记录则从 1 开始）
            3. 新版本默认 ``is_active=false``，需显式调用 ``set_active`` 激活

        Args:
            ctx: 项目上下文。
            **fields: 提示词字段，必须包含 ``system_prompt``，可选 ``evidence_rules`` /
                ``output_schema`` / ``prohibitions`` / ``risk_template``。

        Returns:
            创建后的 PromptVersion 实例（含自动生成的 version）。
        """
        # 查询当前项目最大版本号
        stmt_max = (
            select(func.max(PromptVersion.version))
            .where(PromptVersion.project_id == ctx.project_id)
        )
        max_version = await self.session.scalar(stmt_max) or 0
        # 新版本号 = 最大版本号 + 1
        new_version = max_version + 1

        # 构造 ORM 实例，project_id 由 ctx 注入，version 自动生成
        prompt = PromptVersion(
            project_id=ctx.project_id,
            version=new_version,
            is_active=False,  # 新版本默认不激活，需显式调用 set_active
            **fields,
        )
        self.session.add(prompt)
        await self.session.flush()
        return prompt

    async def get_by_id(
        self, ctx: "ProjectContext", id: str
    ) -> PromptVersion | None:
        """按 ID 查询提示词版本（强制 project_id 过滤）。

        Args:
            ctx: 项目上下文。
            id: 提示词版本主键。

        Returns:
            PromptVersion 实例；不存在或属于其他项目返回 None。
        """
        # 双重过滤：id + project_id
        stmt = select(PromptVersion).where(
            PromptVersion.id == id,
            PromptVersion.project_id == ctx.project_id,
        )
        return await self.session.scalar(stmt)

    async def get_active(self, ctx: "ProjectContext") -> PromptVersion | None:
        """获取当前项目的启用版本（is_active=true）。

        每个项目至多一条 is_active=true 的记录（由部分唯一索引保证）。
        若无启用版本，Service 层应使用默认提示词。

        Args:
            ctx: 项目上下文。

        Returns:
            当前启用的 PromptVersion 实例；未启用任何版本返回 None。
        """
        # WHERE project_id = ? AND is_active = true
        stmt = select(PromptVersion).where(
            PromptVersion.project_id == ctx.project_id,
            PromptVersion.is_active.is_(True),
        )
        return await self.session.scalar(stmt)

    async def list_versions(self, ctx: "ProjectContext") -> list[PromptVersion]:
        """列出当前项目的所有提示词版本，按版本号倒序。

        Args:
            ctx: 项目上下文。

        Returns:
            PromptVersion 列表，最新版本在前。
        """
        # 按 version 倒序，便于查看最新版本
        stmt = (
            select(PromptVersion)
            .where(PromptVersion.project_id == ctx.project_id)
            .order_by(PromptVersion.version.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def set_active(self, ctx: "ProjectContext", id: str) -> PromptVersion:
        """激活指定版本（事务内：先取消其他 active，再激活指定版本）。

        为什么必须在事务内？
            ``prompt_versions`` 上有部分唯一索引 ``uq_prompt_active_per_project``：
            ``CREATE UNIQUE INDEX ... ON prompt_versions(project_id) WHERE is_active = true``
            若先激活新版本再取消旧版本，会触发唯一约束冲突。
            必须按"先取消所有 active，再激活指定版本"的顺序执行，
            且两步在同一事务内，避免中间状态被其他请求观察到。

        Args:
            ctx: 项目上下文。
            id: 待激活的提示词版本 ID。

        Returns:
            激活后的 PromptVersion 实例。

        Raises:
            ValueError: 指定版本不存在或不属于当前项目。
        """
        # 步骤 1：校验目标版本存在且属于当前项目
        target = await self.get_by_id(ctx, id)
        if target is None:
            raise ValueError(f"提示词版本 {id} 不存在或不属于当前项目")

        # 步骤 2：取消当前项目下所有 active 版本
        # WHERE project_id = ? AND is_active = true → SET is_active = false
        stmt_deactivate = (
            update(PromptVersion)
            .where(
                PromptVersion.project_id == ctx.project_id,
                PromptVersion.is_active.is_(True),
            )
            .values(is_active=False)
        )
        await self.session.execute(stmt_deactivate)

        # 步骤 3：激活指定版本
        stmt_activate = (
            update(PromptVersion)
            .where(
                PromptVersion.id == id,
                PromptVersion.project_id == ctx.project_id,
            )
            .values(is_active=True)
            .returning(PromptVersion)
        )
        result = await self.session.execute(stmt_activate)
        activated = result.scalar()
        # 理论上不会为 None（步骤 1 已校验），此处防御性处理
        if activated is None:
            raise ValueError(f"激活提示词版本 {id} 失败")
        return activated

    async def update(
        self,
        ctx: "ProjectContext",
        id: str,
        **fields: Any,
    ) -> PromptVersion | None:
        """按 ID 更新提示词版本字段（强制 project_id 过滤）。

        注意：``version`` 创建后不可改，``is_active`` 应通过 ``set_active`` 修改。

        Args:
            ctx: 项目上下文。
            id: 提示词版本主键。
            **fields: 待更新字段。

        Returns:
            更新后的 PromptVersion 实例；不存在或属于其他项目返回 None。
        """
        # 复用基类 update
        return await super().update(ctx, id, **fields)
