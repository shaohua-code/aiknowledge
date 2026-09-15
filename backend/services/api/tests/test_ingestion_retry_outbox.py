"""验证人工重试会保存新的 Outbox 投递意图。"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from knowledge_core.domains.knowledge.service import KnowledgeService


class FakeSession:
    """记录服务提交动作，避免该单元测试依赖真实数据库。"""

    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        """模拟事务提交，供断言重试状态和 Outbox 写入顺序。"""
        self.commits += 1


class FakeRepository:
    """仅实现重试流程读取任务所需的仓储接口。"""

    def __init__(self, run: SimpleNamespace) -> None:
        self.run = run

    async def get_run_by_ids(self, *_args: object) -> SimpleNamespace:
        """返回同一条失败任务，模拟限定应用与环境后的查询结果。"""
        return self.run


@pytest.mark.asyncio
async def test_retry_run_records_a_new_outbox_event_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已发布任务再次失败后，人工重试必须产生可恢复的新派发事件。"""
    application_id = uuid4()
    environment_id = uuid4()
    run = SimpleNamespace(
        id=uuid4(),
        status="failed",
        stage="failed",
        progress=100,
        error_code="provider_unavailable",
        error_message="暂时不可用",
        started_at=object(),
        completed_at=object(),
        retry_count=0,
        request_id="old-request",
    )
    session = FakeSession()
    service = object.__new__(KnowledgeService)
    service.session = session
    service.repository = FakeRepository(run)
    recorded: list[tuple[object, object, object]] = []
    enqueued: list[tuple[object, object, object]] = []

    async def record_event(
        actual_run: object, actual_application: object, actual_environment: object
    ) -> None:
        """记录调用参数，证明新事件发生在本次重试的数据库提交之前。"""
        assert session.commits == 0
        recorded.append((actual_run, actual_application, actual_environment))

    def enqueue(run_id: object, actual_application: object, actual_environment: object) -> None:
        """记录派发器唤醒，避免测试实际连接 Celery。"""
        assert session.commits == 1
        enqueued.append((run_id, actual_application, actual_environment))

    monkeypatch.setattr(service, "_record_ingestion_event", record_event)
    monkeypatch.setattr(service, "_enqueue", enqueue)

    result = await service.retry_run(application_id, environment_id, run.id)

    assert result is run
    assert run.status == "queued"
    assert run.stage == "received"
    assert run.retry_count == 1
    assert recorded == [(run, application_id, environment_id)]
    assert enqueued == [(run.id, application_id, environment_id)]
