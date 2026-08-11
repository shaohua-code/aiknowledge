"""幂等性存储：基于 Redis 实现 24h 幂等键去重。

对应 SubTask 8.4：所有写入接口（文件上传、文本/URL 写入）支持 ``Idempotency-Key``
请求头，避免客户端因网络抖动重试导致重复创建文档与入库任务。

幂等机制说明（务必阅读）
------------------------
1. 为什么需要幂等？
   网络不稳定时客户端可能重试同一请求，若服务端不识别会创建多个相同文档
   与入库任务，造成存储与计算资源浪费，并产生重复 chunk 影响检索质量。

2. 幂等键冲突策略
   - 相同 Key + 相同请求内容 → 返回原响应（"重放"）
   - 相同 Key + 不同请求内容 → 抛 ``IdempotencyConflictError``（409）
   - 不存在 → 处理请求并存储响应

3. 为什么 24h TTL？
   - 24h 覆盖绝大多数客户端重试窗口（含跨日重试）
   - 避免无限期占用 Redis 内存
   - 与对象存储、数据库中的实际资源解耦：TTL 过期后即使客户端重试，
     通过 ``content_hash`` 去重仍能避免重复入库

4. request_hash 包含什么？
   - 文件上传：文件名 + 文件大小 + 文件 SHA-256 + title + tags
   - 文本/URL：请求体 JSON 的 SHA-256
   通过对比 request_hash 判断"内容是否相同"

5. Redis Key 格式：``idempotency:{key}``，值为 JSON 字符串
   ``{"request_hash": "...", "response": {...}}``

6. 为什么使用 ``redis.asyncio``？
   FastAPI 是异步框架，使用同步 Redis 客户端会阻塞事件循环；
   ``redis.asyncio`` 提供原生协程接口，与 SQLAlchemy 异步会话无缝配合。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import redis.asyncio as redis

from app.core.config import settings

# 默认 TTL：24 小时（86400 秒），覆盖客户端重试窗口
DEFAULT_TTL_SECONDS = 86400

# Redis Key 前缀：所有幂等记录都以此为前缀，便于运维批量清理
_KEY_PREFIX = "idempotency:"

# Redis 客户端单例：全局复用连接池
# 使用 module 级变量而非 lru_cache，便于测试通过 setattr 替换
_redis_client: redis.Redis | None = None


async def get_redis_client() -> redis.Redis:
    """获取 Redis 异步客户端单例。

    使用 ``redis.asyncio.Redis`` 解析 ``settings.redis_url``，
    内部维护连接池，多次调用复用同一客户端。

    Returns:
        Redis 异步客户端实例。
    """
    global _redis_client
    if _redis_client is None:
        # from_url 自动解析 redis://[:password@]host:port/db
        # decode_responses=True 让返回值为 str 而非 bytes，便于 JSON 处理
        _redis_client = redis.from_url(
            settings.redis_url,
            decode_responses=True,
        )
    return _redis_client


def _build_key(key: str) -> str:
    """构造 Redis 完整 Key。

    Args:
        key: 业务传入的幂等键（客户端生成的 UUID/ULID 等）。

    Returns:
        完整 Redis Key，形如 ``idempotency:abc-123``。
    """
    return f"{_KEY_PREFIX}{key}"


def compute_request_hash(body: dict | bytes) -> str:
    """计算请求内容的 SHA-256 哈希。

    用于幂等校验时对比两次请求内容是否相同：
        - dict：序列化为 JSON 后哈希（按 key 排序保证稳定性）
        - bytes：直接哈希（文件上传场景，调用方预先组装好 bytes）

    Args:
        body: 请求内容，可为 dict（JSON 请求体）或 bytes（文件上传场景）。

    Returns:
        SHA-256 十六进制字符串（64 字符）。
    """
    sha = hashlib.sha256()
    if isinstance(body, bytes):
        # 文件上传：调用方已组装好 bytes（含文件名/大小/content_hash/title/tags）
        sha.update(body)
    else:
        # JSON 请求体：sort_keys=True 保证字段顺序稳定，避免序列化顺序差异
        # ensure_ascii=False 保留中文原字符，不同 Python 版本序列化一致
        payload = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
        sha.update(payload)
    return sha.hexdigest()


def compute_file_request_hash(
    filename: str,
    file_size: int,
    content_hash: str,
    title: str | None,
    tags: list[str] | None,
) -> str:
    """计算文件上传请求的哈希（专用于 SubTask 8.1）。

    文件上传使用 multipart/form-data，无法直接序列化为 dict，
    因此显式拼接关键字段计算哈希：
        - filename：文件名变化视为不同请求
        - file_size：文件大小变化视为不同请求
        - content_hash：文件内容 SHA-256，是最强判等依据
        - title：标题变化视为不同请求
        - tags：标签变化视为不同请求

    为什么 content_hash 已经能判等还要包含其他字段？
        content_hash 仅反映文件内容，相同内容但 title/tags 不同的请求
        应视为不同业务意图（如同一文件归档到不同分类），需创建不同文档。

    Args:
        filename: 上传文件名。
        file_size: 文件大小（字节）。
        content_hash: 文件内容 SHA-256。
        title: 文档标题，可空。
        tags: 标签列表，可空。

    Returns:
        SHA-256 十六进制字符串。
    """
    # 拼接关键字段为稳定字符串，再计算 SHA-256
    # 使用 JSON 序列化保证结构稳定（避免字符串拼接歧义）
    payload = json.dumps(
        {
            "filename": filename,
            "file_size": file_size,
            "content_hash": content_hash,
            "title": title,
            "tags": sorted(tags) if tags else [],
        },
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


async def get_idempotency_record(key: str) -> dict[str, Any] | None:
    """读取幂等记录。

    从 Redis 读取 ``idempotency:{key}`` 对应的 JSON 记录。

    Args:
        key: 业务幂等键。

    Returns:
        记录字典，形如 ``{"request_hash": "...", "response": {...}}``；
        不存在返回 None。
    """
    client = await get_redis_client()
    raw = await client.get(_build_key(key))
    if raw is None:
        # 不存在：首次请求或已过期
        return None
    try:
        # 解析 JSON 字符串为 dict
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # 数据损坏：视为不存在，重新写入覆盖
        return None


async def set_idempotency_record(
    key: str,
    request_hash: str,
    response: dict[str, Any],
    ttl: int = DEFAULT_TTL_SECONDS,
) -> None:
    """存储幂等记录（含请求哈希与响应内容）。

    在请求处理完成后调用，将响应内容缓存 24h，
    后续相同 Key + 相同 request_hash 的请求可直接重放此响应。

    Args:
        key: 业务幂等键。
        request_hash: 请求内容哈希（由 ``compute_request_hash`` 计算）。
        response: 待缓存的响应体（标准 ApiResponse.success 返回的 dict）。
        ttl: 过期时间（秒），默认 24h。
    """
    client = await get_redis_client()
    record = {
        "request_hash": request_hash,
        "response": response,
    }
    # 序列化为 JSON 字符串存储
    payload = json.dumps(record, ensure_ascii=False)
    # SET key value EX ttl：EX 表示秒级过期
    await client.set(_build_key(key), payload, ex=ttl)


async def check_idempotency(
    key: str | None,
    request_hash: str,
) -> dict[str, Any] | None:
    """幂等校验通用入口（供上传/写入接口复用）。

    校验逻辑：
        1. ``key`` 为 None → 跳过幂等校验，返回 None（无 Idempotency-Key 头）
        2. Redis 中无记录 → 首次请求，返回 None（调用方处理完后再写入）
        3. Redis 中有记录且 request_hash 匹配 → 重放，返回原响应
        4. Redis 中有记录但 request_hash 不匹配 → 抛 ``IdempotencyConflictError``

    为什么把"写入记录"留给调用方？
        本函数仅做"读 + 判等"，写入需在请求处理完成后调用 ``set_idempotency_record``，
        保证只有成功响应才被缓存（失败响应不缓存，便于客户端重试）。

    Args:
        key: 业务幂等键，None 表示未提供 Idempotency-Key 头，跳过校验。
        request_hash: 当前请求的内容哈希。

    Returns:
        - None：首次请求或未提供 Key，调用方应正常处理
        - dict：重放的响应体，调用方应直接返回（不再处理）

    Raises:
        IdempotencyConflictError: 相同 Key 但 request_hash 不一致。
    """
    if key is None:
        # 未提供 Idempotency-Key：跳过幂等校验，由 content_hash 去重兜底
        return None

    record = await get_idempotency_record(key)
    if record is None:
        # 无记录：首次请求，调用方处理完后应调用 set_idempotency_record
        return None

    stored_hash = record.get("request_hash")
    if stored_hash == request_hash:
        # 哈希匹配：重放原响应，避免重复创建
        return record.get("response")

    # 哈希不匹配：相同 Key 不同内容，拒绝以避免误用
    # 延迟导入避免循环依赖
    from app.core.exceptions import IdempotencyConflictError

    raise IdempotencyConflictError(
        f"幂等键 {key} 已用于不同内容的请求",
        details={
            "idempotency_key": key,
            "stored_request_hash": stored_hash,
            "current_request_hash": request_hash,
        },
    )
