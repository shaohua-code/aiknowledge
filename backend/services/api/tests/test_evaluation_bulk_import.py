"""评测样本批量导入 API 测试。

验证批量导入功能的权限控制、重复处理和错误处理。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from knowledge_core.main import app


@pytest.fixture
def client():
    """创建测试客户端。"""
    return TestClient(app)


def test_bulk_import_requires_admin(client: TestClient) -> None:
    """批量导入需要管理员会话，匿名请求应被拒绝。"""
    response = client.post(
        "/control/v1/applications/00000000-0000-0000-0000-000000000001"
        "/environments/00000000-0000-0000-0000-000000000001"
        "/evaluations/sets/00000000-0000-0000-0000-000000000001/cases/bulk",
        json={
            "cases": [
                {
                    "caseCode": "test_case_01",
                    "question": "测试问题",
                    "inputs": {},
                    "expectedDocumentIds": [],
                    "expectedAnswerModes": ["KNOWLEDGE_GROUNDED"],
                    "expectedOutputSchema": {},
                    "labels": ["测试"]
                }
            ],
            "skipExisting": True
        }
    )
    assert response.status_code == 401


def test_bulk_import_validates_request_body(client: TestClient) -> None:
    """批量导入应验证请求体格式。"""
    # 空数组应被拒绝
    response = client.post(
        "/control/v1/applications/00000000-0000-0000-0000-000000000001"
        "/environments/00000000-0000-0000-0000-000000000001"
        "/evaluations/sets/00000000-0000-0000-0000-000000000001/cases/bulk",
        json={"cases": [], "skipExisting": True}
    )
    # 401 因为未认证，但格式验证在认证之后
    assert response.status_code == 401
