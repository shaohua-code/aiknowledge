from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.config import settings
from knowledge_core.infrastructure.model_resolver import (
    ResolvedModelConfig,
    resolve_capability_config,
    resolve_capability_config_async,
)
from knowledge_core.shared.errors import EmbeddingDimensionError, ProviderUnavailableError


def _build_timeout(total: float) -> httpx.Timeout:
    """构造分级超时：连接类故障 5 秒内快速失败，读取保留完整时长容纳大模型生成耗时。

    扁平超时会让不可达端点阻塞满总时长（如 30s）才报错，
    拆分 connect/pool 后，地址不可达在数秒内即抛出 ConnectTimeout。
    """
    return httpx.Timeout(total, connect=min(5.0, total), pool=min(5.0, total))


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


def embedding_space_fingerprint(provider: Any) -> str:
    """生成不含密钥的 Embedding 空间标识，供入库和检索共享同一算法。"""
    configuration = getattr(provider, "configuration", None)
    payload = {
        "provider": getattr(configuration, "provider", type(provider).__name__),
        "model": getattr(configuration, "model", settings.embedding_model),
        "baseUrl": getattr(configuration, "base_url", "local"),
        "dimension": settings.embedding_dimension,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
    def __init__(self, configuration: ResolvedModelConfig) -> None:
        self.configuration = configuration

    async def embed(self, texts: list[str]) -> list[list[float]]:
        configuration = self.configuration
        if not configuration.api_key:
            raise ProviderUnavailableError(
                "Embedding Provider 尚未配置",
                suggestion="请在平台环境中配置 EMBEDDING_API_KEY",
            )
        # 网络层异常（超时、连接失败等）统一归一化为 Provider 不可用，避免冒泡成 500
        try:
            async with httpx.AsyncClient(timeout=_build_timeout(30)) as client:
                response = await client.post(
                    f"{configuration.base_url.rstrip('/')}/embeddings",
                    headers={"Authorization": f"Bearer {configuration.api_key}"},
                    # 显式指定输出维度，避免 Provider 默认维度与数据库 1536 维列不一致
                    json={
                        "model": configuration.model,
                        "input": texts,
                        "dimensions": settings.embedding_dimension,
                    },
                )
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                "Embedding Provider 连接失败",
                details={"upstream": f"{type(exc).__name__}: {exc}"},
            ) from exc
        if response.status_code >= 400:
            raise ProviderUnavailableError(
                "Embedding Provider 调用失败",
                details={"statusCode": response.status_code},
            )
        # 上游异常时可能返回 HTML 错误页等非 JSON 内容，解析失败同样归为 Provider 不可用
        try:
            data = response.json().get("data", [])
        except json.JSONDecodeError as exc:
            raise ProviderUnavailableError(
                "Embedding Provider 返回了非 JSON 内容",
                details={"upstream": response.text[:200]},
            ) from exc
        vectors = [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]
        if len(vectors) != len(texts):
            raise ProviderUnavailableError("Embedding Provider 返回数量与输入不一致")
        # 兜底校验：部分 Provider 会忽略 dimensions 参数，静默返回其他维度
        mismatched = next(
            (len(vector) for vector in vectors if len(vector) != settings.embedding_dimension),
            None,
        )
        if mismatched is not None:
            raise EmbeddingDimensionError(
                f"Embedding Provider 返回 {mismatched} 维向量，知识库要求 "
                f"{settings.embedding_dimension} 维",
                suggestion="请在“设置 → 模型与联网能力”中为 Embedding 模型配置 "
                f"{settings.embedding_dimension} 维输出，或更换支持该维度的模型",
            )
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
    def __init__(self, configuration: ResolvedModelConfig) -> None:
        self.configuration = configuration

    @property
    def available(self) -> bool:
        return bool(self.configuration.api_key and self.configuration.model)

    async def generate(self, messages: list[dict[str, str]]) -> ChatResult:
        configuration = self.configuration
        if not self.available:
            raise ProviderUnavailableError("Chat Provider 尚未配置")
        # 网络层异常（超时、连接失败等）统一归一化为 Provider 不可用，避免冒泡成 500
        try:
            async with httpx.AsyncClient(
                timeout=_build_timeout(settings.answer_timeout_seconds)
            ) as client:
                response = await client.post(
                    f"{configuration.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {configuration.api_key}"},
                    json={
                        "model": configuration.model,
                        "messages": messages,
                        "temperature": 0.2,
                        "response_format": {"type": "json_object"},
                    },
                )
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                "Chat Provider 连接失败",
                details={"upstream": f"{type(exc).__name__}: {exc}"},
            ) from exc
        if response.status_code >= 400:
            raise ProviderUnavailableError(
                "Chat Provider 调用失败",
                details={"statusCode": response.status_code},
            )
        # 上游异常时可能返回 HTML 错误页等非 JSON 内容，解析失败同样归为 Provider 不可用
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderUnavailableError(
                "Chat Provider 返回了非 JSON 内容",
                details={"upstream": response.text[:200]},
            ) from exc
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
    def __init__(self, configuration: ResolvedModelConfig) -> None:
        self.configuration = configuration

    @property
    def available(self) -> bool:
        return bool(self.configuration.api_key)

    async def search(self, query: str, *, limit: int = 5) -> list[WebSearchHit]:
        configuration = self.configuration
        if not self.available:
            raise ProviderUnavailableError("联网搜索 Provider 尚未配置")
        # 网络层异常（超时、连接失败等）统一归一化为 Provider 不可用，避免冒泡成 500
        try:
            async with httpx.AsyncClient(timeout=_build_timeout(10)) as client:
                response = await client.post(
                    f"{configuration.base_url.rstrip('/')}/search",
                    headers={
                        "X-API-KEY": configuration.api_key,
                        "Content-Type": "application/json",
                    },
                    json={"q": query, "num": limit},
                )
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                "联网搜索连接失败",
                details={"upstream": f"{type(exc).__name__}: {exc}"},
            ) from exc
        if response.status_code >= 400:
            raise ProviderUnavailableError(
                "联网搜索调用失败",
                details={"statusCode": response.status_code},
            )
        # serper 异常时可能返回 HTML 错误页等非 JSON 内容，解析失败同样归为 Provider 不可用
        try:
            rows = response.json().get("organic") or []
        except json.JSONDecodeError as exc:
            raise ProviderUnavailableError(
                "联网搜索返回了非 JSON 内容",
                details={"upstream": response.text[:200]},
            ) from exc
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


