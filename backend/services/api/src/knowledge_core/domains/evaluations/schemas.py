"""评测集与样本修订的控制面输入、输出契约。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import Field, model_validator

from knowledge_core.shared.schema import ApiSchema


class EvaluationSetCreate(ApiSchema):
    """创建固定评测集时使用的元数据，不包含任何运行结果。"""

    code: str = Field(min_length=2, max_length=80, pattern=r"^[a-z][a-z0-9_]+$")
    name: str = Field(min_length=2, max_length=160)
    description: str = Field(default="", max_length=2000)


class EvaluationSetView(EvaluationSetCreate):
    """控制台展示的评测集摘要。"""

    id: UUID
    status: str


class EvaluationCaseCreate(ApiSchema):
    """新增样本会创建第一个不可变修订，输入需已完成脱敏。"""

    case_code: str = Field(min_length=2, max_length=80, pattern=r"^[a-z][a-z0-9_]+$")
    question: str = Field(min_length=1, max_length=20_000)
    inputs: dict[str, Any] = Field(default_factory=dict)
    expected_document_ids: list[UUID] = Field(default_factory=list, max_length=50)
    expected_answer_modes: list[str] = Field(default_factory=list, max_length=8)
    expected_output_schema: dict[str, Any] = Field(default_factory=dict)
    labels: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_expected_schema(self):
        """将输出结构检查前置到样本创建，避免评测运行时才发现无效门槛。"""
        if self.expected_output_schema:
            try:
                Draft202012Validator.check_schema(self.expected_output_schema)
            except SchemaError as exc:
                raise ValueError(
                    f"expectedOutputSchema 不是有效的 JSON Schema：{exc.message}"
                ) from exc
        return self


class EvaluationCaseRevisionView(EvaluationCaseCreate):
    """不可变样本修订的控制面视图。"""

    id: UUID
    evaluation_set_id: UUID
    revision: int


class EvaluationRunCreate(ApiSchema):
    """发起评测必须明确指定候选回答修订，避免默认测到生产版本。"""

    answer_profile_revision_id: UUID


class EvaluationRunView(ApiSchema):
    """评测运行状态和门禁摘要；通过不等于主观质量已人工验收。"""

    id: UUID
    evaluation_set_id: UUID
    answer_profile_revision_id: UUID
    retrieval_profile_revision_id: UUID
    status: str
    gate_passed: bool | None
    summary: dict[str, Any]


class EvaluationCaseResultView(ApiSchema):
    """单样本执行和确定性检查结果，供控制台定位失败原因。"""

    id: UUID
    case_revision_id: UUID
    status: str
    checks: dict[str, Any]
    error_code: str | None


class BulkCaseImportRequest(ApiSchema):
    """批量导入评测样本请求，支持从 JSON 数组批量创建。"""

    cases: list[EvaluationCaseCreate] = Field(min_length=1, max_length=100)
    skip_existing: bool = Field(
        default=True,
        description="跳过已存在的样本编码；为 False 时遇到重复会返回冲突错误"
    )


class BulkCaseImportResponse(ApiSchema):
    """批量导入评测样本响应。"""

    imported: int
    skipped: int
    case_codes: list[str]
