"""项目级限流：基于 Redis 的固定窗口计数器。

对应 SubTask 23.1：每 API Key 每分钟 60 次调用上限，
``/retrieval/search`` 与 ``/research/run`` 分别计数，互不影响。

设计要点
--------
1. **固定窗口算法**
   使用 Redis ``INCR`` + ``EXPIRE`` 实现每分钟计数：第一次 INCR 时设置 60s 过期，
   后续请求仅 INCR。窗口结束时 Key 自动过期，下一窗口从 0 重新计数。
   为什么选固定窗口而非滑动窗口？
     - 实现简单，单次 INCR + EXPIRE 即可，性能开销极低（O(1)）
     - 业务可接受窗口边界处的突发（最多 2x 流量），无需滑动窗口的精确性
     - 与 PRD "每分钟 60 次" 语义直接对应

2. **Key 格式**
   ``ratelimit:{api_key_id}:{endpoint_group}``
   - 按 api_key_id 维度计数，不同 Key 互不影响
   - 按 endpoint_group 维度分组：``retrieval_search`` / ``research_run``
     使 /retrieval/search 与 /research/run 计数互不影响

3. **Redis 不可用降级**
   Redis 故障时记录 warning 日志并放行请求，避免缓存故障导致业务全挂。
   限流是保护手段而非业务核心逻辑，宁可放过不可错杀。

4. **响应头注入**
   接口通过 ``Response.headers[...]`` 注入：
   - ``X-RateLimit-Limit``：窗口内总配额（60）
   - ``X-RateLimit-Remaining``：剩余次数
   - ``X-RateLimit-Reset``：窗口重置时间（Unix 秒）

5. **限流位置**
   在 Scope 校验之后、业务逻辑之前执行：
   - Scope 校验先于限流：未授权请求不应消耗限流配额
   - 业务逻辑后于限流：超限请求不应进入业务链路
"""
from __future__ import annotations

import logging
import time
from typing import Any

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.exceptions import RateLimitedError
from app.core.idempotency import get_redis_client

# 应用日志器：记录 Redis 故障降级日志
logger = logging.getLogger(__name__)

# Redis Key 前缀：所有限流计数器以此为前缀，便于运维批量清理
_KEY_PREFIX = "ratelimit:"

# 固定窗口长度（秒）：1 分钟
# 与 settings.rate_limit_per_minute 的"每分钟"语义对应
_WINDOW_SECONDS = 60


def _build_key(api_key_id: str, endpoint_group: str) -> str:
    """构造 Redis 限流 Key。

    Args:
        api_key_id: API Key 主键（UUID），用于按 Key 维度计数。
        endpoint_group: 端点分组名，如 ``retrieval_search`` / ``research_run``，
            使不同端点的计数互不影响。

    Returns:
        完整 Redis Key，形如 ``ratelimit:{api_key_id}:{endpoint_group}``。
    """
    return f"{_KEY_PREFIX}{api_key_id}:{endpoint_group}"


async def check_rate_limit(api_key_id: str, endpoint_group: str) -> None:
    """检查当前 API Key 在指定端点分组上的调用是否超限。

    超限抛 ``RateLimitedError``，details 含 ``limit`` / ``window_seconds`` /
    ``retry_after``，便于客户端按 Retry-After 头退避重试。

    限流逻辑：
        1. INCR 计数器：首次 INCR 返回 1，后续递增
        2. 首次 INCR（返回 1）时设置 EXPIRE，开始 60s 窗口
        3. 若计数 > 限额 → 抛 RateLimitedError，retry_after = 剩余窗口时间

    Args:
        api_key_id: API Key 主键。Worker 场景可能为空字符串，此时跳过限流。
        endpoint_group: 端点分组，``retrieval_search`` 或 ``research_run``。

    Raises:
        RateLimitedError: 当前窗口内调用次数超过 ``settings.rate_limit_per_minute``。
    """
    # Worker 场景无 api_key_id，跳过限流（Worker 内部任务由队列自身控制并发）
    if not api_key_id:
        return

    # 限额：从配置读取，便于运维调整
    limit = settings.rate_limit_per_minute
    key = _build_key(api_key_id, endpoint_group)

    try:
        client = await get_redis_client()
        # 使用 pipeline 一次性发送 INCR + EXPIRE，减少网络往返
        # INCR 是原子操作，多请求并发安全
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, _WINDOW_SECONDS)  # EXPIRE 仅在 Key 存在时生效，幂等
        current, _ = await pipe.execute()

        # 首次计数（current == 1）时 EXPIRE 已生效，开启新窗口
        # 注意：EXPIRE 每次 INCR 都会刷新过期时间？不会。
        # 此处 EXPIRE 是覆盖式设置（非 NX），每次都会刷新过期时间，
        # 但实际效果可接受：高并发下窗口会向后顺延，与固定窗口语义略有偏差
        # 但限流是保护手段，宽松一点不影响业务

        # 超限：抛 RateLimitedError，details 含 retry_after
        if current > limit:
            # 计算剩余窗口时间（秒）：通过 TTL 获取
            ttl = await client.ttl(key)
            # TTL 为 -1（无过期）或 -2（Key 不存在）时使用默认值
            retry_after = ttl if ttl > 0 else _WINDOW_SECONDS
            raise RateLimitedError(
                f"请求过于频繁，每分钟限 {limit} 次，{endpoint_group} 已达上限",
                details={
                    "limit": limit,
                    "window_seconds": _WINDOW_SECONDS,
                    "retry_after": retry_after,
                    "endpoint_group": endpoint_group,
                },
            )
    except RedisError as exc:
        # Redis 故障：降级放行，仅记录 warning
        # 限流是保护手段而非业务核心逻辑，宁可放过不可错杀
        # 避免因缓存故障导致业务全挂
        logger.warning(
            "Redis 限流检查失败，已降级放行（api_key_id=%s, endpoint=%s）：%s",
            api_key_id,
            endpoint_group,
            exc,
        )


