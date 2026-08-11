"""Embedding Provider 抽象层：将文本转向量，支持 OpenAI 兼容接口。

对应 SubTask 9.4：定义 ``EmbeddingProvider`` Protocol 与工厂函数
``get_embedding_provider``，业务代码依赖抽象协议而非具体实现，
便于在 OpenAI / 自托管 / 开源模型间切换而无需修改入库流程。

设计要点
--------
1. ``EmbeddingProvider`` 是 ``Protocol``（结构化子类型），实现类无需显式继承，
   只需提供 ``embed_texts`` 方法即可被工厂返回，符合"鸭子类型"。
2. ``get_embedding_provider`` 根据 ``settings.embedding_provider`` 返回对应实现，
   用 ``lru_cache`` 缓存单例，避免每次入库任务都重建 httpx 客户端。
3. 实现类应保证 ``embed_texts`` 返回的向量维度与 ``settings.embedding_dimension``
   一致，调用方在写入 document_chunks.embedding 前会做维度校验，避免入库后
   因维度不匹配导致 pgvector 列约束失败。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Embedding Provider 协议：将文本列表转换为向量列表。

    所有实现（如 ``OpenAIEmbeddingProvider``）必须满足此协议，
    入库流程通过此协议访问 Provider，不依赖具体实现类。

    协议要求的方法签名
    ------------------
    ``async embed_texts(texts: list[str]) -> list[list[float]]``

    - 输入：文本列表，长度 1 ~ N（实现内部按 batch_size 分批调用远端接口）
    - 输出：与输入等长的向量列表，每条向量是 ``list[float]``，
      维度必须等于 ``settings.embedding_dimension``
    - 实现需保证输入与输出的顺序一致（texts[i] 对应 result[i]），
      入库流程依赖此顺序将向量与 chunk 一一对应写入数据库

    异常处理
    --------
    - 远端接口返回非 2xx：实现应抛 ``RuntimeError``，由调用方决定是否重试
    - 维度不匹配：实现应在内部抛 ``ValueError``，避免脏数据入库
    - 网络超时/连接失败：实现应在内部重试 N 次后抛出底层异常
    """

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """将文本列表批量转换为向量列表。

        Args:
            texts: 待向量化的文本列表，长度 ≥ 1。

        Returns:
            向量列表，与 ``texts`` 等长且顺序一致，
            每条向量维度 = ``settings.embedding_dimension``。
        """
        ...


@lru_cache
def get_embedding_provider() -> Any:
    """获取 Embedding Provider 单例。

    根据 ``settings.embedding_provider`` 返回对应实现：
        - ``openai``：返回 ``OpenAIEmbeddingProvider``，调用 OpenAI 兼容
          ``/v1/embeddings`` 接口（支持 OpenAI 官方 / Azure / 自托管 vLLM 等）

    使用 ``lru_cache`` 缓存单例的原因：
        - Provider 内部持有 httpx.AsyncClient，重复创建会浪费连接池资源
        - 配置在运行期不变，缓存安全
        - 测试时可通过 ``get_embedding_provider.cache_clear()`` 重置

    Returns:
        满足 ``EmbeddingProvider`` 协议的实例。

    Raises:
        ValueError: 配置的 provider 不被支持。
    """
    from app.core.config import settings

    provider = settings.embedding_provider.lower()

    if provider == "openai":
        # OpenAI 兼容 Provider：覆盖 OpenAI 官方、Azure OpenAI、自托管 vLLM 等
        from app.providers.embeddings.openai_provider import OpenAIEmbeddingProvider

        return OpenAIEmbeddingProvider(
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_base_url,
            model=settings.embedding_model,
            dimension=settings.embedding_dimension,
        )

    raise ValueError(f"不支持的 Embedding Provider：{provider}")
