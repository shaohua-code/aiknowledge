from __future__ import annotations

import json
from functools import partial

import httpx
import pytest

from knowledge_core.config import settings
from knowledge_core.infrastructure import providers
from knowledge_core.infrastructure.model_resolver import ResolvedModelConfig
from knowledge_core.shared.errors import EmbeddingDimensionError


def _configuration() -> ResolvedModelConfig:
    return ResolvedModelConfig(
        provider="openai_compatible",
        model="embedding-test",
        base_url="https://provider.example/v1",
        api_key="test-key",
    )


def _patch_provider(monkeypatch: pytest.MonkeyPatch, dimension: int) -> list[dict]:
    # 用 MockTransport 模拟 OpenAI 兼容 Embedding 接口，记录请求体并返回指定维度向量
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "embedding": [0.1] * dimension},
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        providers.httpx, "AsyncClient", partial(httpx.AsyncClient, transport=transport)
    )
    return requests


@pytest.mark.asyncio
async def test_embed_requests_database_dimension(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_provider(monkeypatch, settings.embedding_dimension)
    vectors = await providers.OpenAICompatibleEmbeddingProvider(_configuration()).embed(
        ["测试文本"]
    )
    assert len(vectors[0]) == settings.embedding_dimension
    assert requests[0]["dimensions"] == settings.embedding_dimension


@pytest.mark.asyncio
async def test_embed_rejects_mismatched_dimension(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provider(monkeypatch, settings.embedding_dimension - 512)
    with pytest.raises(EmbeddingDimensionError) as captured:
        await providers.OpenAICompatibleEmbeddingProvider(_configuration()).embed(["测试文本"])
    assert captured.value.code == "EMBEDDING_DIMENSION_MISMATCH"
    assert "维" in captured.value.message
