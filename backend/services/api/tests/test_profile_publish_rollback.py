"""策略发布与回滚流程 API 测试。

验证发布和回滚端点的权限控制和参数验证。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from knowledge_core.main import app


@pytest.fixture
def client():
    """创建测试客户端。"""
    return TestClient(app)


def test_publish_retrieval_revision_requires_admin(client: TestClient) -> None:
    """检索修订发布需要管理员会话，匿名请求应被拒绝。"""
    response = client.post(
        "/control/v1/applications/00000000-0000-0000-0000-000000000001"
        "/environments/00000000-0000-0000-0000-000000000001"
        "/retrieval-profiles/00000000-0000-0000-0000-000000000001"
        "/revisions/00000000-0000-0000-0000-000000000001/publish"
    )
    assert response.status_code == 401


def test_publish_answer_revision_requires_admin(client: TestClient) -> None:
    """回答修订发布需要管理员会话，匿名请求应被拒绝。"""
    response = client.post(
        "/control/v1/applications/00000000-0000-0000-0000-000000000001"
        "/environments/00000000-0000-0000-0000-000000000001"
        "/answer-profiles/00000000-0000-0000-0000-000000000001"
        "/revisions/00000000-0000-0000-0000-000000000001/publish"
    )
    assert response.status_code == 401


def test_rollback_retrieval_revision_requires_admin(client: TestClient) -> None:
    """检索修订回滚需要管理员会话，匿名请求应被拒绝。"""
    response = client.post(
        "/control/v1/applications/00000000-0000-0000-0000-000000000001"
        "/environments/00000000-0000-0000-0000-000000000001"
        "/retrieval-profiles/00000000-0000-0000-0000-000000000001"
        "/revisions/00000000-0000-0000-0000-000000000001/rollback"
    )
    assert response.status_code == 401


def test_rollback_answer_revision_requires_admin(client: TestClient) -> None:
    """回答修订回滚需要管理员会话，匿名请求应被拒绝。"""
    response = client.post(
        "/control/v1/applications/00000000-0000-0000-0000-000000000001"
        "/environments/00000000-0000-0000-0000-000000000001"
        "/answer-profiles/00000000-0000-0000-0000-000000000001"
        "/revisions/00000000-0000-0000-0000-000000000001/rollback"
    )
    assert response.status_code == 401


def test_debug_answer_profile_requires_admin(client: TestClient) -> None:
    """调试台调用需要管理员会话，匿名请求应被拒绝。"""
    response = client.post(
        "/control/v1/applications/00000000-0000-0000-0000-000000000001"
        "/environments/00000000-0000-0000-0000-000000000001"
        "/answer-profiles/00000000-0000-0000-0000-000000000001/debug",
        json={"query": "测试问题", "inputs": {}}
    )
    assert response.status_code == 401


def test_list_profile_revisions_requires_admin(client: TestClient) -> None:
    """修订列表查询需要管理员会话，匿名请求应被拒绝。"""
    response = client.get(
        "/control/v1/applications/00000000-0000-0000-0000-000000000001"
        "/environments/00000000-0000-0000-0000-000000000001"
        "/retrieval-profiles/00000000-0000-0000-0000-000000000001/revisions"
    )
    assert response.status_code == 401


def test_list_profile_releases_requires_admin(client: TestClient) -> None:
    """发布事件列表查询需要管理员会话，匿名请求应被拒绝。"""
    response = client.get(
        "/control/v1/applications/00000000-0000-0000-0000-000000000001"
        "/environments/00000000-0000-0000-0000-000000000001"
        "/profile-releases/00000000-0000-0000-0000-000000000001"
    )
    assert response.status_code == 401
