"""集合概览只聚合当前发布版本片段的仓储回归测试。"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from sqlalchemy.dialects import postgresql

from knowledge_core.domains.knowledge.repository import KnowledgeRepository
from knowledge_core.shared.context import ApplicationContext


class RecordingSession:
    """记录仓储构造的 SQL 表达式，无需连接真实数据库即可校验聚合边界。"""

    def __init__(self) -> None:
        self.scalar_statements: list[object] = []
        self._scalar_results = iter((1, 2))

    async def execute(self, _statement: object) -> SimpleNamespace:
        """模拟条件发布成功，令测试聚焦后续集合统计查询。"""
        return SimpleNamespace(rowcount=1)

    async def scalar(self, statement: object) -> int:
        """记录两条集合聚合查询，并提供非零计数结果。"""
        self.scalar_statements.append(statement)
        return next(self._scalar_results)


async def test_publish_revision_counts_only_current_ready_revision_chunks(monkeypatch) -> None:
    """发布时生成的片段统计 SQL 必须排除同文档历史 ready 修订。"""
    application_id = UUID("00000000-0000-0000-0000-000000000401")
    environment_id = UUID("00000000-0000-0000-0000-000000000402")
    collection_id = UUID("00000000-0000-0000-0000-000000000403")
    session = RecordingSession()
    repository = KnowledgeRepository(session)  # type: ignore[arg-type]
    collection = SimpleNamespace(id=collection_id)

    async def get_collection(*_args: object) -> SimpleNamespace:
        """发布聚合前返回当前集合，不触发数据库读取。"""
        return collection

    monkeypatch.setattr(repository, "get_collection", get_collection)
    await repository.publish_revision(
        ApplicationContext(
            application_id=application_id,
            environment_id=environment_id,
            application_code="collection-count-test",
            environment_code="test",
            api_key_id=UUID(int=0),
            scopes=frozenset(),
        ),
        SimpleNamespace(
            id=UUID("00000000-0000-0000-0000-000000000404"),
            collection_id=collection_id,
        ),
        SimpleNamespace(version=2, storage_key="safe/current-version", status="draft"),
        SimpleNamespace(status="running", stage="received", progress=0, completed_at=None),
        chunk_count=2,
        char_count=16,
    )

    # 第二条 scalar 是 document_chunks 聚合；编译 PostgreSQL 方言可直接验证实际条件。
    chunk_count_sql = str(
        session.scalar_statements[1].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "document_revisions.version = documents.current_version" in chunk_count_sql
    assert collection.document_count == 1
    assert collection.chunk_count == 2
