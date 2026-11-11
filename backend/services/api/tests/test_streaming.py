"""流式回答模块测试。

验证流式事件生成、状态追踪和聚合功能。
"""

from __future__ import annotations

import pytest

from knowledge_core.domains.intelligence.streaming import (
    StreamEventType,
    StreamingAnswerBuilder,
    StreamingConfig,
    create_chunk_event,
    create_citation_event,
    create_done_event,
    create_error_event,
    create_progress_event,
)


class TestStreamEvents:
    """流式事件创建测试。"""

    def test_chunk_event_format(self) -> None:
        """增量事件应包含正确的字段。"""
        event = create_chunk_event("req_123", "增量文本", 0)

        assert event.type == StreamEventType.CHUNK
        assert event.data["requestId"] == "req_123"
        assert event.data["delta"] == "增量文本"
        assert event.data["index"] == 0

    def test_chunk_event_sse_format(self) -> None:
        """增量事件应正确转换为 SSE 格式。"""
        event = create_chunk_event("req_123", "Hello", 0)
        sse = event.to_sse()

        assert "event: chunk" in sse
        assert '"requestId": "req_123"' in sse
        assert '"delta": "Hello"' in sse
        assert sse.endswith("\n\n")

    def test_progress_event_with_details(self) -> None:
        """进度事件应支持额外详情。"""
        event = create_progress_event(
            "req_123",
            "retrieval",
            150,
            {"hitCount": 5},
        )

        assert event.type == StreamEventType.PROGRESS
        assert event.data["stage"] == "retrieval"
        assert event.data["completedMs"] == 150
        assert event.data["details"]["hitCount"] == 5

    def test_citation_event_format(self) -> None:
        """引用事件应包含引用列表。"""
        citations = [
            {"documentId": "doc_1", "title": "文档一"},
            {"documentId": "doc_2", "title": "文档二"},
        ]
        event = create_citation_event("req_123", citations)

        assert event.type == StreamEventType.CITATION
        assert len(event.data["citations"]) == 2

    def test_done_event_includes_timing(self) -> None:
        """完成事件应包含耗时统计。"""
        event = create_done_event(
            request_id="req_123",
            answer_mode="KNOWLEDGE_GROUNDED",
            structured_output={"score": 85},
            confidence=0.92,
            total_ms=1200,
            retrieval_ms=150,
            model_ms=1000,
        )

        assert event.type == StreamEventType.DONE
        assert event.data["answerMode"] == "KNOWLEDGE_GROUNDED"
        assert event.data["structuredOutput"]["score"] == 85
        assert event.data["confidence"] == 0.92
        assert event.data["timing"]["totalMs"] == 1200
        assert event.data["timing"]["retrievalMs"] == 150
        assert event.data["timing"]["modelMs"] == 1000

    def test_error_event_format(self) -> None:
        """错误事件应包含错误码和可重试标识。"""
        event = create_error_event(
            "req_123",
            "PROVIDER_ERROR",
            "模型服务不可用",
            retryable=True,
        )

        assert event.type == StreamEventType.ERROR
        assert event.data["code"] == "PROVIDER_ERROR"
        assert event.data["message"] == "模型服务不可用"
        assert event.data["retryable"] is True


class TestStreamingAnswerBuilder:
    """流式回答构建器测试。"""

    def test_default_config(self) -> None:
        """默认配置应启用流式输出。"""
        builder = StreamingAnswerBuilder("req_123")

        assert builder.config.enabled is True
        assert builder.config.emit_progress is True
        assert builder.config.emit_citations is True

    def test_retrieval_complete_emits_events(self) -> None:
        """检索完成应发送进度和引用事件。"""
        builder = StreamingAnswerBuilder("req_123")
        citations = [{"documentId": "doc_1", "title": "测试文档"}]

        events = builder.on_retrieval_complete(citations)

        assert len(events) == 2
        assert events[0].type == StreamEventType.PROGRESS
        assert events[0].data["stage"] == "retrieval"
        assert events[1].type == StreamEventType.CITATION

    def test_retrieval_complete_without_citations(self) -> None:
        """无引用时不发送引用事件。"""
        builder = StreamingAnswerBuilder("req_123")

        events = builder.on_retrieval_complete([])

        # 只有进度事件
        assert len(events) == 1
        assert events[0].type == StreamEventType.PROGRESS

    def test_text_chunk_buffering(self) -> None:
        """小块文本应被缓冲。"""
        config = StreamingConfig(min_chunk_chars=10)
        builder = StreamingAnswerBuilder("req_123", config)

        # 小于最小字符数，应被缓冲
        events = builder.on_text_chunk("Hi")
        assert len(events) == 0

        # 累积后超过最小字符数，应发送
        events = builder.on_text_chunk(" there, world!")
        assert len(events) == 1
        assert events[0].data["delta"] == "Hi there, world!"

    def test_complete_flushes_buffer(self) -> None:
        """完成时应发送剩余缓冲。"""
        config = StreamingConfig(min_chunk_chars=100)
        builder = StreamingAnswerBuilder("req_123", config)

        # 添加小块文本
        builder.on_text_chunk("小块文本")

        # 完成时应发送缓冲和完成事件
        events = builder.on_complete(
            answer_mode="KNOWLEDGE_GROUNDED",
            structured_output=None,
            confidence=0.8,
        )

        assert len(events) == 2
        assert events[0].type == StreamEventType.CHUNK
        assert events[0].data["delta"] == "小块文本"
        assert events[1].type == StreamEventType.DONE

    def test_error_handling(self) -> None:
        """错误应正确记录和发送。"""
        builder = StreamingAnswerBuilder("req_123")

        events = builder.on_error(
            "TIMEOUT",
            "请求超时",
            retryable=True,
        )

        assert len(events) == 1
        assert events[0].type == StreamEventType.ERROR
        assert builder.state.error is not None

    def test_get_full_text(self) -> None:
        """应能获取完整的已生成文本。"""
        config = StreamingConfig(min_chunk_chars=5)
        builder = StreamingAnswerBuilder("req_123", config)

        builder.on_text_chunk("Hello ")
        builder.on_text_chunk("World")
        builder.on_text_chunk("!")  # 缓冲中

        assert builder.get_full_text() == "Hello World!"

    def test_disabled_progress_events(self) -> None:
        """禁用进度事件时不应发送。"""
        config = StreamingConfig(emit_progress=False)
        builder = StreamingAnswerBuilder("req_123", config)

        events = builder.on_retrieval_complete([{"documentId": "doc_1"}])

        # 只有引用事件，没有进度事件
        assert len(events) == 1
        assert events[0].type == StreamEventType.CITATION

    def test_disabled_citation_events(self) -> None:
        """禁用引用事件时不应发送。"""
        config = StreamingConfig(emit_citations=False)
        builder = StreamingAnswerBuilder("req_123", config)

        events = builder.on_retrieval_complete([{"documentId": "doc_1"}])

        # 只有进度事件，没有引用事件
        assert len(events) == 1
        assert events[0].type == StreamEventType.PROGRESS
