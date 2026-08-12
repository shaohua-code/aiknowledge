from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from knowledge_core.shared.schema import ApiSchema


class ApplicationCreate(ApiSchema):
    code: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9-]+$")
    name: str = Field(min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    application_type: str = Field(default="general", pattern=r"^[a-z][a-z0-9_]+$")


class ApplicationUpdate(ApiSchema):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    status: str | None = Field(default=None, pattern=r"^(active|disabled|archived)$")


class EnvironmentView(ApiSchema):
    id: UUID
    application_id: UUID
    code: str
    name: str
    status: str
    created_at: datetime


class EnvironmentUpdate(ApiSchema):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    status: str | None = Field(default=None, pattern=r"^(active|disabled)$")


class ApplicationView(ApiSchema):
    id: UUID
    code: str
    name: str
    description: str | None
    application_type: str
    status: str
    created_at: datetime
    updated_at: datetime
    environments: list[EnvironmentView] = Field(default_factory=list)


class ApiKeyCreate(ApiSchema):
    name: str = Field(min_length=2, max_length=120)
    scopes: list[str] = Field(min_length=1)
    expires_at: datetime | None = None

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, scopes: list[str]) -> list[str]:
        allowed = {"knowledge:read", "answer:run", "feedback:write", "ingestion:write"}
        unknown = sorted(set(scopes) - allowed)
        if unknown:
            raise ValueError(f"不支持的 Scope：{', '.join(unknown)}")
        return sorted(set(scopes))


class ApiKeyView(ApiSchema):
    id: UUID
    name: str
    key_prefix: str
    scopes: list[str]
    status: str
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class ApiKeyCreated(ApiKeyView):
    secret: str
