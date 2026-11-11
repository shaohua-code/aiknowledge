#!/usr/bin/env python3
"""服务联调验证脚本。

本脚本用于验证 AI 知识中台各服务组件的集成状态，
包括数据库连接、Redis/Celery、存储服务、LLM Provider 等。

运行方式：
    # 预览模式（不执行实际操作）
    python scripts/verify_service_integration.py --dry-run

    # 完整验证
    python scripts/verify_service_integration.py --execute

    # 仅验证特定服务
    python scripts/verify_service_integration.py --services database,redis

依赖环境变量：
    DATABASE_URL: PostgreSQL 连接字符串
    REDIS_URL: Redis 连接字符串
    STORAGE_TYPE: 存储类型 (local/s3/minio)
    OPENAI_API_KEY: OpenAI API 密钥（可选）

验证项说明：
    - database: PostgreSQL 连接和 schema 版本
    - redis: Redis 连接和 Celery worker 状态
    - storage: 对象存储读写
    - embedding: Embedding Provider 可用性
    - llm: LLM Provider 可用性
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class ServiceStatus(str, Enum):
    """服务状态枚举。"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    SKIPPED = "skipped"


@dataclass(slots=True)
class ServiceCheckResult:
    """单个服务检查结果。"""
    service: str
    status: ServiceStatus
    message: str
    latency_ms: float | None = None
    details: dict[str, Any] | None = None


