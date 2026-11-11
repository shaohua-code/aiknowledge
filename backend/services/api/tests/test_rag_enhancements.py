"""RAG 增强功能测试。

验证查询改写、重排序和分块策略的正确性和向后兼容性。
"""

from __future__ import annotations

import pytest

from knowledge_core.domains.intelligence.rag_enhancements import (
    ChunkingStrategy,
    RAGEnhancementConfig,
    RAGMetrics,
    RerankCandidate,
    chunk_by_fixed_size,
    chunk_by_paragraph,
    chunk_by_sentence,
    chunk_text,
    rerank_results,
    rewrite_query,
)


class TestRAGEnhancementConfig:
    """RAG 增强配置测试。"""

    def test_default_config_disables_all_enhancements(self) -> None:
        """默认配置应禁用所有增强功能以保持向后兼容。"""
        config = RAGEnhancementConfig()
        assert config.query_rewriting_enabled is False
        assert config.reranking_enabled is False
        assert config.chunking_strategy == ChunkingStrategy.FIXED_SIZE

    def test_config_to_dict_and_from_dict_roundtrip(self) -> None:
        """配置应支持序列化和反序列化往返。"""
        original = RAGEnhancementConfig(
            query_rewriting_enabled=True,
            reranking_enabled=True,
            chunking_strategy=ChunkingStrategy.PARAGRAPH,
            chunk_target_chars=1500,
        )
        data = original.to_dict()
        restored = RAGEnhancementConfig.from_dict(data)

        assert restored.query_rewriting_enabled == original.query_rewriting_enabled
        assert restored.reranking_enabled == original.reranking_enabled
        assert restored.chunking_strategy == original.chunking_strategy
        assert restored.chunk_target_chars == original.chunk_target_chars

    def test_config_from_dict_handles_missing_fields(self) -> None:
        """配置反序列化应正确处理缺失字段。"""
        config = RAGEnhancementConfig.from_dict({})
        assert config.query_rewriting_enabled is False
        assert config.chunking_strategy == ChunkingStrategy.FIXED_SIZE

    def test_config_from_dict_handles_invalid_strategy(self) -> None:
        """配置反序列化应正确处理无效的分块策略。"""
        config = RAGEnhancementConfig.from_dict({
            "chunking": {"strategy": "invalid_strategy"}
        })
        assert config.chunking_strategy == ChunkingStrategy.FIXED_SIZE


class TestQueryRewriting:
    """查询改写测试。"""

    @pytest.mark.asyncio
    async def test_disabled_rewriting_returns_original_query(self) -> None:
        """禁用改写时应返回原始查询。"""
        config = RAGEnhancementConfig(query_rewriting_enabled=False)
        result = await rewrite_query("测试查询", config)
        assert result == "测试查询"

    @pytest.mark.asyncio
    async def test_no_provider_returns_original_query(self) -> None:
        """无 Provider 时应返回原始查询。"""
        config = RAGEnhancementConfig(query_rewriting_enabled=True)
        result = await rewrite_query("测试查询", config, llm_provider=None)
        assert result == "测试查询"


class TestReranking:
    """重排序测试。"""

    @pytest.mark.asyncio
    async def test_disabled_reranking_preserves_order(self) -> None:
        """禁用重排序时应按原始分数排序。"""
        config = RAGEnhancementConfig(reranking_enabled=False)
        candidates = [
            RerankCandidate(index=0, content="文档A", original_score=0.8),
            RerankCandidate(index=1, content="文档B", original_score=0.9),
            RerankCandidate(index=2, content="文档C", original_score=0.7),
        ]
        result = await rerank_results("查询", candidates, config)

        # 应该按原始分数降序排列
        assert result[0][0] == 1  # 文档B，分数0.9
        assert result[1][0] == 0  # 文档A，分数0.8
        assert result[2][0] == 2  # 文档C，分数0.7

    @pytest.mark.asyncio
    async def test_no_provider_preserves_order(self) -> None:
        """无 Reranker Provider 时应按原始分数排序。"""
        config = RAGEnhancementConfig(reranking_enabled=True)
        candidates = [
            RerankCandidate(index=0, content="文档A", original_score=0.8),
        ]
        result = await rerank_results("查询", candidates, config, reranker=None)

        assert len(result) == 1
        assert result[0][0] == 0

    @pytest.mark.asyncio
    async def test_empty_candidates_returns_empty(self) -> None:
        """空候选列表应返回空结果。"""
        config = RAGEnhancementConfig(reranking_enabled=True)
        result = await rerank_results("查询", [], config)
        assert result == []


