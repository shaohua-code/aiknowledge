"""敏感数据脱敏：统一处理日志与响应中的敏感字段。

对应 SubTask 23.3：日志中不应出现完整 API Key / 模型密钥 / 文档正文，
仅记录脱敏后的版本（保留前 4 后 4，中间 ****），避免泄露凭据。

设计要点
--------
1. **脱敏粒度**
   - API Key：保留前 4 + 后 4，中间 ``****``，便于运维识别 Key 归属
     而不泄露完整凭据
   - Authorization 头：剥离 ``Bearer `` 前缀后对 Token 脱敏
   - 模型密钥 / Web 搜索密钥：统一返回 ``***REDACTED***``，不保留任何字符
     （这些密钥业务方无需识别，完全隐藏最安全）
   - 文档正文：替换为 ``[content redacted, length=N]``，仅记录长度

2. **递归脱敏 dict**
   ``redact_dict`` 递归遍历嵌套 dict / list，遇到 ``SENSITIVE_LOG_KEYS``
   中的 key 时对 value 脱敏。用于：
   - 日志打印请求头 / 请求体前的预处理
   - 异常 details 写入前的预处理

3. **查询文本截断**
   ``truncate_query_for_log`` 仅记录查询长度与截断内容（超 100 字符），
   避免 retrieval_logs / UsageLog 中存储完整业务问题（可能含敏感信息）。

4. **常量化**
   ``SENSITIVE_LOG_KEYS`` 为 frozenset，避免被运行时修改；
   匹配时小写比较，兼容不同大小写命名（``X-API-Key`` vs ``api_key``）。
"""
from __future__ import annotations

from typing import Any

# 敏感日志 Key 集合：匹配时小写比较
# 包含常见凭据字段：Authorization 头、API Key、密码、Token、模型密钥等
SENSITIVE_LOG_KEYS: frozenset[str] = frozenset({
    "authorization",
    "x-api-key",
    "api_key",
    "password",
    "secret",
    "token",
    "model_api_key",
    "web_search_api_key",
    "chat_api_key",
    "embedding_api_key",
    "management_api_key",
})

# 默认截断长度：查询文本超此长度时仅保留前 100 字符
_DEFAULT_QUERY_MAX_LENGTH = 100


def redact_api_key(key: str) -> str:
    """脱敏 API Key：保留前 4 + 后 4，中间替换为 ``****``。

    用于日志中打印 API Key 时脱敏，便于运维识别 Key 归属
    （前 4 位与 key_prefix 一致）而不泄露完整凭据。

    边界情况：
        - 长度 <= 8：全部替换为 ``****``（前后 4 位会重叠，无法安全脱敏）
        - 空字符串：返回 ``""``
        - None：调用方应自行处理，本函数要求 str 入参

    Args:
        key: 原始 API Key 字符串。

    Returns:
        脱敏后的字符串，形如 ``abcd****wxyz``。
    """
    if not key:
        return ""
    # 长度不足 8 位时全部脱敏，避免前后 4 位重叠泄露完整 Key
    if len(key) <= 8:
        return "****"
    # 保留前 4 + 后 4，中间 ****
    return f"{key[:4]}****{key[-4:]}"


def redact_authorization_header(header: str) -> str:
    """脱敏 Authorization 头：剥离 ``Bearer `` 前缀后对 Token 脱敏。

    处理 ``Bearer xxx`` 格式：
        - 提取 Bearer 后的 Token
        - 调用 ``redact_api_key`` 对 Token 脱敏
        - 重新拼接为 ``Bearer abcd****wxyz`` 格式

    非 Bearer 格式（如 Basic auth）整体脱敏为 ``****``，
    避免泄露原始凭据。

    Args:
        header: 原始 Authorization 头，形如 ``Bearer ikh_live_xxx``。

    Returns:
        脱敏后的字符串，形如 ``Bearer abcd****wxyz``。
    """
    if not header:
        return ""
    # Bearer 格式：剥离前缀后对 Token 脱敏
    if header.startswith("Bearer "):
        token = header[len("Bearer "):].strip()
        return f"Bearer {redact_api_key(token)}"
    # 非 Bearer 格式（如 Basic）：整体脱敏，避免泄露
    return "****"


