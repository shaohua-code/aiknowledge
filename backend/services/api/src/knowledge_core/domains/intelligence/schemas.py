from __future__ import annotations

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
    minimum_score: float = Field(default=0.55, ge=0, le=1)
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
    request_id: str = Field(min_length=8, max_length=80)
    rating: Literal[-1, 1]
    reason_code: str | None = Field(default=None, max_length=80)
    comment: str | None = Field(default=None, max_length=2000)