class TestChunkingStrategies:
    """分块策略测试。"""

    def test_fixed_size_chunking_with_short_text(self) -> None:
        """短文本应产生单个 chunk。"""
        text = "这是一段短文本。"
        chunks = chunk_by_fixed_size(text, target_chars=100, min_chars=5)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_fixed_size_chunking_with_long_text(self) -> None:
        """长文本应被切分为多个重叠的 chunks。"""
        text = "A" * 500
        chunks = chunk_by_fixed_size(
            text,
            target_chars=100,
            overlap_chars=20,
            min_chars=10,
        )
        assert len(chunks) > 1
        # 验证重叠
        for i in range(len(chunks) - 1):
            assert chunks[i][-20:] in chunks[i + 1] or len(chunks[i]) < 100

    def test_fixed_size_filters_short_chunks(self) -> None:
        """过短的 chunk 应被过滤。"""
        text = "短"
        chunks = chunk_by_fixed_size(text, target_chars=100, min_chars=50)
        assert chunks == []

    def test_paragraph_chunking_preserves_paragraphs(self) -> None:
        """段落分块应保持段落完整性。"""
        text = "第一段内容。\n\n第二段内容。\n\n第三段内容。"
        chunks = chunk_by_paragraph(
            text,
            target_chars=1000,
            min_chars=5,
            max_chars=2000,
        )
        # 三个短段落应合并为一个 chunk
        assert len(chunks) == 1
        assert "第一段" in chunks[0]
        assert "第二段" in chunks[0]
        assert "第三段" in chunks[0]

    def test_paragraph_chunking_splits_long_paragraphs(self) -> None:
        """过长的段落应被强制切分。"""
        long_para = "长" * 5000
        text = f"短段。\n\n{long_para}\n\n短段。"
        chunks = chunk_by_paragraph(
            text,
            target_chars=100,
            min_chars=5,
            max_chars=500,
        )
        # 长段落应被切分为多个 chunks
        assert len(chunks) > 2

    def test_sentence_chunking_preserves_sentences(self) -> None:
        """句子分块应保持句子完整性。"""
        text = "这是第一句。这是第二句！这是第三句？"
        chunks = chunk_by_sentence(
            text,
            target_chars=1000,
            min_chars=5,
        )
        # 三个短句应合并为一个 chunk
        assert len(chunks) == 1
        assert "第一句" in chunks[0]
        assert "第二句" in chunks[0]
        assert "第三句" in chunks[0]

    def test_sentence_chunking_splits_at_sentence_boundary(self) -> None:
        """句子分块应在句子边界切分。"""
        sentences = ["这是一个很长的句子。"] * 20
        text = "".join(sentences)
        chunks = chunk_by_sentence(
            text,
            target_chars=100,
            min_chars=5,
        )
        # 应该有多个 chunks
        assert len(chunks) > 1
        # 每个 chunk 应该以句号结尾（除非是最后一个）
        for chunk in chunks[:-1]:
            assert chunk.rstrip().endswith(("。", "！", "？", ".", "!", "?"))

    def test_chunk_text_uses_config_strategy(self) -> None:
        """chunk_text 应根据配置选择策略。"""
        # 使用足够长的文本以满足 min_chars 要求
        text = (
            "这是一段测试文本，需要足够长才能通过最小字符数限制。"
            "\n\n这是另一段落，同样需要足够长。"
        )

        # 固定大小策略，设置较小的 min_chars
        config_fixed = RAGEnhancementConfig(
            chunking_strategy=ChunkingStrategy.FIXED_SIZE,
            chunk_min_chars=10,
        )
        chunks_fixed = chunk_text(text, config_fixed)

        # 段落策略，设置较小的 min_chars
        config_para = RAGEnhancementConfig(
            chunking_strategy=ChunkingStrategy.PARAGRAPH,
            chunk_min_chars=10,
        )
        chunks_para = chunk_text(text, config_para)

        # 两种策略应该都能产生结果
        assert len(chunks_fixed) > 0
        assert len(chunks_para) > 0

    def test_chunk_text_default_config(self) -> None:
        """chunk_text 无配置时应使用默认配置。"""
        # 使用足够长的文本以满足默认 min_chars=50 要求（需要超过 50 个字符）
        text = (
            "这是一段足够长的测试文本内容，用于验证默认配置下的分块功能是否正常工作。"
            "我们需要确保文本长度超过最小字符数限制，这样才能正确测试分块逻辑。"
        )
        chunks = chunk_text(text)
        assert len(chunks) > 0


class TestRAGMetrics:
    """RAG 指标测试。"""

    def test_precision_with_no_results(self) -> None:
        """无检索结果时精确率应为 None。"""
        metrics = RAGMetrics(total_retrieved=0, relevant_retrieved=0)
        assert metrics.precision is None

    def test_precision_calculation(self) -> None:
        """精确率应正确计算。"""
        metrics = RAGMetrics(total_retrieved=10, relevant_retrieved=7)
        assert metrics.precision == 0.7

    def test_metrics_to_dict(self) -> None:
        """指标应正确序列化。"""
        metrics = RAGMetrics(
            total_retrieved=10,
            relevant_retrieved=7,
            retrieval_latency_ms=50.5,
            reranking_enabled=True,
            reranking_latency_ms=20.3,
            total_latency_ms=100.8,
        )
        data = metrics.to_dict()

        assert data["retrieval"]["totalRetrieved"] == 10
        assert data["retrieval"]["relevantRetrieved"] == 7
        assert data["retrieval"]["precision"] == 0.7
        assert data["retrieval"]["latencyMs"] == 50.5
        assert data["reranking"]["enabled"] is True
        assert data["reranking"]["latencyMs"] == 20.3
        assert data["totalLatencyMs"] == 100.8
