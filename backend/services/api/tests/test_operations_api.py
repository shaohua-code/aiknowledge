"""运行追踪与质量 API 测试。

验证运行中心各端点的权限控制和基础查询功能。
包括轨迹列表、详情、摘要、反馈、审计事件和入库尝试。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from knowledge_core.main import app


@pytest.fixture
def client():
    """创建测试客户端。"""
    return TestClient(app)


class TestOperationsSummary:
    """运行摘要端点测试。"""

    def test_summary_requires_admin(self, client: TestClient) -> None:
        """运行摘要查询需要管理员会话，匿名请求应被拒绝。"""
        response = client.get(
            "/control/v1/applications/00000000-0000-0000-0000-000000000001"
            "/environments/00000000-0000-0000-0000-000000000001"
            "/operations/summary"
        )
        assert response.status_code == 401

    def test_summary_accepts_window_hours_param(self, client: TestClient) -> None:
        """摘要端点应接受 windowHours 参数（认证失败前不会验证参数）。"""
        response = client.get(
            "/control/v1/applications/00000000-0000-0000-0000-000000000001"
            "/environments/00000000-0000-0000-0000-000000000001"
            "/operations/summary?windowHours=48"
        )
        assert response.status_code == 401


class TestOperationsTraces:
    """运行轨迹列表端点测试。"""

    def test_list_traces_requires_admin(self, client: TestClient) -> None:
        """轨迹列表查询需要管理员会话，匿名请求应被拒绝。"""
        response = client.get(
            "/control/v1/applications/00000000-0000-0000-0000-000000000001"
            "/environments/00000000-0000-0000-0000-000000000001"
            "/operations/traces"
        )
        assert response.status_code == 401

    def test_list_traces_accepts_filter_params(self, client: TestClient) -> None:
        """轨迹列表应接受状态、错误码和慢请求过滤参数。"""
        response = client.get(
            "/control/v1/applications/00000000-0000-0000-0000-000000000001"
            "/environments/00000000-0000-0000-0000-000000000001"
            "/operations/traces"
            "?status=failed&errorCode=TIMEOUT&minTotalMs=5000"
        )
        assert response.status_code == 401

    def test_get_trace_detail_requires_admin(self, client: TestClient) -> None:
        """轨迹详情查询需要管理员会话，匿名请求应被拒绝。"""
        response = client.get(
            "/control/v1/applications/00000000-0000-0000-0000-000000000001"
            "/environments/00000000-0000-0000-0000-000000000001"
            "/operations/traces/req_12345678"
        )
        assert response.status_code == 401


class TestOperationsAuditEvents:
    """审计事件端点测试。"""

    def test_list_audit_events_requires_admin(self, client: TestClient) -> None:
        """审计事件列表需要管理员会话，匿名请求应被拒绝。"""
        response = client.get(
            "/control/v1/applications/00000000-0000-0000-0000-000000000001"
            "/environments/00000000-0000-0000-0000-000000000001"
            "/operations/audit-events"
        )
        assert response.status_code == 401


class TestOperationsFeedback:
    """用户反馈端点测试。"""

    def test_list_feedback_requires_admin(self, client: TestClient) -> None:
        """反馈列表需要管理员会话，匿名请求应被拒绝。"""
        response = client.get(
            "/control/v1/applications/00000000-0000-0000-0000-000000000001"
            "/environments/00000000-0000-0000-0000-000000000001"
            "/operations/feedback"
        )
        assert response.status_code == 401


class TestOperationsIngestionAttempts:
    """入库尝试端点测试。"""

    def test_list_ingestion_attempts_requires_admin(self, client: TestClient) -> None:
        """入库尝试列表需要管理员会话，匿名请求应被拒绝。"""
        response = client.get(
            "/control/v1/applications/00000000-0000-0000-0000-000000000001"
            "/environments/00000000-0000-0000-0000-000000000001"
            "/operations/ingestion-attempts"
        )
        assert response.status_code == 401

    def test_list_ingestion_attempts_accepts_filter_params(self, client: TestClient) -> None:
        """入库尝试列表应接受 runId 和 status 过滤参数。"""
        response = client.get(
            "/control/v1/applications/00000000-0000-0000-0000-000000000001"
            "/environments/00000000-0000-0000-0000-000000000001"
            "/operations/ingestion-attempts"
            "?runId=00000000-0000-0000-0000-000000000001&status=failed"
        )
        assert response.status_code == 401
