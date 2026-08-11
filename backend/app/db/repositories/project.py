"""项目相关 Repository：Project / ApiKey / ProjectSettings。

对应 SubTask 6.1：
- ``ProjectRepository``：操作全局表 ``projects``，不强制 project_id 过滤
  （项目本身就是顶层实体，无 project_id 列）。
- ``ApiKeyRepository``：操作 ``api_keys``，业务方法强制 project_id 过滤；
  但 ``find_by_prefix`` 是鉴权时用的全局查询，不带 project_id。
- ``ProjectSettingsRepository``：操作 ``project_settings``，强制 project_id 过滤。

设计要点
--------
1. 全局表（Project）的 Repository 不继承 BaseRepository，单独实现，
   避免 ``WHERE project_id = ?`` 在无 project_id 列的表上出错。
2. ApiKey 的鉴权查询（find_by_prefix）必须全局扫描，因为鉴权时还不知道
   当前 Key 属于哪个项目，需通过 prefix 匹配候选记录后再 argon2 校验。
3. ProjectSettings 使用 upsert 模式：每个项目至多一条设置记录，
  存在则更新，不存在则插入。
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models.project import ApiKey, Project, ProjectSettings
from app.db.repositories.base import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.project_context import ProjectContext


# ---------------------------------------------------------------------------
# ProjectRepository：全局表，不强制 project_id 过滤
# ---------------------------------------------------------------------------
class ProjectRepository:
    """项目主表 Repository：操作全局表 ``projects``。

    不继承 ``BaseRepository``，因为 ``Project`` 表无 ``project_id`` 列
    （项目本身是顶层实体，PRD 强制要求平台多租户顶层）。
    所有方法不接收 ``ProjectContext``，由管理接口（管理密钥保护）直接调用。
    """

    def __init__(self, session: "AsyncSession") -> None:
        """初始化 Repository。

        Args:
            session: 异步数据库会话。
        """
        self.session = session

    async def create(
        self,
        code: str,
        name: str,
        description: str | None = None,
    ) -> Project:
        """创建项目。

        Args:
            code: 项目编码（CIText 大小写不敏感，UNIQUE），如 ``ai-fund``。
            name: 项目显示名。
            description: 项目描述，可空。

        Returns:
            创建后的 Project 实例（含数据库生成的 id 与 created_at）。
        """
        # 构造 ORM 实例并加入会话
        project = Project(
            code=code,
            name=name,
            description=description,
        )
        self.session.add(project)
        # flush 触发数据库默认值（gen_random_uuid / now）填充到 ORM 实例
        # 由 Service 层负责 commit
        await self.session.flush()
        return project

    async def get_by_id(self, id: str) -> Project | None:
        """按 ID 查询项目。

        Args:
            id: 项目主键（UUID 字符串）。

        Returns:
            Project 实例；不存在返回 None。
        """
        # 全局查询，无 project_id 过滤
        stmt = select(Project).where(Project.id == id)
        return await self.session.scalar(stmt)

    async def get_by_code(self, code: str) -> Project | None:
        """按 code 查询项目（大小写不敏感，由 CIText 保证）。

        Args:
            code: 项目编码，如 ``ai-fund``。

        Returns:
            Project 实例；不存在返回 None。
        """
        # CIText 列在 PostgreSQL 层大小写不敏感
        stmt = select(Project).where(Project.code == code)
        return await self.session.scalar(stmt)

    async def list_all(self, status_filter: str | None = None) -> list[Project]:
        """列出所有项目，可按状态过滤。

        Args:
            status_filter: 状态过滤，如 ``active`` / ``disabled``；
                None 表示不过滤。

        Returns:
            项目列表，按创建时间倒序。
        """
        stmt = select(Project).order_by(Project.created_at.desc())
        if status_filter is not None:
            # 按状态过滤，便于后台只看启用或停用的项目
            stmt = stmt.where(Project.status == status_filter)
        return list((await self.session.execute(stmt)).scalars().all())

    async def update(self, id: str, **fields: object) -> Project | None:
        """按 ID 更新项目字段。

        Args:
            id: 项目主键。
            **fields: 待更新字段。注意 ``code`` 创建后不可改，调用方应避免传入。

        Returns:
            更新后的 Project 实例；不存在返回 None。
        """
        if not fields:
            # 空字段：直接返回当前对象
            return await self.get_by_id(id)
        # UPDATE...RETURNING 风格，避免并发竞态
        stmt = (
            update(Project)
            .where(Project.id == id)
            .values(**fields)
            .returning(Project)
        )
        result = await self.session.execute(stmt)
        return result.scalar()

    async def set_status(self, id: str, status: str) -> Project | None:
        """更新项目状态（active / disabled）。

        停用项目后，所有业务接口的鉴权依赖会抛 ``ProjectDisabledError``。

        Args:
            id: 项目主键。
            status: 目标状态，``active`` 或 ``disabled``。

        Returns:
            更新后的 Project 实例；不存在返回 None。
        """
        # 复用 update 方法，仅修改 status 字段
        return await self.update(id, status=status)


# ---------------------------------------------------------------------------
# ApiKeyRepository：业务方法强制 project_id 过滤
# ---------------------------------------------------------------------------
class ApiKeyRepository(BaseRepository):
    """API Key Repository：操作 ``api_keys`` 表。

    继承 ``BaseRepository``，所有业务方法强制带 ``project_id`` 过滤。
    但 ``find_by_prefix`` 是鉴权时的全局查询，不带 project_id（鉴权时
    还不知道 Key 属于哪个项目）。
    """

    model = ApiKey

    async def create(
        self,
        ctx: "ProjectContext",
        name: str,
        environment: str,
        key_prefix: str,
        key_hash: str,
        scopes: list[str] | None = None,
        expires_at: datetime | None = None,
    ) -> ApiKey:
        """为当前项目创建一个 API Key。

        为什么需要 project_id？
            ``api_keys.project_id`` 由 ``ProjectOwnedMixin`` 提供，
            创建时由 Repository 强制设为 ``ctx.project_id``，禁止客户端传入。

        Args:
            ctx: 项目上下文，提供 ``project_id``。
            name: Key 显示名，便于后台识别。
            environment: 环境，``dev`` / ``staging`` / ``production`` / ``collector``。
            key_prefix: Key 前缀（前 12 位），用于后台识别与候选定位。
            key_hash: Key 哈希（argon2），仅存哈希。
            scopes: 权限范围数组，如 ``["retrieval:read"]``。
            expires_at: 过期时间，None 表示永不过期。

        Returns:
            创建后的 ApiKey 实例（含数据库生成的 id）。
        """
        # project_id 由服务端从 ctx 注入，杜绝请求体覆盖
        api_key = ApiKey(
            project_id=ctx.project_id,
            name=name,
            environment=environment,
            key_prefix=key_prefix,
            key_hash=key_hash,
            scopes=scopes,
            expires_at=expires_at,
            status="active",
        )
        self.session.add(api_key)
        await self.session.flush()
        return api_key

    async def list_by_project(self, ctx: "ProjectContext") -> list[ApiKey]:
        """列出当前项目下所有 API Key。

        Args:
            ctx: 项目上下文。

        Returns:
            ApiKey 列表，按创建时间倒序。
        """
        # 强制 project_id 过滤，杜绝跨项目查看 Key
        stmt = (
            select(ApiKey)
            .where(ApiKey.project_id == ctx.project_id)
            .order_by(ApiKey.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_by_id(self, ctx: "ProjectContext", id: str) -> ApiKey | None:
        """按 ID 查询 API Key（强制 project_id 过滤）。

        Args:
            ctx: 项目上下文。
            id: ApiKey 主键。

        Returns:
            ApiKey 实例；不存在或属于其他项目返回 None。
        """
        # WHERE id = ? AND project_id = ?，杜绝跨项目访问
        stmt = select(ApiKey).where(
            ApiKey.id == id,
            ApiKey.project_id == ctx.project_id,
        )
        return await self.session.scalar(stmt)

    async def find_by_prefix(self, prefix: str) -> list[ApiKey]:
        """按 key_prefix 全局查询候选 Key（鉴权用，不带 project_id）。

        为什么这个方法不带 project_id？
            鉴权依赖在解析 Authorization 头时，还不知道当前 Key 属于哪个项目。
            必须先按 prefix 全局定位候选记录，再逐个 argon2 校验，
            匹配后才能从 Key 行的 project_id 反查所属项目。

        Args:
            prefix: Key 前缀（前 12 位）。

        Returns:
            候选 ApiKey 列表（status=active），可能为多条（不同项目可能用相同前缀）。
        """
        # 全局查询：仅按 prefix + status 过滤
        # status='active' 排除已吊销的 Key，减少无效 argon2 校验
        stmt = select(ApiKey).where(
            ApiKey.key_prefix == prefix,
            ApiKey.status == "active",
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def update_last_used(self, ctx: "ProjectContext", id: str) -> None:
        """更新 API Key 的最近使用时间。

        Args:
            ctx: 项目上下文。
            id: ApiKey 主键。
        """
        # WHERE id = ? AND project_id = ?，避免越权更新其他项目的 Key
        stmt = (
            update(ApiKey)
            .where(
                ApiKey.id == id,
                ApiKey.project_id == ctx.project_id,
            )
            .values(last_used_at=datetime.utcnow())
        )
        await self.session.execute(stmt)

    async def revoke(self, ctx: "ProjectContext", id: str) -> ApiKey | None:
        """吊销 API Key（status 改为 revoked，不可恢复）。

        Args:
            ctx: 项目上下文。
            id: ApiKey 主键。

        Returns:
            更新后的 ApiKey 实例；不存在或属于其他项目返回 None。
        """
        # 复用基类 update 方法，将 status 改为 revoked
        return await self.update(ctx, id, status="revoked")


# ---------------------------------------------------------------------------
# ProjectSettingsRepository：每个项目至多一条设置记录
# ---------------------------------------------------------------------------
class ProjectSettingsRepository(BaseRepository):
    """项目设置 Repository：操作 ``project_settings`` 表。

    每个项目至多一条设置记录，使用 upsert 模式：
    存在则更新，不存在则插入。强制带 project_id 过滤。
    """

    model = ProjectSettings

    async def get_by_project(self, ctx: "ProjectContext") -> ProjectSettings | None:
        """查询当前项目的设置记录。

        Args:
            ctx: 项目上下文。

        Returns:
            ProjectSettings 实例；未配置过返回 None。
        """
        # 每个项目至多一条，按 project_id 查询即可
        stmt = select(ProjectSettings).where(
            ProjectSettings.project_id == ctx.project_id,
        )
        return await self.session.scalar(stmt)

    async def upsert(self, ctx: "ProjectContext", **fields: object) -> ProjectSettings:
        """更新或插入项目设置（upsert 语义）。

        使用 PostgreSQL ``INSERT ... ON CONFLICT`` 实现真正的 upsert，
        避免应用层"先查后改"的竞态条件。
        注意：本表无 project_id 上的唯一约束（每项目至多一条由 Service 层保证），
        实际实现采用"先查再 update / insert"两步法，由 Service 层控制并发。

        Args:
            ctx: 项目上下文。
            **fields: 待更新字段，如 ``chat_model``、``web_search_enabled`` 等。

        Returns:
            更新或插入后的 ProjectSettings 实例。
        """
        # 先查询是否存在记录
        existing = await self.get_by_project(ctx)
        if existing is not None:
            # 已存在：更新字段
            for key, value in fields.items():
                setattr(existing, key, value)
            await self.session.flush()
            return existing

        # 不存在：插入新记录，project_id 由 ctx 注入
        settings = ProjectSettings(
            project_id=ctx.project_id,
            **fields,  # type: ignore[arg-type]
        )
        self.session.add(settings)
        await self.session.flush()
        return settings
