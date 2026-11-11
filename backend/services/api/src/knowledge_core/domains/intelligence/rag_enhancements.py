"""RAG 增强模块。

提供可配置的 RAG 能力增强功能：
- Query Rewriting（查询改写）：将用户原始问题改写为更适合检索的查询
- Reranking（重排序）：使用更精确的模型对检索结果进行二次排序
- Chunking 策略配置：支持不同的文档分块策略

所有功能设计为向后兼容，可通过配置启用或禁用。
默认关闭增强功能以保持现有行为。

使用示例：
    # 在检索策略配置中启用
    rag_config = RAGEnhancementConfig(
        query_rewriting_enabled=True,
        reranking_enabled=True,
        chunking_strategy="semantic"
    )
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class ChunkingStrategy(str, Enum):
    """分块策略枚举。

    FIXED_SIZE: 固定字符数分块（默认，向后兼容）
    PARAGRAPH: 按段落分块，保持语义完整性
    SENTENCE: 按句子分块，适合问答场景
    SEMANTIC: 语义分块（需要 LLM 支持，实验性）
    """
    FIXED_SIZE = "fixed_size"
    PARAGRAPH = "paragraph"
    SENTENCE = "sentence"
    SEMANTIC = "semantic"


@dataclass(slots=True)
class RAGEnhancementConfig:
    """RAG 增强功能配置。

    所有增强功能默认关闭以保持向后兼容性。
    启用前请确保相关 Provider 已配置。
    """

    # Query Rewriting 配置
    query_rewriting_enabled: bool = False
    query_rewriting_prompt: str = field(default_factory=lambda: (
        "请将以下用户问题改写为更适合在知识库中检索的查询。"
        "保持原意但使用更精确的关键词。"
        "只输出改写后的查询，不要解释。\n\n原始问题：{query}"
    ))
    query_rewriting_max_tokens: int = 200

    # Reranking 配置
    reranking_enabled: bool = False
    reranking_model: str = "default"
    reranking_top_k: int = 10
    reranking_batch_size: int = 32

    # Chunking 配置
    chunking_strategy: ChunkingStrategy = ChunkingStrategy.FIXED_SIZE
    chunk_target_chars: int = 1200
    chunk_overlap_chars: int = 180
    chunk_min_chars: int = 50
    chunk_max_chars: int = 3000

    def to_dict(self) -> dict[str, Any]:
        """导出为可序列化的字典。"""
        return {
            "queryRewriting": {
                "enabled": self.query_rewriting_enabled,
                "prompt": self.query_rewriting_prompt,
                "maxTokens": self.query_rewriting_max_tokens,
            },
            "reranking": {
                "enabled": self.reranking_enabled,
                "model": self.reranking_model,
                "topK": self.reranking_top_k,
                "batchSize": self.reranking_batch_size,
            },
            "chunking": {
                "strategy": self.chunking_strategy.value,
                "targetChars": self.chunk_target_chars,
                "overlapChars": self.chunk_overlap_chars,
                "minChars": self.chunk_min_chars,
                "maxChars": self.chunk_max_chars,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RAGEnhancementConfig:
        """从字典构建配置，缺失字段使用默认值。"""
        query_rewriting = data.get("queryRewriting", {})
        reranking = data.get("reranking", {})
        chunking = data.get("chunking", {})

        strategy_value = chunking.get("strategy", "fixed_size")
        try:
            strategy = ChunkingStrategy(strategy_value)
        except ValueError:
            strategy = ChunkingStrategy.FIXED_SIZE

        return cls(
            query_rewriting_enabled=query_rewriting.get("enabled", False),
            query_rewriting_prompt=query_rewriting.get("prompt", cls.query_rewriting_prompt),
            query_rewriting_max_tokens=query_rewriting.get("maxTokens", 200),
            reranking_enabled=reranking.get("enabled", False),
            reranking_model=reranking.get("model", "default"),
            reranking_top_k=reranking.get("topK", 10),
            reranking_batch_size=reranking.get("batchSize", 32),
            chunking_strategy=strategy,
            chunk_target_chars=chunking.get("targetChars", 1200),
            chunk_overlap_chars=chunking.get("overlapChars", 180),
            chunk_min_chars=chunking.get("minChars", 50),
            chunk_max_chars=chunking.get("maxChars", 3000),
        )


class LLMProvider(Protocol):
    """LLM Provider 接口协议，用于 Query Rewriting。"""

    async def complete(self, prompt: str, max_tokens: int) -> str:
        """执行单次补全请求。"""
        ...


class RerankerProvider(Protocol):
    """Reranker Provider 接口协议。"""

    async def rerank(
        self, query: str, documents: list[str], top_k: int
    ) -> list[tuple[int, float]]:
        """重排序文档，返回 (原索引, 分数) 列表，按分数降序。"""
        ...


async def rewrite_query(
    query: str,
    config: RAGEnhancementConfig,
    llm_provider: LLMProvider | None = None,
) -> str:
    """将用户查询改写为更适合检索的形式。

    Args:
        query: 用户原始查询
        config: RAG 增强配置
        llm_provider: LLM Provider 实例，None 时返回原查询

    Returns:
        改写后的查询，或在 Provider 不可用/禁用时返回原查询

    Note:
        改写失败时静默返回原查询，不抛出异常
    """
    if not config.query_rewriting_enabled:
        return query

    if llm_provider is None:
        return query

    try:
        prompt = config.query_rewriting_prompt.format(query=query)
        rewritten = await llm_provider.complete(prompt, config.query_rewriting_max_tokens)
        # 清理改写结果
        rewritten = rewritten.strip().strip('"\'')
        # 验证改写结果合理性
        if not rewritten or len(rewritten) < 3 or len(rewritten) > len(query) * 3:
            return query
        return rewritten
    except Exception:
        # 改写失败时静默回退，不影响主流程
        return query


@dataclass(slots=True)
class RerankCandidate:
    """重排序候选项。"""
    index: int
    content: str
    original_score: float


async def rerank_results(
    query: str,
    candidates: list[RerankCandidate],
    config: RAGEnhancementConfig,
    reranker: RerankerProvider | None = None,
) -> list[tuple[int, float]]:
    """对检索结果进行重排序。

    Args:
        query: 用户查询
        candidates: 候选结果列表
        config: RAG 增强配置
        reranker: Reranker Provider 实例，None 时按原始分数返回

    Returns:
        (原索引, 新分数) 列表，按新分数降序排列

    Note:
        重排序失败时按原始分数排序，不抛出异常
    """
    if not config.reranking_enabled or not candidates:
        return [
            (c.index, c.original_score)
            for c in sorted(candidates, key=lambda x: x.original_score, reverse=True)
        ]

    if reranker is None:
        return [
            (c.index, c.original_score)
            for c in sorted(candidates, key=lambda x: x.original_score, reverse=True)
        ]

    try:
        documents = [c.content for c in candidates]
        top_k = min(config.reranking_top_k, len(candidates))
        ranked = await reranker.rerank(query, documents, top_k)
        return ranked
    except Exception:
        # 重排序失败时按原始分数回退
        return [
            (c.index, c.original_score)
            for c in sorted(candidates, key=lambda x: x.original_score, reverse=True)
        ]


# === 可配置的 Chunking 策略实现 ===


def chunk_by_fixed_size(
    text: str,
    *,
    target_chars: int = 1200,
    overlap_chars: int = 180,
    min_chars: int = 50,
) -> list[str]:
    """固定字符数分块（默认策略，向后兼容）。

    按目标字符数切分文本，相邻块之间有重叠以保持上下文连续性。
    """
    if len(text) <= target_chars:
        return [text] if len(text) >= min_chars else []

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + target_chars
        chunk = text[start:end].strip()
        if len(chunk) >= min_chars:
            chunks.append(chunk)
        start = max(end - overlap_chars, start + 1)
        if start >= len(text):
            break

    return chunks


def chunk_by_paragraph(
    text: str,
    *,
    target_chars: int = 1200,
    min_chars: int = 50,
    max_chars: int = 3000,
) -> list[str]:
    """按段落分块。

    尽量保持段落完整性，只在段落过长时才切分。
    短段落会被合并以达到目标大小。
    """
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]

    if not paragraphs:
        return []

    chunks: list[str] = []
    buffer: list[str] = []
    buffer_len = 0

    for paragraph in paragraphs:
        para_len = len(paragraph)

        # 段落本身超过最大限制，强制切分
        if para_len > max_chars:
            if buffer:
                chunks.append("\n\n".join(buffer))
                buffer = []
                buffer_len = 0
            # 按句子或固定大小切分长段落
            sub_chunks = chunk_by_fixed_size(
                paragraph,
                target_chars=target_chars,
                overlap_chars=0,
                min_chars=min_chars,
            )
            chunks.extend(sub_chunks)
            continue

        # 加入当前段落是否会超过目标大小
        new_len = buffer_len + para_len + (2 if buffer else 0)
        if new_len > target_chars and buffer:
            # 输出当前缓冲
            chunks.append("\n\n".join(buffer))
            buffer = [paragraph]
            buffer_len = para_len
        else:
            buffer.append(paragraph)
            buffer_len = new_len

    # 处理剩余缓冲
    if buffer:
        final = "\n\n".join(buffer)
        if len(final) >= min_chars:
            chunks.append(final)

    return chunks


# 中文句子结束标点
_SENTENCE_END_PATTERN = re.compile(r"([。！？!?；;]+['\"\u300D\u300F\uFF09\)）]?)")
# 英文句子结束标点
_EN_SENTENCE_END = re.compile(r"([.!?]+[\s]+)")


def chunk_by_sentence(
    text: str,
    *,
    target_chars: int = 1200,
    min_chars: int = 50,
    max_chars: int = 3000,
) -> list[str]:
    """按句子分块。

    适合问答场景，保持句子完整性。
    将句子聚合到目标大小左右。
    """
    # 尝试按中文标点分句
    sentences = _SENTENCE_END_PATTERN.split(text)
    if len(sentences) <= 2:
        # 可能是英文文本，尝试英文分句
        sentences = _EN_SENTENCE_END.split(text)

    # 重组句子（标点符号和正文分开了）
    merged_sentences: list[str] = []
    buffer = ""
    for part in sentences:
        if not part.strip():
            continue
        if _SENTENCE_END_PATTERN.match(part) or _EN_SENTENCE_END.match(part):
            buffer += part
            if buffer.strip():
                merged_sentences.append(buffer.strip())
            buffer = ""
        else:
            buffer = part

    if buffer.strip():
        merged_sentences.append(buffer.strip())

    if not merged_sentences:
        return chunk_by_fixed_size(text, target_chars=target_chars, min_chars=min_chars)

    # 聚合句子到目标大小
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_len = 0

    for sentence in merged_sentences:
        sent_len = len(sentence)

        # 单个句子超过最大限制
        if sent_len > max_chars:
            if current_chunk:
                chunks.append(" ".join(current_chunk))
                current_chunk = []
                current_len = 0
            # 强制按固定大小切分
            sub_chunks = chunk_by_fixed_size(
                sentence,
                target_chars=target_chars,
                min_chars=min_chars,
            )
            chunks.extend(sub_chunks)
            continue

        # 加入当前句子是否会超过目标
        new_len = current_len + sent_len + (1 if current_chunk else 0)
        if new_len > target_chars and current_chunk:
            chunks.append(" ".join(current_chunk))
            current_chunk = [sentence]
            current_len = sent_len
        else:
            current_chunk.append(sentence)
            current_len = new_len

    # 处理剩余
    if current_chunk:
        final = " ".join(current_chunk)
        if len(final) >= min_chars:
            chunks.append(final)

    return chunks


def chunk_text(
    text: str,
    config: RAGEnhancementConfig | None = None,
) -> list[str]:
    """根据配置选择合适的分块策略。

    Args:
        text: 待分块的文本
        config: RAG 增强配置，None 时使用默认配置

    Returns:
        分块后的文本列表
    """
    if config is None:
        config = RAGEnhancementConfig()

    text = text.strip()
    if not text:
        return []

    strategy = config.chunking_strategy
    target = config.chunk_target_chars
    overlap = config.chunk_overlap_chars
    min_chars = config.chunk_min_chars
    max_chars = config.chunk_max_chars

    if strategy == ChunkingStrategy.FIXED_SIZE:
        return chunk_by_fixed_size(
            text,
            target_chars=target,
            overlap_chars=overlap,
            min_chars=min_chars,
        )
    elif strategy == ChunkingStrategy.PARAGRAPH:
        return chunk_by_paragraph(
            text,
            target_chars=target,
            min_chars=min_chars,
            max_chars=max_chars,
        )
    elif strategy == ChunkingStrategy.SENTENCE:
        return chunk_by_sentence(
            text,
            target_chars=target,
            min_chars=min_chars,
            max_chars=max_chars,
        )
    elif strategy == ChunkingStrategy.SEMANTIC:
        # 语义分块需要 LLM 支持，目前回退到段落分块
        # TODO: 实现语义分块
        return chunk_by_paragraph(
            text,
            target_chars=target,
            min_chars=min_chars,
            max_chars=max_chars,
        )
    else:
        return chunk_by_fixed_size(
            text,
            target_chars=target,
            overlap_chars=overlap,
            min_chars=min_chars,
        )


# === 质量评估指标 ===


@dataclass(slots=True)
class RAGMetrics:
    """RAG 检索质量指标。

    用于评估检索增强生成的效果，支持在固定样本上做质量/延迟对比。
    """

    # 检索指标
    total_retrieved: int = 0
    relevant_retrieved: int = 0
    retrieval_latency_ms: float = 0.0

    # Query Rewriting 指标
    query_rewriting_enabled: bool = False
    query_rewriting_latency_ms: float = 0.0

    # Reranking 指标
    reranking_enabled: bool = False
    reranking_latency_ms: float = 0.0
    reranking_score_improvement: float = 0.0

    # 总体指标
    total_latency_ms: float = 0.0

    @property
    def precision(self) -> float | None:
        """检索精确率：相关结果数 / 总检索数。"""
        if self.total_retrieved == 0:
            return None
        return self.relevant_retrieved / self.total_retrieved

    def to_dict(self) -> dict[str, Any]:
        """导出为可序列化的字典。"""
        return {
            "retrieval": {
                "totalRetrieved": self.total_retrieved,
                "relevantRetrieved": self.relevant_retrieved,
                "precision": self.precision,
                "latencyMs": round(self.retrieval_latency_ms, 2),
            },
            "queryRewriting": {
                "enabled": self.query_rewriting_enabled,
                "latencyMs": round(self.query_rewriting_latency_ms, 2),
            },
            "reranking": {
                "enabled": self.reranking_enabled,
                "latencyMs": round(self.reranking_latency_ms, 2),
                "scoreImprovement": round(self.reranking_score_improvement, 4),
            },
            "totalLatencyMs": round(self.total_latency_ms, 2),
        }