def get_embedding_provider(application_id: UUID | str | None = None) -> EmbeddingProvider:
    configuration = resolve_capability_config(application_id, "embedding")
    if configuration.provider == "local_hash":
        return LocalHashEmbeddingProvider()
    if configuration.provider in {"openai", "openai_compatible"}:
        return OpenAICompatibleEmbeddingProvider(configuration)
    raise ProviderUnavailableError(
        f"不支持的 Embedding Provider：{configuration.provider}"
    )


def get_chat_provider(application_id: UUID | str | None = None) -> ChatProvider:
    configuration = resolve_capability_config(application_id, "chat")
    if configuration.provider in {"openai", "openai_compatible"}:
        return OpenAICompatibleChatProvider(configuration)
    return DisabledChatProvider()


def get_web_search_provider(application_id: UUID | str | None = None) -> WebSearchProvider:
    configuration = resolve_capability_config(application_id, "web_search")
    if configuration.provider == "serper":
        return SerperWebSearchProvider(configuration)
    return DisabledWebSearchProvider()


async def get_embedding_provider_async(
    session: AsyncSession, application_id: UUID | str | None, environment_id: UUID | str | None
) -> EmbeddingProvider:
    """按数据库优先配置创建向量 Provider；未回填时保留旧配置回退。"""
    configuration = await resolve_capability_config_async(
        session, application_id, environment_id, "embedding"
    )
    if configuration.provider == "local_hash":
        return LocalHashEmbeddingProvider()
    if configuration.provider in {"openai", "openai_compatible"}:
        return OpenAICompatibleEmbeddingProvider(configuration)
    raise ProviderUnavailableError(f"不支持的 Embedding Provider：{configuration.provider}")


async def get_chat_provider_async(
    session: AsyncSession, application_id: UUID | str | None, environment_id: UUID | str | None
) -> ChatProvider:
    """按数据库优先配置创建聊天 Provider；未配置时保持禁用 Provider 语义。"""
    configuration = await resolve_capability_config_async(
        session, application_id, environment_id, "chat"
    )
    if configuration.provider in {"openai", "openai_compatible"}:
        return OpenAICompatibleChatProvider(configuration)
    return DisabledChatProvider()


async def get_web_search_provider_async(
    session: AsyncSession, application_id: UUID | str | None, environment_id: UUID | str | None
) -> WebSearchProvider:
    """按数据库优先配置创建联网 Provider；未配置时保持禁用 Provider 语义。"""
    configuration = await resolve_capability_config_async(
        session, application_id, environment_id, "web_search"
    )
    if configuration.provider == "serper":
        return SerperWebSearchProvider(configuration)
    return DisabledWebSearchProvider()


def parse_json_object(content: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ProviderUnavailableError("模型没有返回合法 JSON") from exc
    if not isinstance(value, dict):
        raise ProviderUnavailableError("模型结构化输出必须是 JSON 对象")
    return value
