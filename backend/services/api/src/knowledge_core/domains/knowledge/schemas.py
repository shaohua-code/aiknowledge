from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

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
