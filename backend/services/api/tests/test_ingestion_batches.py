"""验证入库任务的向量分批边界，避免长文档一次占满 Provider 请求。"""

from types import SimpleNamespace

import pytest

from knowledge_core.config import settings
from knowledge_core.infrastructure.providers import embedding_space_fingerprint
from knowledge_core.workers.tasks import _embed_chunks


class FakeEmbeddingProvider:
    """记录请求批次，并为每段文本返回可断言的模拟向量。"""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(texts)
        return [[float(len(text))] * settings.embedding_dimension for text in texts]


@pytest.mark.asyncio
async def test_embedding_is_split_into_stable_batches() -> None:
    """长文档应分批调用，同时保持片段与返回向量的顺序一一对应。"""
    provider = FakeEmbeddingProvider()
    chunks = [{"content": f"片段 {index}"} for index in range(5)]

    await _embed_chunks(chunks, provider, batch_size=2)

    assert provider.batches == [["片段 0", "片段 1"], ["片段 2", "片段 3"], ["片段 4"]]
    assert [chunk["embedding"][0] for chunk in chunks] == [4.0, 4.0, 4.0, 4.0, 4.0]
    assert all(len(chunk["embedding"]) == settings.embedding_dimension for chunk in chunks)


@pytest.mark.asyncio
async def test_embedding_rejects_mismatched_provider_results() -> None:
    """Provider 少返回结果时不得写入错位索引，任务需要以可诊断错误失败。"""

    class BrokenProvider:
        async def embed(self, _texts: list[str]) -> list[list[float]]:
            return []

    with pytest.raises(ValueError, match="返回数量异常"):
        await _embed_chunks([{"content": "待处理"}], BrokenProvider())


@pytest.mark.asyncio
async def test_embedding_reuses_only_persisted_complete_batches() -> None:
    """任务重试时应跳过已安全保存的批次，未命中部分仍由 Provider 计算。"""
    provider = FakeEmbeddingProvider()
    saved: dict[int, list[list[float]]] = {}

    async def load_checkpoint(
        batch_index: int, _chunk_start: int, _chunk_count: int, _content_digest: str
    ) -> list[list[float]] | None:
        """模拟仓储按批次、内容摘要和模型指纹命中检查点后的返回。"""
        return saved.get(batch_index)

    async def save_checkpoint(
        batch_index: int,
        _chunk_start: int,
        _chunk_count: int,
        _content_digest: str,
        embeddings: list[list[float]],
    ) -> None:
        """仅记录通过数量和维度校验的完整 Provider 结果。"""
        saved[batch_index] = embeddings

    first_chunks = [{"content": f"片段 {index}"} for index in range(5)]
    await _embed_chunks(
        first_chunks,
        provider,
        batch_size=2,
        load_checkpoint=load_checkpoint,
        save_checkpoint=save_checkpoint,
    )

    retry_provider = FakeEmbeddingProvider()
    retry_chunks = [{"content": f"片段 {index}"} for index in range(5)]
    await _embed_chunks(
        retry_chunks,
        retry_provider,
        batch_size=2,
        load_checkpoint=load_checkpoint,
        save_checkpoint=save_checkpoint,
    )

    assert provider.batches == [["片段 0", "片段 1"], ["片段 2", "片段 3"], ["片段 4"]]
    assert retry_provider.batches == []
    assert [chunk["embedding"][0] for chunk in retry_chunks] == [4.0, 4.0, 4.0, 4.0, 4.0]


def test_embedding_space_fingerprint_changes_with_model_but_not_secret() -> None:
    """不同模型必须属于不同索引空间，凭证轮换不应迫使知识重新建索引。"""
    first = SimpleNamespace(
        configuration=SimpleNamespace(
            provider="openai_compatible",
            model="embedding-a",
            base_url="https://provider.example/v1",
            api_key="secret-a",
        )
    )
    rotated_secret = SimpleNamespace(
        configuration=SimpleNamespace(
            provider="openai_compatible",
            model="embedding-a",
            base_url="https://provider.example/v1",
            api_key="secret-b",
        )
    )
    changed_model = SimpleNamespace(
        configuration=SimpleNamespace(
            provider="openai_compatible",
            model="embedding-b",
            base_url="https://provider.example/v1",
            api_key="secret-a",
        )
    )

    assert embedding_space_fingerprint(first) == embedding_space_fingerprint(rotated_secret)
    assert embedding_space_fingerprint(first) != embedding_space_fingerprint(changed_model)
