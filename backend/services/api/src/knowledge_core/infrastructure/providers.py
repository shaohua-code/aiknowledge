from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import httpx

from knowledge_core.config import settings
from knowledge_core.infrastructure.model_configuration import get_model_configuration
from knowledge_core.shared.errors import ProviderUnavailableError


class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(slots=True)
class ChatResult:
    content: str
    input_tokens: int
    output_tokens: int


class ChatProvider(Protocol):
    @property
    def available(self) -> bool: ...

    async def generate(self, messages: list[dict[str, str]]) -> ChatResult: ...


@dataclass(slots=True)
class WebSearchHit:
    document_id: None
    title: str
    content: str
    score: float
    citation: dict[str, Any]
    published_at: datetime | None = None


class WebSearchProvider(Protocol):
    @property
    def available(self) -> bool: ...

    async def search(self, query: str, *, limit: int = 5) -> list[WebSearchHit]: ...


class LocalHashEmbeddingProvider:
    """仅用于开发和测试的确定性向量。

    它让本地环境在没有外部密钥时验证完整入库与隔离链路；生产配置会拒绝启用。
    """

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        values: list[float] = []
        counter = 0
        while len(values) < settings.embedding_dimension:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            values.extend((byte - 127.5) / 127.5 for byte in digest)
            counter += 1
        vector = values[: settings.embedding_dimension]
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class OpenAICompatibleEmbeddingProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        configuration = get_model_configuration()
        if not configuration.embedding_api_key:
            raise ProviderUnavailableError(
                "Embedding Provider 尚未配置",
                suggestion="请在平台环境中配置 EMBEDDING_API_KEY",
            )
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{configuration.embedding_base_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {configuration.embedding_api_key}"},
                json={"model": configuration.embedding_model, "input": texts},
            )
        if response.status_code >= 400:
            raise ProviderUnavailableError(
                "Embedding Provider 调用失败",
                details={"statusCode": response.status_code},
            )
        data = response.json().get("data", [])
        vectors = [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]
        if len(vectors) != len(texts):
            raise ProviderUnavailableError("Embedding Provider 返回数量与输入不一致")
        return vectors


class DisabledChatProvider:
    @property
    def available(self) -> bool:
        return False

    async def generate(self, messages: list[dict[str, str]]) -> ChatResult:
        raise ProviderUnavailableError(
            "Chat Provider 尚未配置",
            suggestion="配置 CHAT_PROVIDER、CHAT_MODEL 和 CHAT_API_KEY 后重试",
        )


class OpenAICompatibleChatProvider:
    @property
    def available(self) -> bool:
        configuration = get_model_configuration()
        return bool(configuration.chat_api_key and configuration.chat_model)

    async def generate(self, messages: list[dict[str, str]]) -> ChatResult:
        configuration = get_model_configuration()
        if not self.available:
            raise ProviderUnavailableError("Chat Provider 尚未配置")
        async with httpx.AsyncClient(timeout=settings.answer_timeout_seconds) as client:
            response = await client.post(
                f"{configuration.chat_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {configuration.chat_api_key}"},
                json={
                    "model": configuration.chat_model,
                    "messages": messages,
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                },
            )
        if response.status_code >= 400:
            raise ProviderUnavailableError(
                "Chat Provider 调用失败",
                details={"statusCode": response.status_code},
            )
        payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderUnavailableError("Chat Provider 返回结构无效") from exc
        usage = payload.get("usage") or {}
        return ChatResult(
            content=content,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )


class DisabledWebSearchProvider:
    @property
    def available(self) -> bool:
        return False

    async def search(self, query: str, *, limit: int = 5) -> list[WebSearchHit]:
        raise ProviderUnavailableError("联网搜索 Provider 尚未配置")


class SerperWebSearchProvider:
    @property
    def available(self) -> bool:
        return bool(get_model_configuration().web_search_api_key)

    async def search(self, query: str, *, limit: int = 5) -> list[WebSearchHit]:
        configuration = get_model_configuration()
        if not self.available:
            raise ProviderUnavailableError("联网搜索 Provider 尚未配置")
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{configuration.web_search_base_url.rstrip('/')}/search",
                headers={
                    "X-API-KEY": configuration.web_search_api_key,
                    "Content-Type": "application/json",
                },
                json={"q": query, "num": limit},
            )
        if response.status_code >= 400:
            raise ProviderUnavailableError(
                "联网搜索调用失败",
                details={"statusCode": response.status_code},
            )
        rows = response.json().get("organic") or []
        return [
            WebSearchHit(
                document_id=None,
                title=str(row.get("title") or "联网资料"),
                content=str(row.get("snippet") or ""),
                score=max(0.4, 0.9 - index * 0.08),
                citation={"url": str(row.get("link") or ""), "position": index + 1},
            )
            for index, row in enumerate(rows[:limit])
            if row.get("link") and row.get("snippet")
        ]


def get_embedding_provider() -> EmbeddingProvider:
    configuration = get_model_configuration()
    if configuration.embedding_provider == "local_hash":
        return LocalHashEmbeddingProvider()
    if configuration.embedding_provider in {"openai", "openai_compatible"}:
        return OpenAICompatibleEmbeddingProvider()
    raise ProviderUnavailableError(
        f"不支持的 Embedding Provider：{configuration.embedding_provider}"
    )


def get_chat_provider() -> ChatProvider:
    configuration = get_model_configuration()
    if configuration.chat_provider == "disabled":
        return DisabledChatProvider()
    if configuration.chat_provider in {"openai", "openai_compatible"}:
        return OpenAICompatibleChatProvider()
    return DisabledChatProvider()


def get_web_search_provider() -> WebSearchProvider:
    if get_model_configuration().web_search_provider == "serper":
        return SerperWebSearchProvider()
    return DisabledWebSearchProvider()


def parse_json_object(content: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ProviderUnavailableError("模型没有返回合法 JSON") from exc
    if not isinstance(value, dict):
        raise ProviderUnavailableError("模型结构化输出必须是 JSON 对象")
    return value
