"""聊天模型 Provider 抽象层：支持 OpenAI 兼容接口，单次请求最多调用 1 次模型。

对应 SubTask 15.1：定义 ``ChatModelProvider`` Protocol 与工厂函数
``get_chat_model_provider``，业务代码（研究链路）依赖抽象协议而非具体实现，
便于在 OpenAI 官方 / Azure / 自托管 vLLM 等兼容接口间切换。

设计理念（务必阅读）
--------------------
1. **一次模型生成**
   短链路研究遵循"一次生成"原则：整个研究流程只调用 1 次聊天模型。
   多次调用会显著增加延迟（每次 1~3s）与成本，难以满足 P95 ≤ 12s 的目标。
   因此 ``ChatModelProvider.complete`` 设计为单次调用接口，调用方（ResearchService）
   仅在证据整理完成后发起 1 次请求，不重试、不补充追问。

2. **Protocol 结构化子类型**
   ``ChatModelProvider`` 是 ``Protocol``，实现类无需显式继承，
   只需提供 ``complete`` 方法即可被工厂返回，符合"鸭子类型"。
   这与 ``EmbeddingProvider`` 保持一致的抽象风格。

3. **JSON 模式**
   研究链路要求模型返回结构化 JSON（结论/建议/置信度/不确定性），
   ``complete`` 接口接收 ``response_format`` 参数，传 ``{"type": "json_object"}``
   启用 OpenAI 兼容的 JSON 模式，保证输出可被 ``json.loads`` 解析，
   避免正则提取 JSON 块的脆弱方案。

4. **硬超时配合**
   研究整体硬超时 15s（``settings.research_hard_timeout_seconds``），
   取证阶段约消耗 5~7s，留给模型生成约 5s。
   ``complete`` 实现内部通过 httpx 超时控制（``MODEL_TIMEOUT_SECONDS=5``），
   超时抛 ``ModelTimeoutError``，由 ResearchService 捕获后标记降级，
   返回已整理证据而非中断整个请求。

5. **Token 统计**
   ``ChatCompletionResult`` 携带 ``prompt_tokens`` / ``completion_tokens`` /
   ``total_tokens``，由 ResearchService 写入 ``usage_logs`` 表，供成本核算与优化分析。
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol, runtime_checkable


@dataclass
class ChatMessage:
    """聊天消息：单条对话内容。

    OpenAI 兼容接口的消息结构为 ``{"role": "...", "content": "..."}``，
    本 dataclass 提供类型安全的 Python 封装，由 Provider 在调用时转为 dict。

    Attributes:
        role: 消息角色，``system`` / ``user`` / ``assistant``。
            研究链路中通常构造 1 条 system 消息（提示词）+ 1 条 user 消息
            （证据列表 + 问题），不使用多轮对话。
        content: 消息文本内容。system 消息包含系统提示词、证据规则、禁止事项、
            输出 schema、风险模板；user 消息包含证据列表与用户问题。
    """

    role: str
    content: str


@dataclass
class ChatCompletionResult:
    """聊天模型补全结果：携带模型输出与 Token 统计。

    由 ``ChatModelProvider.complete`` 返回，ResearchService 据此解析 JSON
    并写入 ``usage_logs`` 表用于成本核算。

    Attributes:
        content: 模型输出的文本内容。JSON 模式下为 JSON 字符串，
            由 ResearchService 调用 ``json.loads`` 解析。
        prompt_tokens: 提示词 Token 数（输入），用于成本核算。
        completion_tokens: 补全 Token 数（输出），用于成本核算。
        total_tokens: 总 Token 数 = prompt_tokens + completion_tokens。
        model: 实际生成模型名称（响应中返回的 model，可能与请求模型不同，
            如 OpenAI 可能返回 ``gpt-4o-mini-2024-07-18``）。
        finish_reason: 完成原因，``stop``=正常结束 / ``length``=达到长度限制 /
            ``content_filter``=内容过滤。用于判断输出是否被截断。
    """

    content: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model: str
    finish_reason: str


@runtime_checkable
class ChatModelProvider(Protocol):
    """聊天模型 Provider 协议：单次调用聊天模型生成补全。

    所有实现（如 ``OpenAIChatModelProvider``）必须满足此协议，
    研究链路通过此协议访问 Provider，不依赖具体实现类。

    协议要求的方法签名
    ------------------
    ``async complete(messages, temperature=0.3, response_format=None) -> ChatCompletionResult``

    - 输入：消息列表 + 温度 + 可选响应格式
    - 输出：``ChatCompletionResult``，含模型输出文本与 Token 统计
    - 实现需保证单次调用，不重试、不补充追问（短链路一次生成原则）

    异常处理
    --------
    - 调用超时：实现应抛 ``ModelTimeoutError``，ResearchService 捕获后标记降级
    - HTTP 4xx/5xx：实现应抛 ``ExternalSourceFailedError``，ResearchService 捕获后标记降级
    - 实现内部不应吞掉异常，统一向上抛出由调用方决定降级策略
    """

    async def complete(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.3,
        response_format: dict[str, Any] | None = None,
    ) -> ChatCompletionResult:
        """调用聊天模型生成补全。

        Args:
            messages: 消息列表，通常为 [system, user] 两条。
            temperature: 采样温度，默认 0.3。研究场景偏低温度保证输出稳定、
                可复现，避免高温导致的发散与虚构。
            response_format: 响应格式约束，``None`` 表示普通文本，
                ``{"type": "json_object"}`` 表示 JSON 模式（研究链路固定使用）。

        Returns:
            ``ChatCompletionResult`` 实例。

        Raises:
            ModelTimeoutError: 模型调用超时。
            ExternalSourceFailedError: 模型接口返回非 2xx。
        """
        ...


@lru_cache
def get_chat_model_provider() -> ChatModelProvider | None:
    """获取聊天模型 Provider 单例。

    根据 ``settings.chat_provider`` 返回对应实现：
        - ``openai``：返回 ``OpenAIChatModelProvider``，调用 OpenAI 兼容
          ``/v1/chat/completions`` 接口（支持 OpenAI 官方 / Azure / 自托管 vLLM 等）

    为什么用 ``lru_cache`` 缓存单例？
        - Provider 内部持有 ``httpx.AsyncClient``，重复创建会浪费连接池资源
        - 配置在运行期不变，缓存安全
        - 测试时可通过 ``get_chat_model_provider.cache_clear()`` 重置

    为什么返回 ``None`` 而非抛异常？
        - 聊天模型未配置时（如 ``chat_api_key`` 为空），研究链路仍可降级运行：
          返回已整理证据 + 降级标记，而非中断请求
        - 调用方（ResearchService）需检查 ``None`` 并走降级路径

    Returns:
        满足 ``ChatModelProvider`` 协议的实例；未配置 API Key 或 Provider
        不被支持时返回 ``None``，调用方据此降级。
    """
    from app.core.config import settings

    # 未配置 API Key：返回 None，调用方走降级路径（仅返回证据）
    if not settings.chat_api_key:
        return None

    provider = settings.chat_provider.lower()

    if provider == "openai":
        # OpenAI 兼容 Provider：覆盖 OpenAI 官方、Azure OpenAI、自托管 vLLM 等
        from app.providers.chat_models.openai_provider import OpenAIChatModelProvider

        return OpenAIChatModelProvider(
            api_key=settings.chat_api_key,
            base_url=settings.chat_base_url,
            model=settings.chat_model,
        )

    # 未支持的 Provider：返回 None，调用方降级
    return None