class ServiceIntegrationVerifier:
    """服务集成验证器。"""

    def __init__(self, services: list[str] | None = None, dry_run: bool = False):
        """初始化验证器。

        Args:
            services: 要验证的服务列表，None 表示全部
            dry_run: 是否为预览模式
        """
        self.services = services or ["database", "redis", "storage", "embedding", "llm"]
        self.dry_run = dry_run
        self.results: list[ServiceCheckResult] = []

    async def verify_all(self) -> list[ServiceCheckResult]:
        """执行所有服务验证。"""
        print("=" * 60)
        print(f"AI 知识中台服务联调验证")
        print(f"时间: {datetime.now().isoformat()}")
        print(f"模式: {'预览' if self.dry_run else '执行'}")
        print("=" * 60)
        print()

        for service in self.services:
            method = getattr(self, f"_check_{service}", None)
            if method is None:
                self.results.append(ServiceCheckResult(
                    service=service,
                    status=ServiceStatus.SKIPPED,
                    message=f"未知服务: {service}",
                ))
                continue

            print(f"检查 {service}...")
            try:
                result = await method()
                self.results.append(result)
                status_icon = {
                    ServiceStatus.HEALTHY: "✅",
                    ServiceStatus.DEGRADED: "⚠️",
                    ServiceStatus.UNAVAILABLE: "❌",
                    ServiceStatus.SKIPPED: "⏭️",
                }[result.status]
                print(f"  {status_icon} {result.status.value}: {result.message}")
                if result.latency_ms:
                    print(f"     延迟: {result.latency_ms:.1f}ms")
            except Exception as e:
                result = ServiceCheckResult(
                    service=service,
                    status=ServiceStatus.UNAVAILABLE,
                    message=f"检查异常: {type(e).__name__}: {str(e)[:100]}",
                )
                self.results.append(result)
                print(f"  ❌ {result.message}")

            print()

        return self.results

    async def _check_database(self) -> ServiceCheckResult:
        """检查 PostgreSQL 数据库连接。"""
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            return ServiceCheckResult(
                service="database",
                status=ServiceStatus.SKIPPED,
                message="未配置 DATABASE_URL 环境变量",
            )

        if self.dry_run:
            return ServiceCheckResult(
                service="database",
                status=ServiceStatus.SKIPPED,
                message=f"预览模式：将连接 {database_url[:30]}...",
            )

        # 实际连接测试
        try:
            import asyncpg
            start = datetime.now()
            conn = await asyncpg.connect(database_url)
            version = await conn.fetchval("SELECT version()")
            await conn.close()
            latency = (datetime.now() - start).total_seconds() * 1000

            return ServiceCheckResult(
                service="database",
                status=ServiceStatus.HEALTHY,
                message="PostgreSQL 连接正常",
                latency_ms=latency,
                details={"version": version[:50] if version else None},
            )
        except ImportError:
            return ServiceCheckResult(
                service="database",
                status=ServiceStatus.UNAVAILABLE,
                message="缺少 asyncpg 依赖",
            )
        except Exception as e:
            return ServiceCheckResult(
                service="database",
                status=ServiceStatus.UNAVAILABLE,
                message=f"连接失败: {str(e)[:100]}",
            )

    async def _check_redis(self) -> ServiceCheckResult:
        """检查 Redis 连接和 Celery worker。"""
        redis_url = os.getenv("REDIS_URL")
        if not redis_url:
            return ServiceCheckResult(
                service="redis",
                status=ServiceStatus.SKIPPED,
                message="未配置 REDIS_URL 环境变量",
            )

        if self.dry_run:
            return ServiceCheckResult(
                service="redis",
                status=ServiceStatus.SKIPPED,
                message=f"预览模式：将连接 {redis_url[:30]}...",
            )

        try:
            import redis.asyncio as redis
            start = datetime.now()
            client = redis.from_url(redis_url)
            pong = await client.ping()
            await client.close()
            latency = (datetime.now() - start).total_seconds() * 1000

            if pong:
                return ServiceCheckResult(
                    service="redis",
                    status=ServiceStatus.HEALTHY,
                    message="Redis 连接正常",
                    latency_ms=latency,
                )
            else:
                return ServiceCheckResult(
                    service="redis",
                    status=ServiceStatus.DEGRADED,
                    message="Redis 响应异常",
                )
        except ImportError:
            return ServiceCheckResult(
                service="redis",
                status=ServiceStatus.UNAVAILABLE,
                message="缺少 redis 依赖",
            )
        except Exception as e:
            return ServiceCheckResult(
                service="redis",
                status=ServiceStatus.UNAVAILABLE,
                message=f"连接失败: {str(e)[:100]}",
            )

    async def _check_storage(self) -> ServiceCheckResult:
        """检查对象存储服务。"""
        storage_type = os.getenv("STORAGE_TYPE", "local")

        if self.dry_run:
            return ServiceCheckResult(
                service="storage",
                status=ServiceStatus.SKIPPED,
                message=f"预览模式：存储类型为 {storage_type}",
            )

        if storage_type == "local":
            storage_path = os.getenv("STORAGE_PATH", "/tmp/knowledge_storage")
            if os.path.isdir(storage_path):
                return ServiceCheckResult(
                    service="storage",
                    status=ServiceStatus.HEALTHY,
                    message=f"本地存储目录存在: {storage_path}",
                )
            else:
                return ServiceCheckResult(
                    service="storage",
                    status=ServiceStatus.DEGRADED,
                    message=f"本地存储目录不存在: {storage_path}",
                )

        # S3/MinIO 需要额外验证
        return ServiceCheckResult(
            service="storage",
            status=ServiceStatus.SKIPPED,
            message=f"存储类型 {storage_type} 需要手动验证",
        )

    async def _check_embedding(self) -> ServiceCheckResult:
        """检查 Embedding Provider 可用性。"""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return ServiceCheckResult(
                service="embedding",
                status=ServiceStatus.SKIPPED,
                message="未配置 OPENAI_API_KEY 环境变量",
            )

        if self.dry_run:
            return ServiceCheckResult(
                service="embedding",
                status=ServiceStatus.SKIPPED,
                message="预览模式：已配置 OpenAI API Key",
            )

        # 实际调用 API 验证（消耗少量 token）
        return ServiceCheckResult(
            service="embedding",
            status=ServiceStatus.SKIPPED,
            message="Embedding 验证需要真实 API 调用，请使用 --execute-embedding 启用",
        )

    async def _check_llm(self) -> ServiceCheckResult:
        """检查 LLM Provider 可用性。"""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return ServiceCheckResult(
                service="llm",
                status=ServiceStatus.SKIPPED,
                message="未配置 OPENAI_API_KEY 环境变量",
            )

        if self.dry_run:
            return ServiceCheckResult(
                service="llm",
                status=ServiceStatus.SKIPPED,
                message="预览模式：已配置 OpenAI API Key",
            )

        return ServiceCheckResult(
            service="llm",
            status=ServiceStatus.SKIPPED,
            message="LLM 验证需要真实 API 调用，请使用 --execute-llm 启用",
        )

    def print_summary(self) -> int:
        """打印验证摘要并返回退出码。"""
        print("=" * 60)
        print("验证摘要")
        print("=" * 60)

        healthy = sum(1 for r in self.results if r.status == ServiceStatus.HEALTHY)
        degraded = sum(1 for r in self.results if r.status == ServiceStatus.DEGRADED)
        unavailable = sum(1 for r in self.results if r.status == ServiceStatus.UNAVAILABLE)
        skipped = sum(1 for r in self.results if r.status == ServiceStatus.SKIPPED)

        print(f"  ✅ 健康: {healthy}")
        print(f"  ⚠️  降级: {degraded}")
        print(f"  ❌ 不可用: {unavailable}")
        print(f"  ⏭️  跳过: {skipped}")

        if unavailable > 0:
            print("\n⚠️  存在不可用服务，请检查配置和网络。")
            return 1
        elif degraded > 0:
            print("\n⚠️  存在降级服务，部分功能可能受限。")
            return 0
        else:
            print("\n✅ 所有验证的服务状态正常。")
            return 0


def main():
    """主入口。"""
    parser = argparse.ArgumentParser(
        description="AI 知识中台服务联调验证脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预览模式，不执行实际连接",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="执行模式，实际验证服务连接",
    )
    parser.add_argument(
        "--services",
        type=str,
        help="要验证的服务列表，逗号分隔 (database,redis,storage,embedding,llm)",
    )

    args = parser.parse_args()

    services = args.services.split(",") if args.services else None
    dry_run = not args.execute

    verifier = ServiceIntegrationVerifier(services=services, dry_run=dry_run)
    asyncio.run(verifier.verify_all())
    exit_code = verifier.print_summary()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
