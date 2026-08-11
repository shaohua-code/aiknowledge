"""调度相关模型：Schedule / ScheduleRun。

对应 SubTask 4.1：定时任务定义与运行记录。

设计要点
--------
1. ``Schedule`` 定义定时任务（cron 表达式 + 任务类型 + 配置），
   ``next_run_at`` 由 Celery Beat 每分钟扫描到期任务。
2. ``ScheduleRun`` 记录每次执行，``planned_at`` 为计划执行时间，
   通过复合唯一约束 ``(project_id, schedule_id, planned_at)`` 实现幂等
   （同一任务同一计划时间只执行一次）。
3. ``concurrency_policy`` 控制并发策略：skip=跳过 / queue=排队。
4. ``max_retries`` 控制失败重试次数。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, ProjectOwnedMixin, TimestampMixin


class Schedule(Base, ProjectOwnedMixin, TimestampMixin):
    """定时任务表：cron 调度定义。

    表名: schedules

    字段说明
    --------
    name:
        任务名称，便于后台识别。
    task_type:
        任务类型：crawl_source=采集源 / tool_sync=工具同步 /
        research_run=研究任务 / reindex_knowledge=重建索引 /
        expire_knowledge=过期清理。
    cron_expression:
        cron 表达式（如 ``0 */6 * * *`` 每 6 小时）。
    timezone:
        时区，默认 ``Asia/Shanghai``。
    config:
        任务配置（JSONB），如采集源 ID、工具 code、知识库 ID 等。
    concurrency_policy:
        并发策略：skip=跳过 / queue=排队。
    timeout_seconds:
        单次执行超时，默认 300s。
    max_retries:
        失败重试次数，默认 2。
    enabled:
        是否启用，false 时 Beat 不调度。
    next_run_at:
        下次执行时间，Beat 扫描此字段。
    last_run_at:
        上次执行时间，用于监控。
    """

    __tablename__ = "schedules"
    __table_args__ = (
        # 部分索引：仅对 enabled=true 的任务建索引，加速 Beat 扫描到期任务
        Index(
            "idx_schedules_due",
            "enabled",
            "next_run_at",
            postgresql_where=text("enabled = true"),
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="定时任务 ID",
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False, comment="任务名称")
    # 任务类型：crawl_source / tool_sync / research_run / reindex_knowledge / expire_knowledge
    task_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="任务类型：crawl_source / tool_sync / research_run / reindex_knowledge / expire_knowledge",
    )
    # cron 表达式
    cron_expression: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="cron 表达式"
    )
    # 时区：默认 Asia/Shanghai
    timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=text("'Asia/Shanghai'"),
        comment="时区",
    )
    # 任务配置
    config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="任务配置（JSONB）"
    )
    # 并发策略：skip / queue
    concurrency_policy: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'skip'"),
        comment="并发策略：skip / queue",
    )
    # 单次执行超时
    timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("300"),
        comment="单次执行超时秒数",
    )
    # 失败重试次数
    max_retries: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("2"),
        comment="失败重试次数",
    )
    # 是否启用
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        comment="是否启用",
    )
    # 下次执行时间：Beat 扫描此字段
    next_run_at: Mapped[datetime | None] = mapped_column(
        nullable=True, comment="下次执行时间"
    )
    # 上次执行时间
    last_run_at: Mapped[datetime | None] = mapped_column(
        nullable=True, comment="上次执行时间"
    )


class ScheduleRun(Base, ProjectOwnedMixin, TimestampMixin):
    """定时任务运行记录表。

    表名: schedule_runs

    复合唯一约束
    ------------
    ``uq_schedule_runs_project_schedule_planned (project_id, schedule_id, planned_at)``：
    保证同一任务同一计划时间只执行一次，实现幂等。

    字段说明
    --------
    schedule_id:
        关联定时任务 ID（同项目）。
    planned_at:
        计划执行时间（由 Beat 根据 cron 推算）。
    started_at / completed_at:
        实际开始与完成时间。
    status:
        运行状态：pending / running / success / failed / timeout。
    attempt:
        重试次数，从 0 开始递增。
    queue_job_id:
        Celery 任务 ID，用于追踪队列。
    result_summary:
        运行结果摘要（JSONB）。
    error_code / error_message:
        失败时记录错误码与错误信息。
    """

    __tablename__ = "schedule_runs"
    __table_args__ = (
        # 复合唯一约束：保证同一任务同一计划时间只执行一次（幂等）
        UniqueConstraint(
            "project_id",
            "schedule_id",
            "planned_at",
            name="uq_schedule_runs_project_schedule_planned",
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="运行记录 ID",
    )
    schedule_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, comment="关联定时任务 ID"
    )
    # 计划执行时间
    planned_at: Mapped[datetime] = mapped_column(
        nullable=False, comment="计划执行时间"
    )
    started_at: Mapped[datetime | None] = mapped_column(
        nullable=True, comment="实际开始时间"
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        nullable=True, comment="实际完成时间"
    )
    # 运行状态：pending / running / success / failed / timeout
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'pending'"),
        comment="运行状态：pending / running / success / failed / timeout",
    )
    # 重试次数：从 0 开始递增
    attempt: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="重试次数",
    )
    # Celery 任务 ID
    queue_job_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True, comment="Celery 任务 ID"
    )
    # 运行结果摘要
    result_summary: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="运行结果摘要（JSONB）"
    )
    error_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="错误码"
    )
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="错误信息"
    )
