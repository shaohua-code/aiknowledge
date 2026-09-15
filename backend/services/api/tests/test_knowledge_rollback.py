"""验证知识版本回滚的事务编排和审计边界。"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from knowledge_core.domains.knowledge.service import KnowledgeService
from knowledge_core.infrastructure.providers import embedding_space_fingerprint


class FakeSession:
    """模拟服务提交与刷新，避免本单元测试修改真实知识数据。"""

    def __init__(self) -> None:
        self.commits = 0
        self.refreshed: list[object] = []

    async def commit(self) -> None:
        """记录回滚和审计是否在同一次提交中完成。"""
        self.commits += 1

    async def refresh(self, value: object) -> None:
        """模拟提交后重新读取当前文档。"""
        self.refreshed.append(value)


class FakeRepository:
    """提供回滚服务所需的同环境文档、版本和审计接口。"""

    def __init__(self, document: SimpleNamespace, revision: SimpleNamespace) -> None:
        self.document = document
        self.revision = revision
        self.calls: list[tuple[str, object]] = []

    async def get_document(self, _context: object, _document_id: object) -> SimpleNamespace:
        """返回已通过应用环境条件定位的文档。"""
        return self.document

    async def get_document_revision(
        self, *_args: object
    ) -> SimpleNamespace:
        """返回目标版本，实际状态与完整片段由生产仓储校验。"""
        return self.revision

    async def rollback_revision(
        self,
        _context: object,
        document: object,
        revision: object,
        *,
        current_embedding_fingerprint: str,
    ) -> None:
        """记录仓储切换动作，模拟数据库事务内完成指针更新。"""
        assert current_embedding_fingerprint == revision.embedding_fingerprint
        self.calls.append(("rollback", (document, revision)))

    async def create_audit_event(self, **values: object) -> None:
        """记录不包含正文或密钥的回滚审计。"""
        self.calls.append(("audit", values))


@pytest.mark.asyncio
async def test_rollback_switches_revision_and_records_audit_before_commit(monkeypatch) -> None:
    """回滚只切活动版本，且审计事件必须在提交前写入同一事务。"""
    document = SimpleNamespace(id=uuid4(), title="简历规范", collection_id=uuid4())
    provider = SimpleNamespace(
        configuration=SimpleNamespace(
            provider="local_hash", model="local", base_url="local"
        )
    )
    revision = SimpleNamespace(
        id=uuid4(),
        version=2,
        embedding_fingerprint=embedding_space_fingerprint(provider),
    )
    session = FakeSession()
    repository = FakeRepository(document, revision)
    service = object.__new__(KnowledgeService)
    service.session = session
    service.repository = repository

    async def get_provider(_session, _application_id, _environment_id):
        """模拟读取当前数据库模型目录，不访问外部 Provider。"""
        return provider

    monkeypatch.setattr(
        "knowledge_core.domains.knowledge.service.get_embedding_provider_async", get_provider
    )

    result = await service.rollback_document_revision(
        uuid4(), uuid4(), document.id, revision.id, actor="admin@example.com"
    )

    assert result is document
    assert repository.calls[0][0] == "rollback"
    assert repository.calls[1][0] == "audit"
    audit = repository.calls[1][1]
    assert audit["action"] == "document.revision_rolled_back"
    assert audit["details"] == {"documentId": str(document.id), "version": 2}
    assert session.commits == 1
    assert session.refreshed == [document]
