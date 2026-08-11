"""OpenAI 兼容聊天模型 Provider：调用 ``/v1/chat/completions`` 接口生成补全。

对应 SubTask 15.1：实现 ``OpenAIChatModelProvider``，覆盖以下场景：
- OpenAI 官方接口（``https://api.openai.com/v1``）
- Azure OpenAI（需调整 path）
- 自托管 vLLM / Xinference 等兼容 OpenAI 接口的服务

设计要点（务必阅读）
--------------------
1. **单次调用**
   短链路研究遵循"一次生成"原则：整个研究流程只调用 1 次聊天模型。
   本实现不内置重试，超时或失败直接抛异常，由 ResearchService 决定是否降级。
   重试会显著增加延迟（指数退避后可能 10s+），破坏 P95 ≤ 12s 目标。

2. **JSON 模式**
   研究链路固定使用 ``response_format={"type": "json_object"}`` 启用 JSON 模式：
   - OpenAI 官方接口支持 ``json_object`` 模式，保证输出为合法 JSON
   - 自托管 vLLM 等兼容接口若不支持该参数，可由 ResearchService 走兜底解析
     （正则提取 JSON 块），但优先依赖原生 JSON 模式
   - JSON 模式要求 prompt 中包含 "json" 关键字（OpenAI 限制），ResearchService
     在构造 prompt 时已显式要求 JSON 输出

3. **超时控制**
   研究整体硬超时 15s，取证阶段约 5~7s，留给模型生成约 5s。
   本实现 ``MODEL_TIMEOUT_SECONDS=5``，超时抛 ``ModelTimeoutError``，
   ResearchService 捕获后标记 degraded，返回已整理证据。
   为什么不设更长？5s 足以覆盖 gpt-4o-mini 等轻量模型的生成（通常 1~3s），
   留更短超时能在模型卡死时快速降级，而非拖垮整个研究链路。

4. **Token 统计**
   响应体 ``usage`` 字段携带 Token 统计，本实现解析后填入 ``ChatCompletionResult``，
   ResearchService 据此写入 ``usage_logs`` 表，供成本核算与限流决策。
   缺失 ``usage`` 字段时填 0，保证流程不中断。

5. **异常转换**
   - ``httpx.TimeoutException`` → ``ModelTimeoutError``（HTTP 504，可重试）
   - ``httpx.HTTPStatusError`` → ``ExternalSourceFailedError``（HTTP 502，可重试）
   - 不捕获 ``httpx.ConnectError``，让其向上传播为未预期错误（连接失败通常为配置错误）
   - 不内置重试，避免短链路场景下的延迟放大
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.exceptions import ExternalSourceFailedError, ModelTimeoutError
from app.providers.chat_models import ChatCompletionResult, ChatMessage

logger = logging.getLogger(__name__)


class OpenAIChatModelProvider:
    """OpenAI 兼容聊天模型 Provider。

    调用 ``{base_url}/chat/completions`` 接口，请求体示例：
        ``{"model": "gpt-4o-mini", "messages": [...], "temperature": 0.3, "response_format": {...}}``
    响应体示例：
        ``{"choices": [{"message": {"content": "..."}, "finish_reason": "stop"}], "usage": {...}}``

    Attributes:
        api_key: API Key，从 ``settings.chat_api_key`` 注入。
        base_url: 基础 URL，如 ``https://api.openai.com/v1``。
        model: 模型名称，如 ``gpt-4o-mini``。
    """

    # 模型调用超时：5s，与研究硬超时 15s 配合
    # 取证阶段约 5~7s，留 5s 给模型生成，超时则降级返回已整理证据
    MODEL_TIMEOUT_SECONDS = 5

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        """初始化 OpenAI 兼容聊天模型 Provider。

        Args:
            api_key: API Key，请求头 ``Authorization: Bearer {api_key}``。
            base_url: 基础 URL，不含尾部斜杠，如 ``https://api.openai.com/v1``。
            model: 模型名称，如 ``gpt-4o-mini``。
        """
        self.api_key = api_key
        # 去除尾部斜杠，保证拼接 path 时不会出现 //chat/completions
        self.base_url = base_url.rstrip("/")
        self.model = model
        # 请求头：OpenAI 兼容接口统一用 Bearer Token 认证
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        # 单例 httpx 客户端：连接池复用，避免每次请求重建 TCP 连接
        # 超时配置：5s 读取（模型生成），5s 连接（建连阶段）
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.MODEL_TIMEOUT_SECONDS, connect=5.0),
            headers=self._headers,
        )

    async def complete(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.3,
        response_format: dict[str, Any] | None = None,
    ) -> ChatCompletionResult:
        """调用聊天模型生成补全（单次请求，不重试）。

        流程：
            1. 构造请求体（model / messages / temperature / response_format）
            2. POST 到 ``{base_url}/chat/completions``
            3. 超时 → ``ModelTimeoutError``；HTTP 非 2xx → ``ExternalSourceFailedError``
            4. 解析响应：choices[0].message.content + usage Token 统计
            5. 返回 ``ChatCompletionResult``

        为什么不重试？
            短链路研究预算 15s，单次模型调用约 1~3s。重试（即使无退避）也会让
            总耗时翻倍，极易触发整体硬超时。失败时直接降级返回证据，
            比拖垮整个请求更可控。

        Args:
            messages: 消息列表，通常为 [system, user] 两条。
            temperature: 采样温度，默认 0.3。研究场景偏低温度保证输出稳定。
            response_format: 响应格式约束，研究链路固定传
                ``{"type": "json_object"}`` 启用 JSON 模式。

        Returns:
            ``ChatCompletionResult`` 实例，含模型输出与 Token 统计。

        Raises:
            ModelTimeoutError: 模型调用超时（5s）。
            ExternalSourceFailedError: 模型接口返回非 2xx。
        """
        url = f"{self.base_url}/chat/completions"
        # 构造请求体：OpenAI 兼容接口标准字段
        payload: dict[str, Any] = {
            "model": self.model,
            # ChatMessage dataclass 转 dict 列表
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
        }
        # JSON 模式：response_format 非空时加入请求体
        # OpenAI 官方支持 {"type": "json_object"}，保证输出为合法 JSON
        if response_format is not None:
            payload["response_format"] = response_format

        try:
            # 发起 POST 请求，超时由客户端配置控制（5s）
            response = await self._client.post(url, json=payload)
            # 非 2xx 抛 HTTPStatusError，由下方 except 捕获转换
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            # 超时：转为 ModelTimeoutError，ResearchService 捕获后标记降级
            # 不重试，避免拖垮研究链路
            logger.warning(
                "聊天模型调用超时（model=%s, timeout=%ds）：%s",
                self.model,
                self.MODEL_TIMEOUT_SECONDS,
                exc,
            )
            raise ModelTimeoutError(
                f"模型调用超时（{self.MODEL_TIMEOUT_SECONDS}s）",
                details={
                    "model": self.model,
                    "timeout_seconds": self.MODEL_TIMEOUT_SECONDS,
                },
            ) from exc
        except httpx.HTTPStatusError as exc:
            # HTTP 非 2xx：转为 ExternalSourceFailedError
            # 4xx（API Key 无效/模型不存在）与 5xx（服务端故障）都不重试
            status = exc.response.status_code
            logger.warning(
                "聊天模型接口返回 %d（model=%s）：%s",
                status,
                self.model,
                exc.response.text,
            )
            raise ExternalSourceFailedError(
                f"模型接口返回 {status}",
                details={
                    "model": self.model,
                    "status_code": status,
                    "response_body": exc.response.text,
                },
            ) from exc

        # 解析响应体
        data = response.json()
        # choices 数组：取第一条（研究链路 n=1，仅请求 1 条补全）
        choices = data.get("choices") or []
        if not choices:
            raise ExternalSourceFailedError(
                "模型响应缺少 choices 字段",
                details={"model": self.model, "response": data},
            )

        choice = choices[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        finish_reason = choice.get("finish_reason") or ""

        # Token 统计：usage 字段可能缺失（部分兼容实现不返回），缺失时填 0
        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))
        total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens))
        # 实际生成模型：响应中可能返回具体版本号（如 gpt-4o-mini-2024-07-18）
        model_name = data.get("model", self.model)

        return ChatCompletionResult(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            model=model_name,
            finish_reason=finish_reason,
        )

    async def close(self) -> None:
        """显式关闭 httpx 客户端，释放连接池资源。

        Provider 通常单例化（``lru_cache``），进程退出前由 atexit 调用；
        测试场景下可手动调用以避免连接泄漏。
        """
        await self._client.aclose()