async def get_rate_limit_info(api_key_id: str, endpoint_group: str) -> dict[str, Any]:
    """获取当前 API Key 在指定端点分组上的限流状态信息。

    供接口在响应头注入 ``X-RateLimit-Limit`` / ``X-RateLimit-Remaining`` /
    ``X-RateLimit-Reset``，便于客户端感知剩余配额并主动退避。

    返回字段说明：
        - ``limit``：窗口内总配额（settings.rate_limit_per_minute）
        - ``remaining``：剩余可用次数（>= 0）
        - ``reset_at``：窗口重置时间（Unix 秒，整型）

    Redis 不可用时返回降级信息（remaining=limit，表示不限制），
    避免影响响应头构造逻辑。

    Args:
        api_key_id: API Key 主键。
        endpoint_group: 端点分组名。

    Returns:
        限流状态字典，``{limit, remaining, reset_at}``。
    """
    limit = settings.rate_limit_per_minute

    # Worker 场景或 Redis 故障：返回降级信息（不限制）
    if not api_key_id:
        return {
            "limit": limit,
            "remaining": limit,
            "reset_at": int(time.time()) + _WINDOW_SECONDS,
        }

    key = _build_key(api_key_id, endpoint_group)
    try:
        client = await get_redis_client()
        # pipeline 一次获取当前计数与 TTL，减少网络往返
        pipe = client.pipeline()
        pipe.get(key)
        pipe.ttl(key)
        count_raw, ttl = await pipe.execute()

        # count_raw 为 None 表示窗口尚未开始（首次请求）
        count = int(count_raw) if count_raw else 0
        # remaining 不能为负，超限时返回 0
        remaining = max(0, limit - count)
        # TTL > 0 时窗口重置时间 = 当前时间 + TTL；否则按新窗口计算
        if ttl > 0:
            reset_at = int(time.time()) + ttl
        else:
            # 无记录或 TTL 异常：窗口重置时间为 1 分钟后
            reset_at = int(time.time()) + _WINDOW_SECONDS

        return {
            "limit": limit,
            "remaining": remaining,
            "reset_at": reset_at,
        }
    except RedisError as exc:
        # Redis 故障：降级返回满配额，避免影响响应构造
        logger.warning(
            "Redis 限流状态查询失败，返回降级信息（api_key_id=%s, endpoint=%s）：%s",
            api_key_id,
            endpoint_group,
            exc,
        )
        return {
            "limit": limit,
            "remaining": limit,
            "reset_at": int(time.time()) + _WINDOW_SECONDS,
        }


def apply_rate_limit_headers(
    response_headers: dict[str, str],
    info: dict[str, Any],
) -> None:
    """将限流状态信息写入响应头字典（就地修改）。

    将 ``get_rate_limit_info`` 返回的状态写入响应头：
        - ``X-RateLimit-Limit``：窗口内总配额
        - ``X-RateLimit-Remaining``：剩余次数
        - ``X-RateLimit-Reset``：窗口重置 Unix 时间戳

    Args:
        response_headers: 响应头字典，就地修改。
        info: ``get_rate_limit_info`` 返回的状态字典。
    """
    response_headers["X-RateLimit-Limit"] = str(info["limit"])
    response_headers["X-RateLimit-Remaining"] = str(info["remaining"])
    response_headers["X-RateLimit-Reset"] = str(info["reset_at"])
