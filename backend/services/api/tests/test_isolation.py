from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from knowledge_core.domains.intelligence.retrieval import RetrievalService
from knowledge_core.domains.intelligence.schemas import AnswerRequest, RetrieveRequest
from knowledge_core.infrastructure.database import Base
from knowledge_core.shared.context import ApplicationContext


class EmptyResult:
    def all(self) -> list:
        return []


class CapturingSession:
    def __init__(self) -> None:
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return EmptyResult()


@pytest.mark.asyncio
async def test_retrieval_sql_contains_application_and_environment_filters() -> None:
    session = CapturingSession()
    context = ApplicationContext(
        application_id=uuid4(),
        environment_id=uuid4(),
        application_code="resume",
        environment_code="testing",
        api_key_id=uuid4(),
        scopes=frozenset({"knowledge:read"}),
    )
    profile = SimpleNamespace(
        collection_ids=[str(uuid4())],
        top_k=8,
        minimum_score=0.5,
        vector_weight=0.65,
        lexical_weight=0.35,
        metadata_filters={},
    )

    assert await RetrievalService(session).search(context, profile, "隔离测试") == []
    assert len(session.statements) == 2
    for statement in session.statements:
        sql = str(statement.compile(dialect=postgresql.dialect()))
        assert "document_chunks.application_id" in sql
        assert "document_chunks.environment_id" in sql
        assert "document_revisions.application_id" in sql
        assert "documents.environment_id" in sql


def test_runtime_payload_cannot_override_application_context() -> None:
    assert "application_id" not in RetrieveRequest.model_fields
    assert "environment_id" not in RetrieveRequest.model_fields
    assert "application_id" not in AnswerRequest.model_fields
    assert "environment_id" not in AnswerRequest.model_fields


def test_every_application_bound_table_has_context_columns() -> None:
    global_tables = {"applications", "application_environments"}
    for table in Base.metadata.tables.values():
        if table.name in global_tables:
            continue
        assert "application_id" in table.columns, table.name
        assert "environment_id" in table.columns, table.name
