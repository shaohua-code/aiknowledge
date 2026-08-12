from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

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
    source_type: str = Field(default="web", pattern=r"^(web|api)$")


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
