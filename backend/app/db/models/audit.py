"""审计相关模型：Feedback / UsageLog。

对应 SubTask 4.1：用户反馈与使用日志。

设计要点
--------
1. ``Feedback`` 记录用户对研究结果的反馈（有用/部分有用/无用），
   ``request_id`` 关联研究任务的对外请求 ID。
2. ``UsageLog`` 记录每次 API 调用的耗时、token 消耗、降级信息，
   用于成本核算与性能分析。
"""
from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, ProjectOwnedMixin, TimestampMixin


class Feedback(Base, ProjectOwnedMixin, TimestampMixin):
    """用户反馈表：对研究结果的满意度评价。

    表名: feedback

    字段说明
    --------
    request_id:
        关联研究任务的对外请求 ID。
    rating:
        评分：helpful=有用 / partially_helpful=部分有用 / not_helpful=无用。
    accepted:
        是否采纳（用户是否基于此结果行动）。
    comment:
        评论文本，可空。
    business_result_id:
        业务结果 ID（可空），用于关联业务侧落地结果，便于效果评估。
    """

    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="反馈 ID",
    )
    # 关联研究任务的对外请求 ID
    request_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="关联研究任务的对外请求 ID"
    )
    # 评分：helpful / partially_helpful / not_helpful
    rating: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="评分：helpful / partially_helpful / not_helpful"
    )
    # 是否采纳
    accepted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, comment="是否采纳"
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True, comment="评论文本")
    # 业务结果 ID：可空，用于关联业务侧落地结果
    business_result_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True, comment="业务结果 ID"
    )


class UsageLog(Base, ProjectOwnedMixin, TimestampMixin):
    """使用日志表：记录每次 API 调用的耗时、token、降级信息。

    表名: usage_logs

    用于成本核算与性能分析，如按项目统计 token 消耗、识别降级热点。

    字段说明
    --------
    request_id:
        对外请求 ID，便于关联研究任务。
    api_key_id:
        调用方 API Key ID，便于按 Key 维度统计。
    endpoint / method:
        API 端点与 HTTP 方法。
    internal_retrieval_ms / external_parallel_ms / generation_ms / total_ms:
        各阶段耗时（毫秒），可空。
    evidence_count:
        证据数，可空。
    prompt_tokens / completion_tokens / total_tokens:
        token 消耗，可空。
    estimated_cost:
        估算成本，可空。
    degraded:
        是否降级。
    degraded_reasons:
        降级原因数组。
    error_code:
        错误码，可空。
    """

    __tablename__ = "usage_logs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="使用日志 ID",
    )
    request_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="对外请求 ID"
    )
    api_key_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, comment="调用方 API Key ID"
    )
    endpoint: Mapped[str] = mapped_column(
        String(120), nullable=False, comment="API 端点"
    )
    # HTTP 方法
    method: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="HTTP 方法"
    )
    # 内部检索耗时（毫秒）
    internal_retrieval_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="内部检索耗时（毫秒）"
    )
    # 外部并行（联网+工具）耗时（毫秒）
    external_parallel_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="外部并行耗时（毫秒）"
    )
    # 大模型生成耗时（毫秒）
    generation_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="大模型生成耗时（毫秒）"
    )
    # 总耗时（毫秒）
    total_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="总耗时（毫秒）"
    )
    # 证据数
    evidence_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="证据数"
    )
    # 提示词 token 数
    prompt_tokens: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="提示词 token 数"
    )
    # 补全 token 数
    completion_tokens: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="补全 token 数"
    )
    # 总 token 数
    total_tokens: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="总 token 数"
    )
    # 估算成本
    estimated_cost: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="估算成本"
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
    error_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="错误码"
    )
