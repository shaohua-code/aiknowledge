"""入库任务模型：IngestionJob。

对应 SubTask 4.1：文档入库流程的状态跟踪。

设计要点
--------
1. 每个 Document 创建后生成一个 IngestionJob，跟踪解析/分块/向量化全流程。
2. ``stage`` 记录当前处理阶段，``status`` 记录整体状态，
   便于后台监控与失败重试。
3. ``error_code`` / ``error_message`` 用于失败原因记录与降级判断。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, ProjectOwnedMixin, TimestampMixin


class IngestionJob(Base, ProjectOwnedMixin, TimestampMixin):
    """入库任务表：跟踪文档解析/分块/向量化流程。

    表名: ingestion_jobs

    字段说明
    --------
    document_id:
        关联文档 ID（同项目），入库流程的目标文档。
    status:
        整体状态：pending=待处理 / parsing=解析中 / chunking=分块中 /
        embedding=向量化中 / ready=就绪 / failed=失败。
    stage:
        当前处理阶段，与 status 配合用于精细监控。
    error_code / error_message:
        失败时记录错误码与错误信息，便于重试与降级。
    started_at / completed_at:
        入库流程开始与完成时间，用于耗时分析。
    """

    __tablename__ = "ingestion_jobs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="入库任务 ID",
    )
    document_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, comment="关联文档 ID"
    )
    # 整体状态：pending / parsing / chunking / embedding / ready / failed
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'pending'"),
        comment="整体状态：pending / parsing / chunking / embedding / ready / failed",
    )
    # 当前处理阶段
    stage: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="当前处理阶段"
    )
    # 错误码：可空，失败时填写
    error_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="错误码"
    )
    # 错误信息：可空，失败时填写
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="错误信息"
    )
    # 开始时间：可空，进入 processing 阶段时填写
    started_at: Mapped[datetime | None] = mapped_column(
        nullable=True, comment="入库流程开始时间"
    )
    # 完成时间：可空，进入 ready/failed 阶段时填写
    completed_at: Mapped[datetime | None] = mapped_column(
        nullable=True, comment="入库流程完成时间"
    )
