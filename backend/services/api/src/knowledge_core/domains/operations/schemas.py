from __future__ import annotations

from typing import Any

from knowledge_core.shared.schema import ApiSchema


class OperationSummaryResponse(ApiSchema):
    """运行中心概览指标，明确时间窗口且排除运行中请求的成功率。"""

    total_requests: int
    completed_requests: int
    running_requests: int
    failed_requests: int
    success_rate: float | None
    average_duration_ms: int | None
    model_fallback_rate: float | None
    window_started_at: str
    window_ended_at: str


class OperationTraceView(ApiSchema):
    """运行轨迹列表行，保留定位故障所需的摘要而不返回请求原文。"""

    id: str
    request_id: str
    operation: str
    profile_code: str | None
    answer_profile_revision_id: str | None
    retrieval_profile_revision_id: str | None
    status: str
    answer_mode: str | None
    confidence: float | None
    evidence_count: int
    degraded: bool
    degraded_reasons: list[str]
    total_ms: int | None
    retrieval_ms: int | None
    model_ms: int | None
    web_ms: int | None
    input_tokens: int
    output_tokens: int
    error_code: str | None
    created_at: str


class EvidenceView(ApiSchema):
    """单条回答证据的安全展示字段，不包含原始知识对象内容。"""

    source_type: str
    title: str
    excerpt: str
    score: float
    citation: dict[str, Any]


class OperationTraceDetailResponse(ApiSchema):
    """按请求标识查询的运行详情及其已保存证据。"""

    request_id: str
    operation: str
    profile_code: str | None
    answer_profile_revision_id: str | None
    retrieval_profile_revision_id: str | None
    status: str
    answer_mode: str | None
    confidence: float | None
    degraded: bool
    degraded_reasons: list[str]
    total_ms: int | None
    retrieval_ms: int | None
    model_ms: int | None
    web_ms: int | None
    input_tokens: int
    output_tokens: int
    error_code: str | None
    created_at: str
    evidence: list[EvidenceView]


class AuditEventView(ApiSchema):
    """审计事件列表行，保留操作者、资源和已脱敏上下文。"""

    id: str
    actor: str
    action: str
    resource_type: str
    resource_id: str
    request_id: str
    details: dict[str, Any]
    created_at: str


class OperationFeedbackView(ApiSchema):
    """控制台查看的调用方反馈，关联请求标识但不暴露请求正文。"""

    id: str
    request_id: str
    rating: int
    reason_code: str | None
    comment: str | None
    created_at: str


class IngestionAttemptView(ApiSchema):
    """入库 Worker 单次尝试的脱敏诊断视图，供运行中心定位任务失败。"""

    id: str
    run_id: str
    request_id: str
    attempt_number: int
    status: str
    stage: str
    error_code: str | None
    error_message: str | None
    started_at: str
    completed_at: str | None
    created_at: str
