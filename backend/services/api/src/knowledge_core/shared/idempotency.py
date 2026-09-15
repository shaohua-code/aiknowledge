"""提供异步写接口的幂等键预占、结果回放和请求指纹工具。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.infrastructure.models import IdempotencyRecord
from knowledge_core.shared.errors import ConflictError

IDEMPOTENCY_RETENTION = timedelta(hours=24)


@dataclass(frozen=True)
class IdempotencyReplay:
    """已完成请求的原始 HTTP 结果，供客户端安全重试时直接回放。"""

    status_code: int
    body: dict[str, Any]


def fingerprint_payload(operation: str, payload: Any) -> str:
    """对操作和 JSON 语义求摘要；摘要不包含密钥和上传原文。"""
    encoded = json.dumps(
        {"operation": operation, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def begin_idempotency(
    session: AsyncSession,
    *,
    application_id: UUID,
    environment_id: UUID,
    operation: str,
    key: str | None,
    request_fingerprint: str,
) -> IdempotencyReplay | None:
    """预占幂等键；无键时保持兼容，处理中或输入不一致时返回冲突。"""
    normalized_key = (key or "").strip()
    if not normalized_key:
        return None
    if len(normalized_key) > 160:
        raise ConflictError("Idempotency-Key 不能超过 160 个字符")

    now = datetime.now(UTC)
    expires_at = now + IDEMPOTENCY_RETENTION
    # PostgreSQL 唯一约束负责并发抢占；未抢到的请求随后读取同一条记录。
    statement = (
        insert(IdempotencyRecord)
        .values(
            id=uuid4(),
            application_id=application_id,
            environment_id=environment_id,
            operation=operation,
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
            state="pending",
            expires_at=expires_at,
        )
        .on_conflict_do_nothing(
            index_elements=[
                "application_id",
                "environment_id",
                "operation",
                "idempotency_key",
            ]
        )
    )
    # RETURNING 能区分本请求刚预占的记录与并发/历史请求留下的记录；仅查询记录状态
    # 会把首次请求误判为“处理中”，使任何带幂等键的首次写入都无法继续。
    insert_result = await session.execute(statement.returning(IdempotencyRecord.id))
    if insert_result.scalar_one_or_none() is not None:
        return None
    record = await session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.application_id == application_id,
            IdempotencyRecord.environment_id == environment_id,
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.idempotency_key == normalized_key,
        )
    )
    if record is None:
        raise ConflictError("幂等请求暂时无法登记，请稍后重试")
    if record.expires_at < now:
        # 过期记录允许同键重新开始，但仍保留一条记录，避免无界增长。
        record.request_fingerprint = request_fingerprint
        record.state = "pending"
        record.response_status = None
        record.response_body = None
        record.completed_at = None
        record.expires_at = expires_at
        return None
    if record.request_fingerprint != request_fingerprint:
        raise ConflictError("同一个 Idempotency-Key 不能用于不同请求内容")
    if record.state == "completed" and record.response_status and record.response_body:
        return IdempotencyReplay(record.response_status, record.response_body)
    raise ConflictError("相同的 Idempotency-Key 请求正在处理中，请稍后查询任务")


async def complete_idempotency(
    session: AsyncSession,
    *,
    application_id: UUID,
    environment_id: UUID,
    operation: str,
    key: str | None,
    status_code: int,
    body: dict[str, Any],
) -> None:
    """保存可回放结果；响应信封只含资源标识和状态，不保存敏感输入。"""
    normalized_key = (key or "").strip()
    if not normalized_key:
        return
    record = await session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.application_id == application_id,
            IdempotencyRecord.environment_id == environment_id,
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.idempotency_key == normalized_key,
        )
    )
    if record is None:
        raise ConflictError("幂等记录不存在，无法保存响应")
    record.state = "completed"
    record.response_status = status_code
    # FastAPI 返回前会自动编码日期和 UUID；幂等结果先落库，因此这里必须使用同一编码规则。
    record.response_body = jsonable_encoder(body)
    record.completed_at = datetime.now(UTC)
