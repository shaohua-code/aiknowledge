"""运行面 Redis 限流的原子性、故障策略与客户端释放回归测试。"""

from __future__ import annotations

from uuid import UUID

import pytest
from redis.exceptions import RedisError

from knowledge_core.config import settings
from knowledge_core.runtime import rate_limit
from knowledge_core.runtime.rate_limit import RateLimitError, check_rate_limit
from knowledge_core.shared.context import ApplicationContext
from knowledge_core.shared.errors import ProviderUnavailableError


class RecordingRedis:
    """记录脚本调用的最小 Redis 替身，不依赖真实限流服务。"""

    def __init__(self, *, count: int = 1, error: RedisError | None = None) -> None:
        self.count = count
        self.error = error
        self.eval_calls: list[tuple[str, int, str, int]] = []
        self.closed = False

    async def eval(self, script: str, numkeys: int, key: str, seconds: int) -> int:
        """模拟单次 Redis EVAL，并保留调用参数供原子边界断言。"""
        self.eval_calls.append((script, numkeys, key, seconds))
        if self.error is not None:
            raise self.error
        return self.count

    async def aclose(self) -> None:
        """模拟 API 进程关闭时的连接池释放。"""
        self.closed = True


def _context() -> ApplicationContext:
    """构造固定身份，令 Redis 键格式在断言中保持稳定。"""
    return ApplicationContext(
        application_id=UUID("00000000-0000-0000-0000-000000000001"),
        environment_id=UUID("00000000-0000-0000-0000-000000000002"),
        application_code="test-app",
        environment_code="test",
        api_key_id=UUID("00000000-0000-0000-0000-000000000003"),
        scopes=frozenset({"answer:read"}),
    )


@pytest.fixture(autouse=True)
async def reset_rate_limit_redis() -> None:
    """隔离进程级连接池，避免单测间保留替身或关闭状态。"""
    await rate_limit.close_rate_limit_redis()
    yield
    await rate_limit.close_rate_limit_redis()


async def test_rate_limit_uses_one_atomic_redis_script_and_reuses_client(monkeypatch) -> None:
    """递增和 TTL 初始化只能通过 EVAL 完成，多个请求复用同一客户端。"""
    redis = RecordingRedis()
    monkeypatch.setattr(rate_limit.Redis, "from_url", lambda *args, **kwargs: redis)

    await check_rate_limit(_context(), "answer")
    await check_rate_limit(_context(), "retrieve")

    assert redis.eval_calls[0] == (
        rate_limit._INCREMENT_WITH_EXPIRY_SCRIPT,
        1,
        "rate:00000000-0000-0000-0000-000000000001:"
        "00000000-0000-0000-0000-000000000002:"
        "00000000-0000-0000-0000-000000000003:answer",
        rate_limit._RATE_LIMIT_WINDOW_SECONDS,
    )
    assert redis.eval_calls[1] == (
        rate_limit._INCREMENT_WITH_EXPIRY_SCRIPT,
        1,
        "rate:00000000-0000-0000-0000-000000000001:"
        "00000000-0000-0000-0000-000000000002:"
        "00000000-0000-0000-0000-000000000003:retrieve",
        rate_limit._RATE_LIMIT_WINDOW_SECONDS,
    )
    assert "INCR" in rate_limit._INCREMENT_WITH_EXPIRY_SCRIPT
    assert "EXPIRE" in rate_limit._INCREMENT_WITH_EXPIRY_SCRIPT
    assert redis.closed is False


async def test_rate_limit_rejects_count_above_quota(monkeypatch) -> None:
    """脚本返回超额计数后仍应返回统一 429。"""
    redis = RecordingRedis(count=4)
    monkeypatch.setattr(rate_limit.Redis, "from_url", lambda *args, **kwargs: redis)
    monkeypatch.setattr(settings, "rate_limit_per_minute", 3)

    with pytest.raises(RateLimitError):
        await check_rate_limit(_context(), "answer")


async def test_rate_limit_fails_closed_when_production_redis_is_unavailable(monkeypatch) -> None:
    """生产环境失去 Redis 时不得放行无法计量的业务请求。"""
    redis = RecordingRedis(error=RedisError("offline"))
    monkeypatch.setattr(rate_limit.Redis, "from_url", lambda *args, **kwargs: redis)
    monkeypatch.setattr(settings, "app_env", "production")

    with pytest.raises(ProviderUnavailableError):
        await check_rate_limit(_context(), "answer")


async def test_rate_limit_degrades_in_development_when_redis_is_unavailable(monkeypatch) -> None:
    """开发环境保持既有降级行为，避免本地无 Redis 时阻塞调试。"""
    redis = RecordingRedis(error=RedisError("offline"))
    monkeypatch.setattr(rate_limit.Redis, "from_url", lambda *args, **kwargs: redis)
    monkeypatch.setattr(settings, "app_env", "development")

    await check_rate_limit(_context(), "answer")

    assert redis.closed is False


async def test_rate_limit_client_closes_on_process_shutdown(monkeypatch) -> None:
    """进程关闭统一回收共享连接池，避免复用策略泄露连接。"""
    redis = RecordingRedis()
    monkeypatch.setattr(rate_limit.Redis, "from_url", lambda *args, **kwargs: redis)

    await check_rate_limit(_context(), "answer")
    await rate_limit.close_rate_limit_redis()

    assert redis.closed is True