def redact_secret(value: str | None) -> str:
    """脱敏密钥类字段：统一返回 ``***REDACTED***``。

    用于模型 API Key、Web 搜索 API Key、管理密钥等业务方无需识别的凭据，
    完全隐藏任何字符（连前缀都不保留），最安全。

    Args:
        value: 原始密钥字符串，可空。

    Returns:
        固定字符串 ``***REDACTED***``。
    """
    # 无论 value 内容如何，统一返回固定占位符
    # 这样日志中不会泄露任何密钥信息
    return "***REDACTED***"


def redact_document_content(content: str, max_length: int = 80) -> str:
    """脱敏文档正文：替换为 ``[content redacted, length=N]``。

    文档正文可能包含敏感业务信息（如客户数据、财务数据），
    日志中不应存储完整正文，仅记录长度供运维判断规模。

    Args:
        content: 原始文档正文。
        max_length: 未使用参数，保留以兼容调用方签名。
            脱敏后始终为 ``[content redacted, length=N]`` 格式。

    Returns:
        脱敏后的字符串，形如 ``[content redacted, length=1234]``。
    """
    if not content:
        return "[content redacted, length=0]"
    # 仅记录长度，不保留任何正文内容
    return f"[content redacted, length={len(content)}]"


def truncate_query_for_log(query: str, max_length: int = _DEFAULT_QUERY_MAX_LENGTH) -> str:
    """截断查询文本用于日志记录：超长截断并标注长度。

    retrieval_logs / UsageLog 中不应存储完整业务问题（可能含敏感信息），
    仅记录截断后的查询与总长度，便于：
        - 运维判断查询规模（短查询 vs 长查询）
        - 排查性能问题时定位具体查询
        - 不泄露完整业务问题

    Args:
        query: 原始查询文本。
        max_length: 最大保留长度，默认 100 字符。

    Returns:
        截断后的查询文本，超长时附加 ``...[truncated, total=N]`` 后缀。
    """
    if not query:
        return ""
    # 未超长：原样返回
    if len(query) <= max_length:
        return query
    # 超长：截断并标注总长度
    return f"{query[:max_length]}...[truncated, total={len(query)}]"


def redact_dict(data: dict[str, Any]) -> dict[str, Any]:
    """递归脱敏 dict：遇到敏感 key 时对 value 脱敏。

    遍历规则：
        - dict：对每个 key 小写化后判断是否敏感，敏感则脱敏 value，
          非敏感则递归处理 value
        - list：对每个元素递归处理
        - 其他类型：原样返回

    用途：
        - 日志打印请求头 / 请求体前的预处理
        - 异常 details 写入前的预处理

    Args:
        data: 待脱敏的字典，可为嵌套结构。

    Returns:
        脱敏后的新字典（不修改原字典），敏感字段的 value 已替换。
    """
    if not isinstance(data, dict):
        # 非 dict 类型：原样返回（调用方应保证入参为 dict）
        return data  # type: ignore[return-value]

    redacted: dict[str, Any] = {}
    for key, value in data.items():
        # key 小写化后判断是否敏感（兼容 X-API-Key vs api_key）
        key_lower = str(key).lower()
        if key_lower in SENSITIVE_LOG_KEYS:
            # 敏感 key：根据类型脱敏
            if key_lower in ("authorization",):
                # Authorization 头：剥离 Bearer 后脱敏 Token
                redacted[key] = redact_authorization_header(str(value)) if value else ""
            elif key_lower in ("api_key", "x-api-key"):
                # API Key：保留前 4 后 4
                redacted[key] = redact_api_key(str(value)) if value else ""
            else:
                # 其他密钥类（password/secret/token/model_api_key 等）：完全隐藏
                redacted[key] = redact_secret(str(value) if value is not None else None)
        elif isinstance(value, dict):
            # 嵌套 dict：递归脱敏
            redacted[key] = redact_dict(value)
        elif isinstance(value, list):
            # 列表：对每个元素递归处理（元素可能是 dict）
            redacted[key] = [
                redact_dict(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            # 非敏感且非容器：原样保留
            redacted[key] = value
    return redacted
