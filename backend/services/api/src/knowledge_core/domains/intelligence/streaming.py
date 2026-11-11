"""流式回答模块。

提供 Server-Sent Events (SSE) 格式的流式回答能力，
支持增量输出、进度事件和最终结构校验。

事件格式：
    event: chunk
    data: {"requestId": "req_xxx", "delta": "增量文本", "index": 0}

    event: progress
    data: {"requestId": "req_xxx", "stage": "retrieval", "completedMs": 150}

    event: done
    data: {"requestId": "req_xxx", "structuredOutput": {...}, "totalMs": 1200}

    event: error
    data: {"requestId": "req_xxx", "code": "PROVIDER_ERROR", "message": "..."}

使用方式：
    async for event in stream_answer(context, request):
        yield f"event: {event.type}\\ndata: {json.dumps(event.data)}\\n\\n"
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class StreamEventType(StrEnum):
    """流式事件类型枚举。"""

    CHUNK = "chunk"
    PROGRESS = "progress"
    CITATION = "citation"
    DONE = "done"
    ERROR = "error"


@dataclass(slots=True)
class StreamEvent:
    """流式事件。"""

    type: StreamEventType
    data: dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_sse(self) -> str:
        """转换为 Server-Sent Events 格式。"""
        data_json = json.dumps(self.data, ensure_ascii=False)
        return f"event: {self.type.value}\ndata: {data_json}\n\n"


@dataclass(slots=True)
class StreamingConfig:
    """流式回答配置。"""

    # 是否启用流式输出
    enabled: bool = True

    # 增量输出的最小字符数（避免过于频繁的小块）
    min_chunk_chars: int = 10

    # 是否发送进度事件
    emit_progress: bool = True

    # 是否发送引用事件（在检索完成后）
    emit_citations: bool = True

    # 心跳间隔（毫秒），防止连接超时
    heartbeat_interval_ms: int = 15000


@dataclass(slots=True)
class StreamingState:
    """流式回答状态追踪。"""

    request_id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # 阶段耗时
    retrieval_completed_at: datetime | None = None
    model_started_at: datetime | None = None

    # 累积输出
    chunks: list[str] = field(default_factory=list)
    total_chars: int = 0
    chunk_index: int = 0

    # 引用
    citations: list[dict] = field(default_factory=list)

    # 最终结果
    structured_output: dict | None = None
    error: dict | None = None

    def elapsed_ms(self) -> int:
        """计算已用时间（毫秒）。"""
        delta = datetime.now(UTC) - self.started_at
        return int(delta.total_seconds() * 1000)

    def retrieval_ms(self) -> int | None:
        """计算检索阶段耗时。"""
        if self.retrieval_completed_at is None:
            return None
        delta = self.retrieval_completed_at - self.started_at
        return int(delta.total_seconds() * 1000)


def create_chunk_event(
    request_id: str,
    delta: str,
    index: int,
) -> StreamEvent:
    """创建增量文本事件。"""
    return StreamEvent(
        type=StreamEventType.CHUNK,
        data={
            "requestId": request_id,
            "delta": delta,
            "index": index,
        },
    )


def create_progress_event(
    request_id: str,
    stage: str,
    completed_ms: int,
    details: dict | None = None,
) -> StreamEvent:
    """创建进度事件。

    Args:
        request_id: 请求 ID
        stage: 阶段名称 (retrieval, model, validation)
        completed_ms: 该阶段完成时的累计耗时
        details: 额外详情（如检索命中数）
    """
    data = {
        "requestId": request_id,
        "stage": stage,
        "completedMs": completed_ms,
    }
    if details:
        data["details"] = details
    return StreamEvent(type=StreamEventType.PROGRESS, data=data)


def create_citation_event(
    request_id: str,
    citations: list[dict],
) -> StreamEvent:
    """创建引用事件。

    在检索完成后发送，让前端可以提前展示引用来源。
    """
    return StreamEvent(
        type=StreamEventType.CITATION,
        data={
            "requestId": request_id,
            "citations": citations,
        },
    )


def create_done_event(
    request_id: str,
    answer_mode: str,
    structured_output: dict | None,
    confidence: float | None,
    total_ms: int,
    retrieval_ms: int | None,
    model_ms: int | None,
) -> StreamEvent:
    """创建完成事件。

    包含最终的结构化输出和耗时统计。
    """
    return StreamEvent(
        type=StreamEventType.DONE,
        data={
            "requestId": request_id,
            "answerMode": answer_mode,
            "structuredOutput": structured_output,
            "confidence": confidence,
            "timing": {
                "totalMs": total_ms,
                "retrievalMs": retrieval_ms,
                "modelMs": model_ms,
            },
        },
    )


def create_error_event(
    request_id: str,
    code: str,
    message: str,
    retryable: bool = False,
) -> StreamEvent:
    """创建错误事件。"""
    return StreamEvent(
        type=StreamEventType.ERROR,
        data={
            "requestId": request_id,
            "code": code,
            "message": message,
            "retryable": retryable,
        },
    )


def create_heartbeat_event(request_id: str) -> StreamEvent:
    """创建心跳事件。

    使用空 comment 行，不作为正式事件。
    """
    return StreamEvent(
        type=StreamEventType.PROGRESS,
        data={
            "requestId": request_id,
            "stage": "heartbeat",
            "completedMs": 0,
        },
    )


async def aggregate_stream_chunks(
    events: AsyncIterator[StreamEvent],
) -> tuple[str, dict | None, dict | None]:
    """聚合流式事件为最终结果。

    用于测试或需要完整结果的场景。

    Args:
        events: 流式事件迭代器

    Returns:
        (完整回答文本, 结构化输出, 错误信息)
    """
    chunks: list[str] = []
    structured_output: dict | None = None
    error: dict | None = None

    async for event in events:
        if event.type == StreamEventType.CHUNK:
            chunks.append(event.data.get("delta", ""))
        elif event.type == StreamEventType.DONE:
            structured_output = event.data.get("structuredOutput")
        elif event.type == StreamEventType.ERROR:
            error = event.data

    return "".join(chunks), structured_output, error


class StreamingAnswerBuilder:
    """流式回答构建器。

    协助将非流式 LLM 输出转换为流式事件序列。
    实际流式输出依赖 LLM Provider 支持。
    """

    def __init__(
        self,
        request_id: str,
        config: StreamingConfig | None = None,
    ):
        """初始化构建器。

        Args:
            request_id: 请求 ID
            config: 流式配置，None 使用默认配置
        """
        self.request_id = request_id
        self.config = config or StreamingConfig()
        self.state = StreamingState(request_id=request_id)
        self._buffer = ""

    def on_retrieval_complete(
        self,
        citations: list[dict],
    ) -> list[StreamEvent]:
        """处理检索完成。

        Returns:
            要发送的事件列表
        """
        self.state.retrieval_completed_at = datetime.now(UTC)
        self.state.citations = citations

        events: list[StreamEvent] = []

        if self.config.emit_progress:
            events.append(
                create_progress_event(
                    self.request_id,
                    "retrieval",
                    self.state.retrieval_ms() or 0,
                    {"hitCount": len(citations)},
                )
            )

        if self.config.emit_citations and citations:
            events.append(create_citation_event(self.request_id, citations))

        return events

    def on_model_start(self) -> list[StreamEvent]:
        """处理模型开始生成。"""
        self.state.model_started_at = datetime.now(UTC)

        if self.config.emit_progress:
            return [
                create_progress_event(
                    self.request_id,
                    "model",
                    self.state.elapsed_ms(),
                )
            ]
        return []

    def on_text_chunk(self, delta: str) -> list[StreamEvent]:
        """处理增量文本。

        累积小块直到达到最小字符数。
        """
        self._buffer += delta

        if len(self._buffer) >= self.config.min_chunk_chars:
            event = create_chunk_event(
                self.request_id,
                self._buffer,
                self.state.chunk_index,
            )
            self.state.chunks.append(self._buffer)
            self.state.total_chars += len(self._buffer)
            self.state.chunk_index += 1
            self._buffer = ""
            return [event]

        return []

    def on_complete(
        self,
        answer_mode: str,
        structured_output: dict | None,
        confidence: float | None,
    ) -> list[StreamEvent]:
        """处理生成完成。

        发送剩余缓冲和完成事件。
        """
        events: list[StreamEvent] = []

        # 发送剩余缓冲
        if self._buffer:
            events.append(
                create_chunk_event(
                    self.request_id,
                    self._buffer,
                    self.state.chunk_index,
                )
            )
            self.state.chunks.append(self._buffer)
            self._buffer = ""

        # 计算耗时
        total_ms = self.state.elapsed_ms()
        retrieval_ms = self.state.retrieval_ms()
        model_ms = None
        if self.state.model_started_at and retrieval_ms is not None:
            model_ms = total_ms - retrieval_ms

        self.state.structured_output = structured_output

        events.append(
            create_done_event(
                self.request_id,
                answer_mode,
                structured_output,
                confidence,
                total_ms,
                retrieval_ms,
                model_ms,
            )
        )

        return events

    def on_error(
        self,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> list[StreamEvent]:
        """处理错误。"""
        self.state.error = {"code": code, "message": message}
        return [create_error_event(self.request_id, code, message, retryable)]

    def get_full_text(self) -> str:
        """获取已生成的完整文本。"""
        return "".join(self.state.chunks) + self._buffer
