"""审计 Repository：Feedback / UsageLog。

对应 SubTask 6.1：用户反馈与使用日志。

设计要点
--------
1. ``Feedback`` 记录用户对研究结果的反馈（helpful / partially_helpful / not_helpful），
   ``request_id`` 关联研究任务的对外请求 ID。
2. ``UsageLog`` 记录每次 API 调用的耗时、token 消耗、降级信息，
   用于成本核算与性能分析。
3. ``UsageLogRepository.aggregate_stats`` 聚合当前项目的调用量、平均耗时、
   错误率、Token 消耗，供仪表盘展示。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from app.db.models.audit import Feedback, UsageLog
from app.db.repositories.base import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.project_context import ProjectContext


# ---------------------------------------------------------------------------
# FeedbackRepository
# ---------------------------------------------------------------------------
class FeedbackRepository(BaseRepository):
    """用户反馈 Repository：操作 ``feedback`` 表。

    强制带 project_id 过滤。
    """

    model = Feedback

    async def create(self, ctx: "ProjectContext", **fields: Any) -> Feedback:
        """创建用户反馈。

        Args:
            ctx: 项目上下文。
            **fields: 反馈字段，必须包含 ``request_id`` / ``rating`` / ``accepted``。

        Returns:
            创建后的 Feedback 实例。
        """
        # project_id 由 ctx 注入
        fields["project_id"] = ctx.project_id
        feedback = Feedback(**fields)
        self.session.add(feedback)
        await self.session.flush()
        return feedback

    async def get_by_request_id(
        self,
        ctx: "ProjectContext",
        request_id: str,
    ) -> Feedback | None:
        """按对外 request_id 查询反馈（一个请求至多一条反馈）。

        Args:
            ctx: 项目上下文。
            request_id: 对外请求 ID（关联研究任务）。

        Returns:
            Feedback 实例；不存在或属于其他项目返回 None。
        """
        # 双重过滤：project_id + request_id
        stmt = select(Feedback).where(
            Feedback.project_id == ctx.project_id,
            Feedback.request_id == request_id,
        )
        return await self.session.scalar(stmt)

    async def list(
        self,
        ctx: "ProjectContext",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Feedback], int]:
        """分页列出当前项目的反馈。

        Args:
            ctx: 项目上下文。
            offset: 分页偏移。
            limit: 每页条数。

        Returns:
            元组 ``(items, total)``。
        """
        # 条件：始终带 project_id
        conditions = [Feedback.project_id == ctx.project_id]

        # 数据查询：按 created_at 倒序
        stmt_data = (
            select(Feedback)
            .where(*conditions)
            .order_by(Feedback.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        items = list((await self.session.execute(stmt_data)).scalars().all())

        # 计数查询
        stmt_count = select(func.count()).select_from(Feedback).where(*conditions)
        total = await self.session.scalar(stmt_count) or 0

        return items, total


# ---------------------------------------------------------------------------
# UsageLogRepository
# ---------------------------------------------------------------------------
class UsageLogRepository(BaseRepository):
    """使用日志 Repository：操作 ``usage_logs`` 表。

    用于成本核算与性能分析。强制带 project_id 过滤。
    """

    model = UsageLog

    async def create(self, ctx: "ProjectContext", **fields: Any) -> UsageLog:
        """创建使用日志。

        Args:
            ctx: 项目上下文。
            **fields: 日志字段，必须包含 ``request_id`` / ``api_key_id`` /
                ``endpoint`` / ``method``。

        Returns:
            创建后的 UsageLog 实例。
        """
        # project_id 由 ctx 注入
        fields["project_id"] = ctx.project_id
        log = UsageLog(**fields)
        self.session.add(log)
        await self.session.flush()
        return log

    async def list_by_project(
        self,
        ctx: "ProjectContext",
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[UsageLog], int]:
        """分页列出当前项目的使用日志。

        Args:
            ctx: 项目上下文。
            offset: 分页偏移。
            limit: 每页条数，默认 50（日志量较大）。

        Returns:
            元组 ``(items, total)``。
        """
        # 条件：始终带 project_id
        conditions = [UsageLog.project_id == ctx.project_id]

        # 数据查询：按 created_at 倒序
        stmt_data = (
            select(UsageLog)
            .where(*conditions)
            .order_by(UsageLog.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        items = list((await self.session.execute(stmt_data)).scalars().all())

        # 计数查询
        stmt_count = select(func.count()).select_from(UsageLog).where(*conditions)
        total = await self.session.scalar(stmt_count) or 0

        return items, total

    async def aggregate_stats(self, ctx: "ProjectContext") -> dict[str, Any]:
        """聚合当前项目的调用量、平均耗时、错误率、Token 消耗。

        返回字段说明：
            - total_calls: 总调用次数
            - avg_total_ms: 平均总耗时（毫秒）
            - error_rate: 错误率（error_code 非空的比例，0~1）
            - total_tokens: 总 token 消耗
            - total_prompt_tokens: 总提示词 token 数
            - total_completion_tokens: 总补全 token 数
            - degraded_rate: 降级率（degraded=true 的比例）

        Args:
            ctx: 项目上下文。

        Returns:
            聚合统计字典。
        """
        # 单次聚合查询：一次往返拿到所有指标，避免多次查询
        # 使用 func.count / func.avg / func.sum / func.count(filter=...) 聚合
        stmt = (
            select(
                # 总调用次数
                func.count().label("total_calls"),
                # 平均总耗时：NULL 值会被 AVG 忽略
                func.avg(UsageLog.total_ms).label("avg_total_ms"),
                # 错误率：error_code 非空的行数 / 总行数
                # 使用 func.count(filter=...) PostgreSQL filter 语法
                func.count(UsageLog.id)
                .filter(UsageLog.error_code.isnot(None))
                .label("error_count"),
                # 总 token 数
                func.coalesce(func.sum(UsageLog.total_tokens), 0).label("total_tokens"),
                # 总提示词 token 数
                func.coalesce(func.sum(UsageLog.prompt_tokens), 0).label(
                    "total_prompt_tokens"
                ),
                # 总补全 token 数
                func.coalesce(func.sum(UsageLog.completion_tokens), 0).label(
                    "total_completion_tokens"
                ),
                # 降级行数
                func.count(UsageLog.id)
                .filter(UsageLog.degraded.is_(True))
                .label("degraded_count"),
            )
            .where(UsageLog.project_id == ctx.project_id)
        )
        result = (await self.session.execute(stmt)).one()

        # 计算比率（避免除零）
        total_calls = result.total_calls or 0
        error_rate = (result.error_count or 0) / total_calls if total_calls > 0 else 0.0
        degraded_rate = (
            (result.degraded_count or 0) / total_calls if total_calls > 0 else 0.0
        )

        return {
            "total_calls": total_calls,
            "avg_total_ms": float(result.avg_total_ms) if result.avg_total_ms else 0.0,
            "error_rate": error_rate,
            "total_tokens": int(result.total_tokens or 0),
            "total_prompt_tokens": int(result.total_prompt_tokens or 0),
            "total_completion_tokens": int(result.total_completion_tokens or 0),
            "degraded_rate": degraded_rate,
        }
