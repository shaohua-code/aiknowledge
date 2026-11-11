from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import Field, model_validator

from knowledge_core.shared.schema import ApiSchema


class RetrievalProfileCreate(ApiSchema):
    code: str = Field(min_length=2, max_length=80, pattern=r"^[a-z][a-z0-9_]+$")
    name: str = Field(min_length=2, max_length=160)
    collection_ids: list[UUID] = Field(min_length=1)
    top_k: int = Field(default=8, ge=1, le=30)
    # 默认 0.2：混合得分 = 向量*0.65 + 词法*0.35，短问题词法分通常为 0，
    # 0.55 要求余弦相似度≥0.85，中文真实语料几乎达不到，会把有效证据全部过滤
    minimum_score: float = Field(default=0.2, ge=0, le=1)
    vector_weight: float = Field(default=0.65, ge=0, le=1)
    lexical_weight: float = Field(default=0.35, ge=0, le=1)
    metadata_filters: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_weights(self):
        if abs(self.vector_weight + self.lexical_weight - 1) > 0.001:
            raise ValueError("vectorWeight 与 lexicalWeight 之和必须等于 1")
        return self


class RetrievalProfileView(ApiSchema):
    id: UUID
    code: str
    name: str
    collection_ids: list[str]
    top_k: int
    minimum_score: float
    vector_weight: float
    lexical_weight: float
    metadata_filters: dict[str, str | int | float | bool]
    status: str


class AnswerProfileCreate(ApiSchema):
    code: str = Field(min_length=2, max_length=80, pattern=r"^[a-z][a-z0-9_]+$")
    name: str = Field(min_length=2, max_length=160)
    retrieval_profile_id: UUID
    system_prompt: str = Field(default="", max_length=20_000)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    tool_codes: list[str] = Field(default_factory=list, max_length=30)
    knowledge_required: bool = False
    model_fallback_allowed: bool = True
    web_fallback_allowed: bool = False
    minimum_evidence_count: int = Field(default=1, ge=0, le=20)
    minimum_evidence_score: float = Field(default=0.55, ge=0, le=1)
    require_fresh_data: bool = False
    maximum_data_age_seconds: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_output_schema(self):
        if self.output_schema:
            try:
                Draft202012Validator.check_schema(self.output_schema)
            except SchemaError as exc:
                raise ValueError(f"outputSchema 不是有效的 JSON Schema：{exc.message}") from exc
        return self


class AnswerProfileView(AnswerProfileCreate):
    id: UUID
    status: str


class RetrieveRequest(ApiSchema):
    query: str = Field(min_length=1, max_length=10_000)
    profile: str = Field(min_length=2, max_length=80)
    top_k: int | None = Field(default=None, ge=1, le=30)


class AnswerOptions(ApiSchema):
    include_citations: bool = True
    include_evidence: bool = False


class AnswerRequest(ApiSchema):
    profile: str = Field(min_length=2, max_length=80)
    query: str = Field(min_length=1, max_length=20_000)
    inputs: dict[str, Any] = Field(default_factory=dict)
    options: AnswerOptions = Field(default_factory=AnswerOptions)


class FeedbackRequest(ApiSchema):
    """用户反馈请求体，关联到运行请求 ID 并记录评价和原因。"""

    request_id: str = Field(min_length=8, max_length=80)
    rating: Literal[-1, 1]
    reason_code: str | None = Field(default=None, max_length=80)
    comment: str | None = Field(default=None, max_length=2000)


class AnswerDebugRequest(ApiSchema):
    """管理员控制面调试请求，不需要业务 API Key。"""

    query: str = Field(min_length=1, max_length=20_000)
    inputs: dict[str, Any] = Field(default_factory=dict)


class AnswerResponse(ApiSchema):
    """回答响应体，包含回答内容、结构化输出、证据与诊断信息。"""

    request_id: str = Field(alias="requestId")
    answer_mode: str = Field(alias="answerMode")
    answer: str
    structured_output: dict[str, Any] = Field(default_factory=dict, alias="structuredOutput")
    confidence: float
    warnings: list[str] = Field(default_factory=list)
    knowledge: dict[str, Any] = Field(default_factory=dict)
    web: dict[str, Any] = Field(default_factory=dict)
    model_supplement: dict[str, Any] = Field(default_factory=dict, alias="modelSupplement")
    degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list, alias="degradedReasons")
    usage: dict[str, int] = Field(default_factory=dict)
    timing: dict[str, int] = Field(default_factory=dict)


class RetrievalProfileRevisionDraft(ApiSchema):
    """检索策略修订草稿，仅包含可更新字段。"""

    collection_ids: list[UUID] = Field(min_length=1)
    top_k: int = Field(default=8, ge=1, le=30)
    minimum_score: float = Field(default=0.2, ge=0, le=1)
    vector_weight: float = Field(default=0.65, ge=0, le=1)
    lexical_weight: float = Field(default=0.35, ge=0, le=1)
    metadata_filters: dict[str, str | int | float | bool] = Field(default_factory=dict)
    # 可选开关。不传时不写入发布快照，查询改写和重排序保持关闭。
    rag_enhancement: dict[str, Any] | None = Field(
        default=None,
        description="可选 RAG 增强配置。默认不启用查询改写和重排序。",
    )

    @model_validator(mode="after")
    def validate_weights(self):
        """校验向量和词法权重之和必须等于 1。"""
        if abs(self.vector_weight + self.lexical_weight - 1) > 0.001:
            raise ValueError("vectorWeight 与 lexicalWeight 之和必须等于 1")
        return self


class AnswerProfileRevisionDraft(ApiSchema):
    """回答策略修订草稿，绑定到特定的检索修订版本。"""

    retrieval_revision_id: UUID
    system_prompt: str = Field(default="", max_length=20_000)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    tool_codes: list[str] = Field(default_factory=list, max_length=30)
    knowledge_required: bool = False
    model_fallback_allowed: bool = True
    web_fallback_allowed: bool = False
    minimum_evidence_count: int = Field(default=1, ge=0, le=20)
    minimum_evidence_score: float = Field(default=0.55, ge=0, le=1)
    require_fresh_data: bool = False
    maximum_data_age_seconds: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_output_schema(self):
        """校验输出 Schema 是否为有效的 JSON Schema。"""
        if self.output_schema:
            try:
                Draft202012Validator.check_schema(self.output_schema)
            except SchemaError as exc:
                raise ValueError(f"outputSchema 不是有效的 JSON Schema：{exc.message}") from exc
        return self


class ProfileRevisionView(ApiSchema):
    """策略修订视图，展示修订版本、状态和配置快照。"""

    id: UUID
    profile_id: UUID
    revision: int
    state: str
    configuration: dict[str, Any]
    published_at: datetime | None = None
    created_at: datetime


class ProfileReleaseView(ApiSchema):
    """发布事件视图，记录发布或回滚的不可变事件。"""

    id: UUID
    profile_kind: str
    profile_id: UUID
    revision_id: UUID
    action: str
    created_at: datetime


class RetrieveResponse(ApiSchema):
    """检索响应体，返回命中的知识片段列表及耗时统计。"""

    query: str
    hits: list[dict[str, Any]]
    total_hits: int = Field(alias="totalHits")
    elapsed_ms: int = Field(alias="elapsedMs")


class FeedbackAcceptedResponse(ApiSchema):
    """反馈已接受响应，返回反馈记录 ID。"""

    feedback_id: str = Field(alias="feedbackId")
    accepted: bool = True
