"""OpenAI 兼容 Embedding Provider：调用 ``/v1/embeddings`` 接口生成向量。

对应 SubTask 9.4：实现 ``OpenAIEmbeddingProvider``，覆盖以下场景：
- OpenAI 官方接口（``https://api.openai.com/v1``）
- Azure OpenAI（``https://{resource}.openai.azure.com``，需调整 path）
- 自托管 vLLM / TEI / Xinference 等兼容 OpenAI 接口的服务

设计要点
--------
1. **批量调用**：远端接口对单次请求的输入条数与 token 总数有限制
   （OpenAI 官方单次最多 2048 条输入），本实现按 ``BATCH_SIZE=64`` 分批，
   平衡单次延迟与请求次数。每批失败时独立重试，不影响其他批次。
2. **重试策略**：网络类错误（ConnectError / TimeoutException / 5xx）重试
   ``MAX_RETRIES=2`` 次，间隔 ``RETRY_BACKOFF`` 秒指数退避；
   4xx 业务错误（如 API Key 无效、模型不存在）不重试，直接抛出。
3. **维度校验**：首次调用后校验返回向量维度是否等于 ``settings.embedding_dimension``，
   避免配置错误导致后续批量写入 document_chunks.embedding 时触发 pgvector 列约束失败
   （列类型为 ``Vector(settings.embedding_dimension)``，维度不匹配会抛错）。
4. **顺序保证**：分批调用时按批次顺序拼接结果，保证 ``result[i]`` 对应 ``texts[i]``，
   入库流程依赖此顺序将向量与 chunk 一一对应写入。
5. **异步实现**：使用 ``httpx.AsyncClient``，可在 ``asyncio.run()`` 包装的
   Celery 同步任务中无缝调用，复用事件循环。
"""
from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)


