from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AliasChoices, Field, field_validator, model_validator

from knowledge_core.shared.schema import ApiSchema


class CollectionCreate(ApiSchema):
    code: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9-]+$")
    name: str = Field(min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=2000)


class CollectionView(ApiSchema):
    id: UUID
    application_id: UUID
    environment_id: UUID
    code: str
    name: str
    description: str | None
    status: str
    document_count: int
    chunk_count: int
    last_published_at: datetime | None
    created_at: datetime


class TextDocumentCreate(ApiSchema):
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=2_000_000)


class TextRevisionCreate(ApiSchema):
    content: str = Field(min_length=1, max_length=2_000_000)


class RemoteDocumentCreate(ApiSchema):
    title: str = Field(min_length=1, max_length=240)
    url: str = Field(min_length=8, max_length=2000, pattern=r"^https?://")
    source_type: Literal["auto", "web", "api", "feed", "text"] = "auto"
    method: Literal["GET", "POST"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    query_params: dict[str, str] = Field(default_factory=dict)
    json_body: Any = None
    json_path: str | None = Field(default=None, max_length=500)

    @field_validator("headers", "query_params")
    @classmethod
    def validate_string_map(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 30:
            raise ValueError("自定义参数不能超过 30 项")
        if any(len(key) > 100 or len(item) > 2000 for key, item in value.items()):
            raise ValueError("自定义参数名称不能超过 100 字，内容不能超过 2000 字")
        return value

    @model_validator(mode="after")
    def validate_method_and_body(self) -> RemoteDocumentCreate:
        if self.method == "GET" and self.json_body is not None:
            raise ValueError("GET 请求不能填写 JSON 请求体，请改用 POST")
        if (
            self.json_body is not None
            and len(json.dumps(self.json_body, ensure_ascii=False)) > 100_000
        ):
            raise ValueError("JSON 请求体不能超过 100KB")
        return self


class RemotePreviewView(ApiSchema):
    final_url: str
    content_type: str
    size_bytes: int
    status_code: int
    detected_title: str | None
    excerpt: str
    attempts: int


class DocumentView(ApiSchema):
    id: UUID
    collection_id: UUID
    title: str
    mime_type: str | None
    status: str
    current_version: int | None
    source_url: str | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DocumentRevisionView(ApiSchema):
    """文档版本列表的安全视图；只公开状态和摘要，不返回原始对象存储路径。"""

    id: UUID
    document_id: UUID
    version: int
    content_hash: str
    status: str
    char_count: int
    # SQLAlchemy Declarative 基类本身有 ``metadata`` 属性；从 ORM 对象校验时必须读取列属性
    # ``metadata_``，但对外仍保持既有 camelCase/JSON 字段 ``metadata``。
    metadata_: dict[str, Any] = Field(
        # 路由先把 ORM 行转成 ``metadata`` JSON，FastAPI 校验响应模型时会再次读取该 JSON；
        # 因此这里既接收 ORM 属性 ``metadata_``，也接收对外字段 ``metadata``。
        validation_alias=AliasChoices("metadata_", "metadata"),
        serialization_alias="metadata",
    )
    published_at: datetime | None
    created_at: datetime


class DocumentChunkView(ApiSchema):
    """管理员片段预览视图，按已选版本返回文本与定位信息。"""

    id: UUID
    revision_id: UUID
    chunk_index: int
    content: str
    page_number: int | None
    section: str | None
    token_count: int
    # 片段与版本共享同名数据库列，使用同一别名规则避免把 SQLAlchemy 表元数据当作业务 JSON。
    metadata_: dict[str, Any] = Field(
        # 与版本视图保持一致，兼容 ORM 读取与 FastAPI 的二次响应校验。
        validation_alias=AliasChoices("metadata_", "metadata"),
        serialization_alias="metadata",
    )


class IngestionRunView(ApiSchema):
    id: UUID
    document_id: UUID
    revision_id: UUID
    status: str
    stage: str
    progress: int
    error_code: str | None
    error_message: str | None
    retry_count: int
    request_id: str
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class DocumentIngestionQueuedResponse(ApiSchema):
    """资料创建或修订已入队后的文档与入库任务快照。"""

    document: DocumentView
    ingestion_run: IngestionRunView


class ArchiveResponse(ApiSchema):
    """归档资料或集合后的最小确认信息，不暗示已物理删除对象。"""

    archived: bool
    id: str
