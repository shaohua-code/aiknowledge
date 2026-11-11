"""RAG 增强集成测试。

验证 RAG 增强功能与主链路（retrieval、ingestion）的集成。
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from knowledge_core.domains.intelligence.repository import IntelligenceRepository
from knowledge_core.domains.intelligence.retrieval import RetrievalService
from knowledge_core.domains.knowledge.ingestion import (
    ParsedBlock,
    chunk_blocks,
    normalize_text,
    resolve_chunking_options,
)


class TestChunkingIntegration:
    """分块策略集成测试。"""

    def test_default_chunking_backward_compatible(self) -> None:
        """默认分块应保持向后兼容。"""
        blocks = [
            ParsedBlock(text="这是第一段内容。" * 50),
            ParsedBlock(text="这是第二段内容。" * 50),
        ]

        # 不指定 chunking_strategy，使用默认行为
        chunks = chunk_blocks(blocks, target_chars=200)

        assert len(chunks) > 0
        for chunk in chunks:
            assert "chunk_index" in chunk
            assert "content" in chunk
            assert "token_count" in chunk

    def test_paragraph_chunking_strategy(self) -> None:
        """段落分块策略应在超过目标长度时拆开段落。"""
        paragraph = "这是用于验证段落分块策略的一段足够长的中文内容，需要单独成段。"
        text = f"{paragraph}甲。\n\n{paragraph}乙。\n\n{paragraph}丙。"
        blocks = [ParsedBlock(text=text)]

        chunks = chunk_blocks(
            blocks,
            target_chars=80,
            chunking_strategy="paragraph",
        )

        assert len(chunks) >= 2
        full_content = " ".join(c["content"] for c in chunks)
        assert "甲" in full_content
        assert "乙" in full_content
        assert "丙" in full_content

    def test_sentence_chunking_strategy(self) -> None:
        """句子分块策略应在超过目标长度时拆开句子。"""
        sentence = "这是用于验证句子分块策略的一个完整句子，长度超过最小限制"
        text = f"{sentence}第一句。{sentence}第二句！{sentence}第三句？"
        blocks = [ParsedBlock(text=text)]

        chunks = chunk_blocks(
            blocks,
            target_chars=80,
            chunking_strategy="sentence",
        )

        assert len(chunks) >= 2
        full_content = " ".join(c["content"] for c in chunks)
        assert "第一句" in full_content

    def test_short_text_falls_back_instead_of_failing(self) -> None:
        """短于策略最小长度的文本应回退保留，而不是入库失败。"""
        blocks = [ParsedBlock(text="短段落。\n\n另一段。")]

        chunks = chunk_blocks(blocks, target_chars=1200, chunking_strategy="paragraph")

        assert len(chunks) == 1
        assert "短段落" in chunks[0]["content"]

    def test_invalid_strategy_falls_back_to_fixed_size(self) -> None:
        """无效的分块策略应回退到固定大小。"""
        blocks = [ParsedBlock(text="测试内容。" * 50)]

        # 使用无效的策略名
        chunks = chunk_blocks(
            blocks,
            target_chars=100,
            chunking_strategy="invalid_strategy",
        )

        assert len(chunks) > 0

    def test_chunking_with_page_numbers(self) -> None:
        """分块应保留页码信息。"""
        blocks = [
            ParsedBlock(text="第一页内容。" * 20, page_number=1),
            ParsedBlock(text="第二页内容。" * 20, page_number=2),
        ]

        chunks = chunk_blocks(blocks, target_chars=100)

        page_1_chunks = [c for c in chunks if c["page_number"] == 1]
        page_2_chunks = [c for c in chunks if c["page_number"] == 2]

        assert len(page_1_chunks) > 0
        assert len(page_2_chunks) > 0

    def test_chunking_with_sections(self) -> None:
        """分块应保留章节信息。"""
        blocks = [
            ParsedBlock(text="章节一内容。" * 20, section="第一章"),
            ParsedBlock(text="章节二内容。" * 20, section="第二章"),
        ]

        chunks = chunk_blocks(blocks, target_chars=100)

        sections = set(c["section"] for c in chunks if c["section"])
        assert "第一章" in sections
        assert "第二章" in sections


class TestNormalizeText:
    """文本规范化测试。"""

    def test_normalize_line_endings(self) -> None:
        """应统一行尾符为 LF。"""
        text = "line1\r\nline2\rline3"
        result = normalize_text(text)

        assert "\r\n" not in result
        assert "\r" not in result
        assert result == "line1\nline2\nline3"

    def test_remove_control_characters(self) -> None:
        """应移除控制字符。"""
        text = "hello\x00world\x1f"
        result = normalize_text(text)

        assert "\x00" not in result
        assert "\x1f" not in result
        assert result == "helloworld"

    def test_collapse_multiple_newlines(self) -> None:
        """应折叠多个连续空行。"""
        text = "para1\n\n\n\npara2"
        result = normalize_text(text)

        assert "\n\n\n" not in result
        assert result == "para1\n\npara2"


class TestChunkingEdgeCases:
    """分块边缘情况测试。"""

    def test_empty_blocks_raises_error(self) -> None:
        """空文档块应抛出错误。"""
        blocks = [ParsedBlock(text="")]

        with pytest.raises(ValueError, match="没有有效内容"):
            chunk_blocks(blocks)

    def test_whitespace_only_blocks_raises_error(self) -> None:
        """仅包含空白的文档块应抛出错误。"""
        blocks = [ParsedBlock(text="   \n\n   ")]

        with pytest.raises(ValueError, match="没有有效内容"):
            chunk_blocks(blocks)

    def test_very_long_paragraph_is_split(self) -> None:
        """超长段落应被强制切分。"""
        long_text = "长" * 5000  # 5000 个字符
        blocks = [ParsedBlock(text=long_text)]

        chunks = chunk_blocks(blocks, target_chars=100)

        # 应产生多个 chunks
        assert len(chunks) > 1
        # 每个 chunk 不应超过目标大小太多
        for chunk in chunks:
            assert len(chunk["content"]) <= 200  # 允许一定容差

    def test_mixed_content_blocks(self) -> None:
        """混合内容块应正确处理。"""
        blocks = [
            ParsedBlock(text=""),  # 空块
            ParsedBlock(text="有效内容。" * 20),
            ParsedBlock(text="   "),  # 空白块
            ParsedBlock(text="更多内容。" * 20),
        ]

        chunks = chunk_blocks(blocks, target_chars=100)

        # 应忽略空块和空白块
        assert len(chunks) > 0
        for chunk in chunks:
            assert chunk["content"].strip()


class TestChunkingOptions:
    """版本 metadata 中的分块开关。"""

    def test_missing_metadata_keeps_fixed_size(self) -> None:
        """没有配置时不启用新策略。"""
        strategy, target, overlap = resolve_chunking_options(None)
        assert strategy is None
        assert target == 1200
        assert overlap == 180

    def test_nested_strategy_is_read(self) -> None:
        """rag.chunking 中的段落策略和长度应被读取。"""
        strategy, target, overlap = resolve_chunking_options(
            {"rag": {"chunking": {"strategy": "paragraph", "targetChars": 400, "overlapChars": 20}}}
        )
        assert strategy == "paragraph"
        assert target == 400
        assert overlap == 20


class TestRetrievalRagSwitch:
    """检索主链路只在发布快照打开开关时启用增强。"""

    def test_published_snapshot_carries_rag_config(self) -> None:
        """修订 JSON 中的 ragEnhancement 应进入运行面快照。"""
        profile = SimpleNamespace(id=uuid4())
        revision = SimpleNamespace(
            id=uuid4(),
            configuration={
                "code": "resume_search",
                "collection_ids": [str(uuid4())],
                "top_k": 8,
                "minimum_score": 0.2,
                "vector_weight": 0.65,
                "lexical_weight": 0.35,
                "metadata_filters": {},
                "ragEnhancement": {"queryRewriting": {"enabled": True}},
            },
        )

        published = IntelligenceRepository._published_retrieval(profile, revision)

        assert published.rag_config is not None
        assert published.rag_config["queryRewriting"]["enabled"] is True

    def test_old_snapshot_without_rag_stays_disabled(self) -> None:
        """旧快照没有 RAG 键时运行面配置为 None。"""
        profile = SimpleNamespace(id=uuid4())
        revision = SimpleNamespace(
            id=uuid4(),
            configuration={
                "code": "resume_search",
                "collection_ids": [],
                "top_k": 8,
                "minimum_score": 0.2,
                "vector_weight": 0.65,
                "lexical_weight": 0.35,
                "metadata_filters": {},
            },
        )

        published = IntelligenceRepository._published_retrieval(profile, revision)

        assert published.rag_config is None

    def test_disabled_switches_do_not_activate_retrieval(self) -> None:
        """两个开关都关闭时检索不进入改写或重排序。"""
        service = RetrievalService.__new__(RetrievalService)
        config = service._get_rag_config(
            SimpleNamespace(
                rag_config={
                    "queryRewriting": {"enabled": False},
                    "reranking": {"enabled": False},
                }
            )
        )
        enabled = service._get_rag_config(
            SimpleNamespace(rag_config={"reranking": {"enabled": True}})
        )

        assert config is None
        assert enabled is not None
        assert enabled.reranking_enabled is True