class OpenAIEmbeddingProvider:
    """OpenAI 兼容 Embedding Provider。

    调用 ``{base_url}/embeddings`` 接口，请求体示例：
        ``{"model": "text-embedding-3-small", "input": ["text1", "text2"]}``
    响应体示例：
        ``{"data": [{"embedding": [0.1, 0.2, ...]}, {"embedding": [...]}]}``

    Attributes:
        api_key: API Key，从 ``settings.embedding_api_key`` 注入。
        base_url: 基础 URL，如 ``https://api.openai.com/v1``。
        model: 模型名称，如 ``text-embedding-3-small``。
        dimension: 期望向量维度，用于校验返回结果，与
            ``settings.embedding_dimension`` 一致。
    """

    # 单批次最大文本数：OpenAI 官方限制 2048，这里取 64 平衡延迟与吞吐
    BATCH_SIZE = 64
    # 最大重试次数：网络类错误（超时/连接失败/5xx）重试 2 次
    MAX_RETRIES = 2
    # 重试退避基数（秒）：第 1 次重试等待 1s，第 2 次等待 2s（指数退避）
    RETRY_BACKOFF = 1.0
    # 单次请求超时：连接 5s，读取 30s（Embedding 接口通常 < 10s，留足余量）
    REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=5.0)

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        dimension: int,
    ) -> None:
        """初始化 OpenAI 兼容 Embedding Provider。

        Args:
            api_key: API Key，请求头 ``Authorization: Bearer {api_key}``。
            base_url: 基础 URL，不含尾部斜杠，如 ``https://api.openai.com/v1``。
            model: 模型名称，如 ``text-embedding-3-small``。
            dimension: 期望向量维度，用于首次调用后校验返回结果。
        """
        self.api_key = api_key
        # 去除尾部斜杠，保证拼接 path 时不会出现 //embeddings
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dimension = dimension
        # 请求头：OpenAI 兼容接口统一用 Bearer Token 认证
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        # 单例 httpx 客户端：连接池复用，避免每次请求重建 TCP 连接
        # 注意：客户端在 __del__ 时未显式关闭，依赖进程退出时清理，
        # 长期运行场景下 Provider 单例化（lru_cache），不会泄漏
        self._client = httpx.AsyncClient(
            timeout=self.REQUEST_TIMEOUT,
            headers=self._headers,
        )

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """将文本列表批量转换为向量列表。

        流程：
            1. 空输入直接返回空列表
            2. 按 ``BATCH_SIZE`` 分批，每批独立调用远端接口
            3. 每批失败时重试 ``MAX_RETRIES`` 次（指数退避）
            4. 首次成功后校验返回向量维度
            5. 按批次顺序拼接结果，保证顺序与输入一致

        Args:
            texts: 待向量化的文本列表，长度 ≥ 1。

        Returns:
            向量列表，与 ``texts`` 等长且顺序一致，
            每条向量维度 = ``self.dimension``。

        Raises:
            ValueError: 文本列表为空，或返回向量维度与配置不一致。
            RuntimeError: 远端接口返回非 2xx，或重试后仍网络失败。
        """
        # 边界处理：空输入直接返回，避免发起无意义请求
        if not texts:
            return []

        # 分批：切片创建新列表，O(n) 但批量调用远比单条快
        batches = [
            texts[i : i + self.BATCH_SIZE]
            for i in range(0, len(texts), self.BATCH_SIZE)
        ]

        all_embeddings: list[list[float]] = []
        # 维度校验标志：仅首次成功调用后校验一次，避免重复检查开销
        dimension_checked = False

        for batch_idx, batch in enumerate(batches):
            # 单批调用：内部处理重试，失败时抛 RuntimeError
            batch_embeddings = await self._embed_batch_with_retry(batch, batch_idx)

            # 首批返回后校验维度：避免配置错误导致后续 pgvector 列约束失败
            if not dimension_checked and batch_embeddings:
                actual_dim = len(batch_embeddings[0])
                if actual_dim != self.dimension:
                    raise ValueError(
                        f"Embedding 维度不匹配：期望 {self.dimension}，"
                        f"实际 {actual_dim}（模型 {self.model}）。"
                        f"请检查 settings.embedding_dimension 与 embedding_model 是否匹配。"
                    )
                dimension_checked = True

            # 按批次顺序拼接，保证 result[i] 对应 texts[i]
            all_embeddings.extend(batch_embeddings)

        return all_embeddings

    async def _embed_batch_with_retry(
        self,
        batch: list[str],
        batch_idx: int,
    ) -> list[list[float]]:
        """单批调用 Embedding 接口，带指数退避重试。

        重试策略
        --------
        - 网络类错误（``httpx.ConnectError`` / ``httpx.TimeoutException``）：
          可重试，远端可能短暂不可用。
        - HTTP 5xx：可重试，服务端临时故障。
        - HTTP 4xx：不重试，业务错误（API Key 无效 / 模型不存在 / 输入超长），
          重试无意义，直接抛 ``RuntimeError`` 让上层标记任务 failed。

        Args:
            batch: 单批文本列表，长度 ≤ ``BATCH_SIZE``。
            batch_idx: 批次序号，仅用于日志。

        Returns:
            该批文本对应的向量列表，顺序与 ``batch`` 一致。

        Raises:
            RuntimeError: 重试耗尽仍失败，或收到不可重试的 4xx 错误。
        """
        last_exc: Exception | None = None

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                return await self._call_embeddings_api(batch)
            except httpx.ConnectError as exc:
                # 连接失败：可能 DNS 解析失败 / 目标不可达，可重试
                last_exc = exc
                logger.warning(
                    "Embedding 批次 %d 第 %d 次尝试连接失败：%s",
                    batch_idx,
                    attempt + 1,
                    exc,
                )
            except httpx.TimeoutException as exc:
                # 超时：可能网络抖动或服务端慢，可重试
                last_exc = exc
                logger.warning(
                    "Embedding 批次 %d 第 %d 次尝试超时：%s",
                    batch_idx,
                    attempt + 1,
                    exc,
                )
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if 400 <= status < 500:
                    # 4xx 业务错误：API Key 无效 / 模型不存在 / 输入超长，不重试
                    # 直接抛出，上层标记任务 failed，避免无意义重试占用配额
                    raise RuntimeError(
                        f"Embedding 接口返回 {status}（不可重试）："
                        f"{exc.response.text}"
                    ) from exc
                # 5xx 服务端错误：可重试
                last_exc = exc
                logger.warning(
                    "Embedding 批次 %d 第 %d 次尝试收到 %d：%s",
                    batch_idx,
                    attempt + 1,
                    status,
                    exc.response.text,
                )

            # 未到达最大重试次数则退避后继续
            if attempt < self.MAX_RETRIES:
                # 指数退避：第 1 次等 1s，第 2 次等 2s
                backoff = self.RETRY_BACKOFF * (2**attempt)
                await asyncio.sleep(backoff)

        # 重试耗尽：抛 RuntimeError，由 ingestion 任务捕获后标记 failed
        raise RuntimeError(
            f"Embedding 批次 {batch_idx} 重试 {self.MAX_RETRIES} 次后仍失败：{last_exc}"
        ) from last_exc

    async def _call_embeddings_api(self, batch: list[str]) -> list[list[float]]:
        """实际调用 Embedding 接口，返回向量列表。

        封装 HTTP 调用与响应解析，由 ``_embed_batch_with_retry`` 调用。
        本方法不做重试，仅抛出异常供上层判断是否可重试。

        Args:
            batch: 单批文本列表。

        Returns:
            向量列表，顺序与 ``batch`` 一致。

        Raises:
            httpx.HTTPStatusError: HTTP 非 2xx，由 ``raise_for_status`` 抛出。
            httpx.ConnectError / TimeoutException: 网络类错误。
            RuntimeError: 响应体格式异常（data 字段缺失 / 长度不匹配）。
        """
        url = f"{self.base_url}/embeddings"
        # 请求体：OpenAI 兼容接口标准字段
        payload = {
            "model": self.model,
            "input": batch,
        }

        # 发起 POST 请求
        response = await self._client.post(url, json=payload)
        # 非 2xx 抛 HTTPStatusError，由上层判断是否重试
        response.raise_for_status()

        data = response.json()
        # 响应体结构：{"data": [{"embedding": [...], "index": 0}, ...]}
        if "data" not in data:
            raise RuntimeError(f"Embedding 响应缺少 data 字段：{data}")

        embeddings_data = data["data"]
        # 长度校验：返回条数必须与输入一致，避免错位
        if len(embeddings_data) != len(batch):
            raise RuntimeError(
                f"Embedding 返回条数 {len(embeddings_data)} 与输入 {len(batch)} 不一致"
            )

        # 按 index 字段排序，确保顺序与输入一致（部分实现可能乱序返回）
        embeddings_data.sort(key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in embeddings_data]

    async def close(self) -> None:
        """显式关闭 httpx 客户端，释放连接池资源。

        Provider 通常单例化（``lru_cache``），进程退出前由 atexit 调用；
        测试场景下可手动调用以避免连接泄漏。
        """
        await self._client.aclose()
