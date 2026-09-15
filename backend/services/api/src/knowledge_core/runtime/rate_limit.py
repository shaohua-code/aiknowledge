from __future__ import annotations

import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from knowledge_core.config import settings
from knowledge_core.shared.context import ApplicationContext
from knowledge_core.shared.errors import CoreError, ProviderUnavailableError

logger = logging.getLogger(__name__)

# 递增与首次过期必须在 Redis 内作为一个脚本完成；进程若在两条命令之间中断，
# 独立的 INCR/EXPIRE 会留下永久计数，导致该 Key 被错误地持续限流。
_RATE_LIMIT_WINDOW_SECONDS = 60
_INCREMENT_WITH_EXPIRY_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""
_rate_limit_redis: Redis | None = None


class RateLimitError(CoreError):
    code = "RATE_LIMITED"
    title = "请求过于频繁"
    http_status = 429
    retryable = True


def get_rate_limit_redis() -> Redis:
    """复用进程内 Redis 连接池，避免每个运行面请求创建客户端。"""
    global _rate_limit_redis
    if _rate_limit_redis is None:
        _rate_limit_redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return _rate_limit_redis


async def close_rate_limit_redis() -> None:
    """在 API 进程退出时释放限流连接池，避免热重载遗留连接。"""
    global _rate_limit_redis
    if _rate_limit_redis is not None:
        await _rate_limit_redis.aclose()
        _rate_limit_redis = None


async def check_rate_limit(context: ApplicationContext, operation: str) -> None:
    """在固定一分钟窗口内原子计数，并保留生产环境的故障关闭策略。"""
    key = f"rate:{context.application_id}:{context.environment_id}:{context.api_key_id}:{operation}"
    redis = get_rate_limit_redis()
    try:
        # EVAL 将递增和过期写入同一 Redis 原子执行单元，避免中途失败留下无 TTL 的键。
        count = int(
            await redis.eval(
                _INCREMENT_WITH_EXPIRY_SCRIPT,
                1,
                key,
                _RATE_LIMIT_WINDOW_SECONDS,
            )
        )
        if count > settings.rate_limit_per_minute:
            raise RateLimitError(
                "当前 API Key 已超过每分钟调用上限",
                details={"limit": settings.rate_limit_per_minute},
                suggestion="请稍后重试或调整应用配额",
            )
    except RedisError as exc:
        if settings.app_env == "production":
            raise ProviderUnavailableError("限流服务不可用，生产环境拒绝继续执行") from exc
        logger.warning("开发环境限流服务不可用，已跳过限流", exc_info=True)
