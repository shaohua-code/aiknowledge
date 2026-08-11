"""数据库会话管理：异步 Engine、SessionFactory、依赖注入。

对应 Task 4 / Task 6：异步 SQLAlchemy 2 会话工厂。
本文件仅占位，具体实现由后续 Task 填充。
"""
from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# 异步 Engine：连接池参数由后续 Task 调优
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=False,
)

# 异步 Session 工厂
AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：注入异步数据库会话。

    Yields:
        AsyncSession: 异步会话实例，请求结束自动关闭。
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
