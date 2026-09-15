"""验证检索向量查询必须限定在当前 Embedding 空间。"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from knowledge_core.domains.intelligence.retrieval import RetrievalService
from knowledge_core.shared.context import ApplicationContext


class EmptyResult:
    """模拟 SQLAlchemy 空查询结果，测试只关注生成的过滤条件。"""

    def all(self) -> list:
        """返回空命中集，避免该契约测试依赖真实向量数据。"""
        return []


class StatementSession:
    """记录检索发出的 SQL 语句，验证向量分支加入空间指纹条件。"""

    def __init__(self) -> None:
        self.statements: list[object] = []

    async def execute(self, statement: object) -> EmptyResult:
        """保存查询对象并返回空结果。"""
        self.statements.append(statement)
        return EmptyResult()


@pytest.mark.asyncio
async def test_vector_query_filters_current_embedding_space(monkeypatch) -> None:
    """模型切换后旧版本仍可做文本检索，但不能进入新模型的向量候选集。"""
    session = StatementSession()
    provider = SimpleNamespace(
        configuration=SimpleNamespace(
            provider="openai_compatible",
            model="embedding-current",
            base_url="https://provider.example/v1",
        )
    )

    async def get_provider(_session, _application_id, _environment_id):
        """返回当前模型空间的无网络测试 Provider。"""
        return provider

    async def embed(_texts):
        """返回符合数据库维度的测试向量。"""
        return [[0.1] * 1536]

    provider.embed = embed
    monkeypatch.setattr(
        "knowledge_core.domains.intelligence.retrieval.get_embedding_provider_async",
        get_provider,
    )
    service = RetrievalService(session)
    service.repository = SimpleNamespace()
    profile = SimpleNamespace(
        collection_ids=[str(uuid4())],
        top_k=5,
        metadata_filters={},
        vector_weight=0.7,
        lexical_weight=0.3,
        minimum_score=0.0,
    )
    context = ApplicationContext(
        application_id=uuid4(),
        environment_id=uuid4(),
        application_code="test",
        environment_code="test",
        api_key_id=uuid4(),
        scopes=frozenset({"knowledge:read"}),
    )

    await service.search(context, profile, "测试查询")

    assert len(session.statements) == 2
    vector_sql = str(session.statements[1])
    assert "embedding_fingerprint" in vector_sql
