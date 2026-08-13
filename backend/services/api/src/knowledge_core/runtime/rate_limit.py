from __future__ import annotations

import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from knowledge_core.config import settings
from knowledge_core.shared.context import ApplicationContext
from knowledge_core.shared.errors import CoreError, ProviderUnavailableError

logger = logging.getLogger(__name__)


class RateLimitError(CoreError):
    code = "RATE_LIMITED"
    title = "请求过于频繁"
    http_status = 429
    retryable = True


async def check_rate_limit(context: ApplicationContext, operation: str) -> None:
    key = f"rate:{context.application_id}:{context.environment_id}:{context.api_key_id}:{operation}"
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 60)
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
    finally:
        await redis.aclose()
