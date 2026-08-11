"""Repository 基类：封装通用数据库操作，强制项目级强隔离。

对应 Task 6 / SubTask 6.1：所有业务 Repository 继承 ``BaseRepository``，
通过 ``self.model`` 指定操作的 ORM 模型，通用方法在 WHERE 子句中强制带
``project_id == ctx.project_id``，从物理层杜绝跨项目数据访问。

设计要点
--------
1. ``BaseRepository`` 仅服务于 ``ProjectOwnedMixin`` 模型（业务表）；
   全局表（``Project`` / ``ToolDefinition``）不继承本类，单独实现。
2. 所有方法第一参数为 ``ctx: ProjectContext``，由 Service 层从依赖注入传递。
3. 通用方法：``get_by_id`` / ``list`` / ``update`` / ``delete`` 等；
   子类可按需覆写或扩展更复杂的查询方法。
4. 使用 SQLAlchemy 2.0 风格：``select()`` + ``session.execute()`` + ``scalars().all()``。
5. 删除策略：默认物理删除（业务表无软删除列），重要业务（如研究任务、检索日志）
   由子类自行决定是否物理删除或归档。
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from sqlalchemy import delete, func, select, update

if TYPE_CHECKING:
    # 仅类型检查时导入，避免运行时循环导入
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.project_context import ProjectContext
    from app.db.models.base import Base as BaseModel


class BaseRepository:
    """Repository 基类：封装通用数据库操作。

    所有业务 Repository 继承此类，子类通过 ``self.model`` 指定操作的 ORM 模型。
    提供 ``get_by_id`` / ``list`` / ``update`` / ``delete`` 等通用方法，
    均强制带 ``project_id`` 过滤，实现项目级强隔离。

    Attributes:
        session: 异步数据库会话（由 Service 层通过依赖注入传入）。
        model: 子类覆写的 ORM 模型类（必须为 ``ProjectOwnedMixin`` 子类）。
    """

    # 子类必须覆写：指定操作的 ORM 模型类
    model: type["BaseModel"]

    def __init__(self, session: "AsyncSession") -> None:
        """初始化 Repository。

        Args:
            session: 异步数据库会话。Service 层通过 ``Depends(get_db)`` 获取后传入。
                Repository 不负责会话的创建与关闭，仅复用上层会话。
        """
        self.session = session

    # ------------------------------------------------------------------
    # 单条查询
    # ------------------------------------------------------------------
    async def get_by_id(self, ctx: "ProjectContext", id: str) -> "BaseModel | None":
        """按 ID 查询单条记录，强制 project_id 过滤。

        为什么要带 project_id 过滤？
            即使客户端传入其他项目的资源 ID，也能在数据库层过滤掉，
            返回 None 由 Service 层抛 404，避免跨项目数据泄露。

        Args:
            ctx: 当前请求的项目上下文，提供 ``project_id``。
            id: 资源主键（UUID 字符串）。

        Returns:
            匹配的 ORM 实例；不存在或属于其他项目时返回 None。
        """
        # 构造 SELECT 语句：WHERE id = ? AND project_id = ?
        # 双条件保证即使 id 泄露到其他项目也无法访问
        stmt = select(self.model).where(
            self.model.id == id,
            self.model.project_id == ctx.project_id,
        )
        # scalar() 返回首行的第一列，无结果返回 None
        return await self.session.scalar(stmt)

    # ------------------------------------------------------------------
    # 列表查询
    # ------------------------------------------------------------------
    async def list(
        self,
        ctx: "ProjectContext",
        offset: int = 0,
        limit: int = 20,
        **filters: Any,
    ) -> tuple[list["BaseModel"], int]:
        """分页列表查询，强制 project_id 过滤。

        Args:
            ctx: 项目上下文，提供 ``project_id``。
            offset: 偏移量，用于分页。
            limit: 每页条数，默认 20。
            **filters: 额外的等值过滤条件，如 ``status="active"``。
                由子类按需调用，避免基类绑定具体字段。

        Returns:
            元组 ``(items, total)``：当前页记录列表与符合条件的总数。
            返回 total 便于客户端展示"共 N 条"。
        """
        # 构造 WHERE 条件列表：始终带 project_id，再追加等值过滤
        conditions = [self.model.project_id == ctx.project_id]
        for field, value in filters.items():
            # 动态获取模型字段，添加等值条件
            conditions.append(getattr(self.model, field) == value)

        # 数据查询：WHERE + ORDER BY created_at DESC + LIMIT/OFFSET
        # 按 created_at 倒序，保证最新记录在前
        stmt_data = (
            select(self.model)
            .where(*conditions)
            .order_by(self.model.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        # scalars().all() 返回 ORM 实例列表
        items = (await self.session.execute(stmt_data)).scalars().all()

        # 计数查询：SELECT COUNT(*) FROM model WHERE ...
        # 使用 select_from 显式指定计数来源，避免 JOIN 场景下计数错误
        stmt_count = select(func.count()).select_from(self.model).where(*conditions)
        total = await self.session.scalar(stmt_count) or 0

        return list(items), total

    # ------------------------------------------------------------------
    # 通用更新
    # ------------------------------------------------------------------
    async def update(
        self,
        ctx: "ProjectContext",
        id: str,
        **fields: Any,
    ) -> "BaseModel | None":
        """按 ID 更新记录，强制 project_id 过滤。

        使用 UPDATE...RETURNING 风格：先 UPDATE 再 SELECT，避免并发场景下
        "先查后改"的竞态条件。WHERE 中带 project_id 保证不越权修改其他项目数据。

        Args:
            ctx: 项目上下文。
            id: 资源主键。
            **fields: 待更新字段，键值对形式。如 ``status="active"``。

        Returns:
            更新后的 ORM 实例；不存在或属于其他项目时返回 None。
        """
        # 空字段保护：避免生成空 UPDATE 语句
        if not fields:
            return await self.get_by_id(ctx, id)

        # 构造 UPDATE 语句：WHERE id = ? AND project_id = ?
        # returning(self.model) 让数据库返回更新后的整行，无需再次 SELECT
        stmt = (
            update(self.model)
            .where(
                self.model.id == id,
                self.model.project_id == ctx.project_id,
            )
            .values(**fields)
            .returning(self.model)
        )
        # 执行 UPDATE 并通过 returning 拿到更新后的行
        result = await self.session.execute(stmt)
        # scalar() 取 returning 的首行
        return result.scalar()

    # ------------------------------------------------------------------
    # 通用删除
    # ------------------------------------------------------------------
    async def delete(self, ctx: "ProjectContext", id: str) -> bool:
        """按 ID 删除记录，强制 project_id 过滤。

        Args:
            ctx: 项目上下文。
            id: 资源主键。

        Returns:
            True 表示删除成功（至少删除 1 行）；False 表示记录不存在或属于其他项目。
        """
        # DELETE 语句：WHERE id = ? AND project_id = ?
        # rowcount 反映删除的行数，0 表示无匹配
        stmt = delete(self.model).where(
            self.model.id == id,
            self.model.project_id == ctx.project_id,
        )
        result = await self.session.execute(stmt)
        # result.rowcount 为受影响行数，>0 表示删除成功
        return (result.rowcount or 0) > 0

    # ------------------------------------------------------------------
    # 计数
    # ------------------------------------------------------------------
    async def count(self, ctx: "ProjectContext", **filters: Any) -> int:
        """统计符合条件的记录数，强制 project_id 过滤。

        Args:
            ctx: 项目上下文。
            **filters: 额外的等值过滤条件。

        Returns:
            符合条件的记录总数。
        """
        # 构造条件：始终带 project_id
        conditions = [self.model.project_id == ctx.project_id]
        for field, value in filters.items():
            conditions.append(getattr(self.model, field) == value)
        # COUNT(*) 查询
        stmt = select(func.count()).select_from(self.model).where(*conditions)
        return await self.session.scalar(stmt) or 0

    # ------------------------------------------------------------------
    # 批量提交辅助方法
    # ------------------------------------------------------------------
    async def _flush(self) -> None:
        """将 pending 状态的 ORM 对象刷入数据库（不提交事务）。

        作用：
            - 触发数据库默认值（如 gen_random_uuid()、now()）填充到 ORM 实例
            - 检查唯一约束/外键约束，提前暴露冲突
            - 不提交事务，便于上层 Service 统一控制事务边界

        Service 层应在创建对象后调用 ``_flush()`` 拿到主键，
        最后由 Service 调用 ``session.commit()`` 提交事务。
        """
        await self.session.flush()
