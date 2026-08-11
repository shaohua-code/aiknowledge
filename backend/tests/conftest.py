"""pytest 全局 fixture：数据库会话与测试配置。

为跨项目隔离测试提供异步数据库会话 fixture ``db_session``。
数据库未启动时，``db_session`` 仍会 yield 一个会话，
由具体测试用例内的 ``_check_db_available`` 检测后跳过。

设计要点
--------
1. 使用应用自身的 ``AsyncSessionFactory`` 创建会话，保证与生产环境一致。
2. 测试结束自动回滚未提交的事务，避免测试数据污染。
3. 会话关闭交由 ``AsyncSession`` 上下文管理器处理。
"""
from __future__ import annotations

from typing import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionFactory


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """提供异步数据库会话 fixture。

    每个测试用例独立会话，测试结束自动回滚未提交事务并关闭会话。
    数据库连接失败时仍 yield 会话，由测试用例内 ``_check_db_available`` 跳过。

    Yields:
        AsyncSession: 异步数据库会话。
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
        finally:
            # 测试结束回滚未提交事务，避免污染其他测试
            await session.rollback()
            await session.close()
