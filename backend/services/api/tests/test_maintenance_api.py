"""维护 API 功能测试。

验证孤儿清理状态查询、预览和执行端点的行为正确性。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from knowledge_core.main import app


@pytest.fixture
def client():
    """创建测试客户端，不依赖数据库连接。"""
    return TestClient(app)


def test_orphan_cleanup_status_requires_admin(client: TestClient) -> None:
    """孤儿清理状态查询需要管理员会话，匿名请求应被拒绝。"""
    response = client.get("/control/v1/maintenance/orphan-cleanup/status")
    assert response.status_code == 401


def test_orphan_cleanup_preview_requires_admin(client: TestClient) -> None:
    """孤儿清理预览需要管理员会话，匿名请求应被拒绝。"""
    response = client.get("/control/v1/maintenance/orphan-cleanup/preview")
    assert response.status_code == 401


def test_orphan_cleanup_execute_requires_admin(client: TestClient) -> None:
    """孤儿清理执行需要管理员会话，匿名请求应被拒绝。"""
    response = client.post("/control/v1/maintenance/orphan-cleanup/execute")
    assert response.status_code == 401


def test_idempotency_stats_requires_admin(client: TestClient) -> None:
    """幂等记录统计查询需要管理员会话，匿名请求应被拒绝。"""
    response = client.get("/control/v1/maintenance/idempotency/stats")
    assert response.status_code == 401
