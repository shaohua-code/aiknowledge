"""验证异步写入口的请求指纹与幂等结果语义。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from knowledge_core.infrastructure.models import IdempotencyRecord
from knowledge_core.shared.errors import ConflictError
from knowledge_core.shared.idempotency import (
    begin_idempotency,
    complete_idempotency,
    fingerprint_payload,
)


class FakeInsertResult:
    """模拟 PostgreSQL INSERT ... RETURNING 的最小结果，只表达是否由当前请求预占。"""

    def __init__(self, created: bool) -> None:
        self.created = created

    def scalar_one_or_none(self) -> object | None:
        """首次预占返回标识，冲突时返回空值以进入既有记录分支。"""
        return object() if self.created else None


class FakeSession:
    """用最小异步会话替身验证幂等决策，不连接真实业务数据库。"""

    def __init__(self, record: IdempotencyRecord, *, created: bool = False) -> None:
        self.record = record
        self.created = created

    async def execute(self, _statement) -> FakeInsertResult:
        """返回当前请求是否成功预占的结果，不依赖真实数据库。"""
        return FakeInsertResult(self.created)

    async def scalar(self, _statement) -> IdempotencyRecord:
        return self.record


def _record(*, fingerprint: str, state: str = "pending") -> IdempotencyRecord:
    """构造只用于决策测试的幂等记录。"""
    return IdempotencyRecord(
        application_id=uuid4(),
        environment_id=uuid4(),
        operation="knowledge.document.text",
        idempotency_key="key-001",
        request_fingerprint=fingerprint,
        state=state,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        response_status=202 if state == "completed" else None,
        response_body={"success": True} if state == "completed" else None,
    )


def test_fingerprint_is_stable_and_changes_with_payload() -> None:
    """同一语义请求摘要稳定，正文或操作变化会得到不同摘要。"""
    first = fingerprint_payload("knowledge.document.text", {"title": "规范", "content": "A"})
    same = fingerprint_payload("knowledge.document.text", {"content": "A", "title": "规范"})
    changed = fingerprint_payload("knowledge.document.text", {"title": "规范", "content": "B"})

    assert first == same
    assert first != changed


@pytest.mark.asyncio
async def test_same_key_with_different_payload_is_rejected() -> None:
    """相同操作键绑定不同输入时必须冲突，不能静默复用旧任务。"""
    fingerprint = fingerprint_payload("knowledge.document.text", {"content": "A"})
    session = FakeSession(_record(fingerprint=fingerprint))

    with pytest.raises(ConflictError, match="不同请求内容"):
        await begin_idempotency(
            session,
            application_id=session.record.application_id,
            environment_id=session.record.environment_id,
            operation=session.record.operation,
            key=session.record.idempotency_key,
            request_fingerprint=fingerprint_payload(
                "knowledge.document.text", {"content": "B"}
            ),
        )


@pytest.mark.asyncio
async def test_first_key_reservation_allows_the_initial_write() -> None:
    """首次成功插入幂等记录时应继续业务写入，不能误报为处理中。"""
    fingerprint = fingerprint_payload("knowledge.document.text", {"content": "A"})
    session = FakeSession(_record(fingerprint=fingerprint), created=True)

    replay = await begin_idempotency(
        session,
        application_id=session.record.application_id,
        environment_id=session.record.environment_id,
        operation=session.record.operation,
        key=session.record.idempotency_key,
        request_fingerprint=fingerprint,
    )

    assert replay is None


@pytest.mark.asyncio
async def test_completed_same_key_replays_original_body() -> None:
    """已完成请求再次到达时回放原始成功信封，而不是创建第二个任务。"""
    fingerprint = fingerprint_payload("knowledge.document.text", {"content": "A"})
    session = FakeSession(_record(fingerprint=fingerprint, state="completed"))

    replay = await begin_idempotency(
        session,
        application_id=session.record.application_id,
        environment_id=session.record.environment_id,
        operation=session.record.operation,
        key=session.record.idempotency_key,
        request_fingerprint=fingerprint,
    )

    assert replay is not None
    assert replay.status_code == 202
    assert replay.body == {"success": True}


@pytest.mark.asyncio
async def test_completed_body_encodes_datetime_and_uuid_before_storage() -> None:
    """幂等结果落库前必须与 FastAPI 响应使用相同的 JSON 编码规则。"""
    fingerprint = fingerprint_payload("knowledge.document.text", {"content": "A"})
    session = FakeSession(_record(fingerprint=fingerprint))
    resource_id = uuid4()
    created_at = datetime.now(UTC)

    await complete_idempotency(
        session,
        application_id=session.record.application_id,
        environment_id=session.record.environment_id,
        operation=session.record.operation,
        key=session.record.idempotency_key,
        status_code=202,
        body={"id": resource_id, "createdAt": created_at},
    )

    assert session.record.state == "completed"
    assert session.record.response_body == {
        "id": str(resource_id),
        "createdAt": created_at.isoformat(),
    }
