"""研究相关模型：ResearchTask / ResearchEvidence / ResearchResult / RetrievalLog。

对应 SubTask 4.1：短链路一次生成的研究任务与证据、结果、检索日志。

设计要点
--------
1. ``ResearchTask`` 是研究流程的主任务，记录问题、策略、状态、降级信息；
   ``request_id`` 为对外幂等键，UNIQUE。
2. ``ResearchEvidence`` 存储内部检索/联网搜索/业务工具三类证据，
   供大模型引用与裁剪，最多 8 条（PRD 链路限制）。
3. ``ResearchResult`` 存储大模型一次生成的结构化结论（结论/建议/不确定性/风险）。
4. ``RetrievalLog`` 记录每次检索的命中数、耗时、分数，用于性能分析与优化。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, ProjectOwnedMixin, TimestampMixin


class ResearchTask(Base, ProjectOwnedMixin, TimestampMixin):
    """研究任务表：短链路一次生成的主任务。

    表名: research_tasks

    字段说明
    --------
    request_id:
        对外请求 ID，UNIQUE，用于幂等与结果查询。
    question:
        用户问题原文。
    output_type:
        期望输出类型（如 report/bullet/qa），影响提示词模板。
    strategy:
        研究策略：knowledge_only=仅知识库 / knowledge_web=知识库+联网 /
        knowledge_tools=知识库+工具 / full=全开。
    input_context:
        输入上下文（JSONB），如会话历史、用户画像。
    knowledge_base_ids:
        参与检索的知识库 ID 数组。
    requested_tools:
        请求调用的工具 code 数组。
    use_web:
        是否允许联网搜索。
    status:
        任务状态：pending / running / success / partial_success / failed / timeout。
    prompt_version_id:
        使用的提示词版本 ID，可空（未启用版本时使用默认）。
    started_at / completed_at:
        任务开始与完成时间，用于耗时分析。
    total_duration_ms:
        任务总耗时（毫秒）。
    error_code:
        失败错误码，可空。
    degraded:
        是否降级（任一证据源失败时不阻塞请求）。
    degraded_reasons:
        降级原因数组（如 ['web_search_timeout','tool_fund_market_failed']）。
    """

    __tablename__ = "research_tasks"
    __table_args__ = (
        # 索引：按项目+创建时间倒序查询任务列表
        Index(
            "idx_research_tasks_project_created",
            "project_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="研究任务 ID",
    )
    # 对外请求 ID：UNIQUE，幂等键
    request_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, comment="对外请求 ID（UNIQUE，幂等键）"
    )
    question: Mapped[str] = mapped_column(Text, nullable=False, comment="用户问题")
    # 期望输出类型
    output_type: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="期望输出类型"
    )
    # 研究策略：knowledge_only / knowledge_web / knowledge_tools / full
    strategy: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="研究策略：knowledge_only / knowledge_web / knowledge_tools / full",
    )
    # 输入上下文
    input_context: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="输入上下文（JSONB）"
    )
    # 参与检索的知识库 ID 数组
    knowledge_base_ids: Mapped[list[str] | None] = mapped_column(
        ARRAY(UUID(as_uuid=False)), nullable=True, comment="参与检索的知识库 ID 数组"
    )
    # 请求调用的工具 code 数组
    requested_tools: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True, comment="请求调用的工具 code 数组"
    )
    # 是否允许联网搜索
    use_web: Mapped[bool] = mapped_column(
        Boolean, nullable=False, comment="是否允许联网搜索"
    )
    # 任务状态：pending / running / success / partial_success / failed / timeout
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'pending'"),
        comment="任务状态：pending / running / success / partial_success / failed / timeout",
    )
    # 使用的提示词版本 ID：可空
    prompt_version_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), nullable=True, comment="使用的提示词版本 ID"
    )
    started_at: Mapped[datetime | None] = mapped_column(
        nullable=True, comment="任务开始时间"
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        nullable=True, comment="任务完成时间"
    )
    # 总耗时（毫秒）
    total_duration_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="任务总耗时（毫秒）"
    )
    error_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="失败错误码"
    )
    # 是否降级
    degraded: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment="是否降级",
    )
    # 降级原因数组
    degraded_reasons: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True, comment="降级原因数组"
    )


class ResearchEvidence(Base, ProjectOwnedMixin, TimestampMixin):
    """研究证据表：内部检索/联网搜索/业务工具三类证据。

    表名: research_evidence

    字段说明
    --------
    research_task_id:
        关联研究任务 ID（同项目）。
    evidence_type:
        证据类型：internal=内部知识库 / web=联网搜索 / tool=业务工具。
    title / snippet:
        证据标题与摘要，供大模型引用。
    source_url:
        证据来源 URL，可空（内部知识库证据无 URL）。
    published_at:
        证据发布时间，可空。
    data_as_of:
        数据截止时间（如基金净值日期），可空。
    score:
        证据评分（0~1），用于排序与截断。
    metadata_:
        证据元数据（JSONB），如来源、置信度、调用链路等。
    """

    __tablename__ = "research_evidence"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="证据 ID",
    )
    research_task_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, comment="关联研究任务 ID"
    )
    # 证据类型：internal / web / tool
    evidence_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="证据类型：internal / web / tool"
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, comment="证据标题")
    snippet: Mapped[str] = mapped_column(Text, nullable=False, comment="证据摘要")
    source_url: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="证据来源 URL"
    )
    published_at: Mapped[datetime | None] = mapped_column(
        nullable=True, comment="证据发布时间"
    )
    # 数据截止时间：如基金净值日期
    data_as_of: Mapped[datetime | None] = mapped_column(
        nullable=True, comment="数据截止时间"
    )
    # 评分：0~1，用于排序与截断
    score: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="证据评分（0~1）"
    )
    # 元数据
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB, nullable=True, comment="元数据（JSONB）"
    )


class ResearchResult(Base, ProjectOwnedMixin, TimestampMixin):
    """研究结果表：大模型一次生成的结构化结论。

    表名: research_results

    字段说明
    --------
    research_task_id:
        关联研究任务 ID（同项目）。
    answer:
        最终回答文本。
    conclusions:
        结论数组（JSONB），结构化结论列表。
    suggested_actions:
        建议行动数组（JSONB）。
    confidence:
        置信度（0~1）。
    uncertainties:
        不确定性数组（如数据缺失、来源冲突）。
    risk_notice:
        风险提示文本。
    timing:
        各阶段耗时（JSONB）：internalRetrievalMs / externalParallelMs / generationMs / totalMs。
    """

    __tablename__ = "research_results"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="研究结果 ID",
    )
    research_task_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, comment="关联研究任务 ID"
    )
    answer: Mapped[str] = mapped_column(Text, nullable=False, comment="最终回答文本")
    # 结论数组
    conclusions: Mapped[list[Any] | None] = mapped_column(
        JSONB, nullable=True, comment="结论数组（JSONB）"
    )
    # 建议行动数组
    suggested_actions: Mapped[list[Any] | None] = mapped_column(
        JSONB, nullable=True, comment="建议行动数组（JSONB）"
    )
    # 置信度：0~1
    confidence: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="置信度（0~1）"
    )
    # 不确定性数组
    uncertainties: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True, comment="不确定性数组"
    )
    # 风险提示
    risk_notice: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="风险提示文本"
    )
    # 各阶段耗时
    timing: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="各阶段耗时（JSONB）"
    )


class RetrievalLog(Base, ProjectOwnedMixin, TimestampMixin):
    """检索日志表：记录每次检索的命中数、耗时、分数。

    表名: retrieval_logs

    用于性能分析与优化，如对比不同知识库的召回率、不同查询的耗时分布。
    """

    __tablename__ = "retrieval_logs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="检索日志 ID",
    )
    query: Mapped[str] = mapped_column(Text, nullable=False, comment="检索查询")
    # 参与检索的知识库 ID 数组
    knowledge_base_ids: Mapped[list[str] | None] = mapped_column(
        ARRAY(UUID(as_uuid=False)), nullable=True, comment="参与检索的知识库 ID 数组"
    )
    # 命中数
    hit_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="命中数"
    )
    # 检索耗时（毫秒）
    timing_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="检索耗时（毫秒）"
    )
    # 命中分数数组
    scores: Mapped[list[float] | None] = mapped_column(
        ARRAY(Float), nullable=True, comment="命中分数数组"
    )
