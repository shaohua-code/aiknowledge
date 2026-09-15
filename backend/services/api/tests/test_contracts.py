from __future__ import annotations

from fastapi.testclient import TestClient
from kombu.exceptions import OperationalError

from knowledge_core.config import settings
from knowledge_core.control.model_catalog import router as model_catalog_router
from knowledge_core.control.model_configuration import router as model_configuration_router
from knowledge_core.main import _check_worker, app
from knowledge_core.shared.response import success


def test_openapi_contains_control_and_runtime_contracts() -> None:
    paths = app.openapi()["paths"]
    required = {
        "/control/v1/applications",
        "/control/v1/platform/model-configuration",
        "/control/v1/platform/model-configuration/test",
        "/control/v1/session/login",
        "/control/v1/applications/{application_id}/environments/{environment_id}/collections",
        "/control/v1/applications/{application_id}/environments/{environment_id}/ingestion-runs/{run_id}/retry",
        "/control/v1/applications/{application_id}/environments/{environment_id}/collections/{collection_id}/documents/remote-preview",
        "/control/v1/applications/{application_id}/environments/{environment_id}/documents/{document_id}/refresh",
        "/control/v1/applications/{application_id}/environments/{environment_id}/documents/{document_id}/versions/{revision_id}/rollback",
        "/control/v1/applications/{application_id}/environments/{environment_id}/operations/traces/{request_id}",
        "/control/v1/applications/{application_id}/environments/{environment_id}/operations/feedback",
        "/control/v1/applications/{application_id}/environments/{environment_id}/operations/ingestion-attempts",
        "/control/v1/applications/{application_id}/environments/{environment_id}/evaluations/sets",
        "/control/v1/applications/{application_id}/environments/{environment_id}/evaluations/sets/{set_id}/cases",
        "/control/v1/applications/{application_id}/environments/{environment_id}/evaluations/sets/{set_id}/cases/{case_code}/revisions",
        "/control/v1/applications/{application_id}/environments/{environment_id}/evaluations/sets/{set_id}/runs",
        "/control/v1/applications/{application_id}/environments/{environment_id}/evaluations/runs/{run_id}/results",
        "/control/v1/applications/{application_id}/environments/{environment_id}/answer-profiles/{profile_id}/debug",
        "/runtime/v1/retrieve",
        "/runtime/v1/answer",
        "/runtime/v1/feedback",
    }
    assert required <= set(paths)


def test_openapi_declares_health_and_readiness_response_schemas() -> None:
    """诊断端点虽不使用业务成功信封，仍须向控制台公开稳定的响应结构。"""
    paths = app.openapi()["paths"]
    health_response = paths["/health"]["get"]["responses"]["200"]["content"]["application/json"]
    ready_response = paths["/ready"]["get"]["responses"]

    assert health_response["schema"]["$ref"].endswith("/HealthResponse")
    assert ready_response["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ReadinessResponse"
    )
    # 基础依赖故障仍要暴露完整诊断结构，不能把 503 降级成无类型文本。
    assert ready_response["503"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ReadinessResponse"
    )


def test_openapi_declares_application_and_intelligence_success_schemas() -> None:
    """应用与策略控制面不能再把成功响应退化为未约束的自由对象。"""
    paths = app.openapi()["paths"]
    operations = (
        ("/control/v1/applications", "get", "200"),
        ("/control/v1/applications", "post", "201"),
        ("/control/v1/applications/{application_id}", "get", "200"),
        ("/control/v1/applications/{application_id}", "patch", "200"),
        (
            "/control/v1/applications/{application_id}/environments/{environment_id}",
            "patch",
            "200",
        ),
        (
            "/control/v1/applications/{application_id}/environments/{environment_id}/api-keys",
            "get",
            "200",
        ),
        (
            "/control/v1/applications/{application_id}/environments/{environment_id}/api-keys",
            "post",
            "201",
        ),
        (
            "/control/v1/applications/{application_id}/environments/{environment_id}/api-keys/{key_id}",
            "delete",
            "200",
        ),
        (
            "/control/v1/applications/{application_id}/environments/{environment_id}/retrieval-profiles",
            "get",
            "200",
        ),
        (
            "/control/v1/applications/{application_id}/environments/{environment_id}/retrieval-profiles",
            "post",
            "201",
        ),
        (
            "/control/v1/applications/{application_id}/environments/{environment_id}/answer-profiles",
            "get",
            "200",
        ),
        (
            "/control/v1/applications/{application_id}/environments/{environment_id}/answer-profiles",
            "post",
            "201",
        ),
        (
            "/control/v1/applications/{application_id}/environments/{environment_id}/retrieval-profiles/{profile_id}/revisions",
            "get",
            "200",
        ),
        (
            "/control/v1/applications/{application_id}/environments/{environment_id}/answer-profiles/{profile_id}/revisions",
            "get",
            "200",
        ),
        (
            "/control/v1/applications/{application_id}/environments/{environment_id}/profile-releases/{profile_id}",
            "get",
            "200",
        ),
        (
            "/control/v1/applications/{application_id}/environments/{environment_id}/retrieval-profiles/{profile_id}/revisions",
            "post",
            "201",
        ),
        (
            "/control/v1/applications/{application_id}/environments/{environment_id}/answer-profiles/{profile_id}/revisions",
            "post",
            "201",
        ),
        (
            "/control/v1/applications/{application_id}/environments/{environment_id}/answer-profiles/{profile_id}/debug",
            "post",
            "200",
        ),
        (
            "/control/v1/applications/{application_id}/environments/{environment_id}/retrieval-profiles/{profile_id}/revisions/{revision_id}",
            "put",
            "200",
        ),
        (
            "/control/v1/applications/{application_id}/environments/{environment_id}/answer-profiles/{profile_id}/revisions/{revision_id}",
            "put",
            "200",
        ),
        (
            "/control/v1/applications/{application_id}/environments/{environment_id}/retrieval-profiles/{profile_id}/revisions/{revision_id}/publish",
            "post",
            "200",
        ),
        (
            "/control/v1/applications/{application_id}/environments/{environment_id}/answer-profiles/{profile_id}/revisions/{revision_id}/publish",
            "post",
            "200",
        ),
        (
            "/control/v1/applications/{application_id}/environments/{environment_id}/retrieval-profiles/{profile_id}/revisions/{revision_id}/rollback",
            "post",
            "200",
        ),
        (
            "/control/v1/applications/{application_id}/environments/{environment_id}/answer-profiles/{profile_id}/revisions/{revision_id}/rollback",
            "post",
            "200",
        ),
    )

    for path, method, status in operations:
        schema = paths[path][method]["responses"][status]["content"]["application/json"]["schema"]
        assert schema["$ref"].startswith("#/components/schemas/ApiSuccess")


def test_openapi_declares_knowledge_success_schemas() -> None:
    """知识中心的异步入库与归档结果也必须拥有可生成的成功响应类型。"""
    paths = app.openapi()["paths"]
    base = "/control/v1/applications/{application_id}/environments/{environment_id}"
    operations = (
        (f"{base}/collections", "get", "200"),
        (f"{base}/collections", "post", "201"),
        (f"{base}/collections/{{collection_id}}/documents", "get", "200"),
        (f"{base}/collections/{{collection_id}}/documents/upload", "post", "202"),
        (f"{base}/collections/{{collection_id}}/documents/text", "post", "202"),
        (f"{base}/collections/{{collection_id}}/documents/remote", "post", "202"),
        (f"{base}/collections/{{collection_id}}/documents/remote-preview", "post", "200"),
        (f"{base}/ingestion-runs", "get", "200"),
        (f"{base}/ingestion-runs/{{run_id}}/retry", "post", "202"),
        (f"{base}/documents/{{document_id}}/versions/text", "post", "202"),
        (f"{base}/documents/{{document_id}}/versions/upload", "post", "202"),
        (f"{base}/documents/{{document_id}}/versions", "get", "200"),
        (f"{base}/documents/{{document_id}}/versions/{{revision_id}}/chunks", "get", "200"),
        (f"{base}/documents/{{document_id}}/versions/{{revision_id}}/rollback", "post", "200"),
        (f"{base}/documents/{{document_id}}/refresh", "post", "202"),
        (f"{base}/documents/{{document_id}}", "delete", "200"),
        (f"{base}/collections/{{collection_id}}", "delete", "200"),
    )

    for path, method, status in operations:
        schema = paths[path][method]["responses"][status]["content"]["application/json"]["schema"]
        assert schema["$ref"].startswith("#/components/schemas/ApiSuccess")

    schemas = app.openapi()["components"]["schemas"]
    revision_fields = schemas["DocumentRevisionView"]["properties"]
    chunk_fields = schemas["DocumentChunkView"]["properties"]
    # 控制台需要版本摘要和片段正文，但不应获得对象存储定位或数据库向量。
    assert {"storageKey", "embedding"}.isdisjoint(revision_fields)
    assert {"storageKey", "embedding"}.isdisjoint(chunk_fields)
    assert {"version", "contentHash", "status"} <= set(revision_fields)
    assert {"chunkIndex", "content", "tokenCount"} <= set(chunk_fields)


def test_knowledge_rollback_contract_is_explicit() -> None:
    """文档版本回滚必须返回当前文档视图，而不是模糊的自由对象。"""
    operation = app.openapi()["paths"][
        "/control/v1/applications/{application_id}/environments/{environment_id}/documents/{document_id}/versions/{revision_id}/rollback"
    ]["post"]
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["$ref"].startswith("#/components/schemas/ApiSuccess")


def test_async_knowledge_writes_expose_optional_idempotency_key() -> None:
    """异步入库与重试接口必须公开幂等键，兼容调用方逐步接入而不强制旧客户端改造。"""
    paths = app.openapi()["paths"]
    base = "/control/v1/applications/{application_id}/environments/{environment_id}"
    operations = (
        f"{base}/collections/{{collection_id}}/documents/upload",
        f"{base}/collections/{{collection_id}}/documents/text",
        f"{base}/collections/{{collection_id}}/documents/remote",
        f"{base}/ingestion-runs/{{run_id}}/retry",
        f"{base}/documents/{{document_id}}/versions/text",
        f"{base}/documents/{{document_id}}/versions/upload",
        f"{base}/documents/{{document_id}}/refresh",
    )
    for path in operations:
        parameter = next(
            item
            for item in paths[path]["post"]["parameters"]
            if item["name"] == "Idempotency-Key"
        )
        assert parameter["in"] == "header"
        assert parameter["required"] is False


def test_openapi_declares_operations_success_schemas() -> None:
    """运行中心摘要、轨迹、详情和审计事件都需要稳定的生成契约。"""
    paths = app.openapi()["paths"]
    base = "/control/v1/applications/{application_id}/environments/{environment_id}/operations"
    operations = (
        (f"{base}/summary", "get"),
        (f"{base}/traces", "get"),
        (f"{base}/traces/{{request_id}}", "get"),
        (f"{base}/audit-events", "get"),
        (f"{base}/feedback", "get"),
        (f"{base}/ingestion-attempts", "get"),
    )

    for path, method in operations:
        schema = paths[path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["$ref"].startswith("#/components/schemas/ApiSuccess")


def test_operations_trace_contract_exposes_non_negative_slow_request_filter() -> None:
    """运行中心必须把慢请求筛选下推给服务端，避免最新页数据掩盖历史慢调用。"""
    operation = app.openapi()["paths"][
        "/control/v1/applications/{application_id}/environments/{environment_id}/operations/traces"
    ]["get"]
    parameter = next(item for item in operation["parameters"] if item["name"] == "minTotalMs")

    assert parameter["in"] == "query"
    assert {entry.get("minimum") for entry in parameter["schema"]["anyOf"]} >= {0}

    trace_fields = app.openapi()["components"]["schemas"]["OperationTraceDetailResponse"][
        "properties"
    ]
    # 历史轨迹允许阶段耗时未知，但新运行必须能按检索、模型和联网阶段定位瓶颈。
    assert {"retrievalMs", "modelMs", "webMs"} <= set(trace_fields)


def test_operations_summary_contract_declares_completed_request_denominator_and_window() -> None:
    """概览必须公开统计窗口，并允许没有完成请求时明确返回未知指标。"""
    operation = app.openapi()["paths"][
        "/control/v1/applications/{application_id}/environments/{environment_id}/operations/summary"
    ]["get"]
    parameter = next(item for item in operation["parameters"] if item["name"] == "windowHours")
    fields = app.openapi()["components"]["schemas"]["OperationSummaryResponse"]["properties"]

    assert parameter["in"] == "query"
    assert parameter["schema"]["minimum"] == 1
    assert {
        "completedRequests",
        "runningRequests",
        "windowStartedAt",
        "windowEndedAt",
    } <= set(fields)
    assert any(entry.get("type") == "null" for entry in fields["successRate"]["anyOf"])


def test_openapi_debug_answer_does_not_accept_business_api_key() -> None:
    """管理员调试只使用会话身份，OpenAPI 不得诱导浏览器提交业务 Key。"""
    fields = app.openapi()["components"]["schemas"]["AnswerDebugRequest"]["properties"]

    assert {"query", "inputs"} <= set(fields)
    assert {"apiKey", "authorization", "profile"}.isdisjoint(fields)


def test_openapi_declares_evaluation_success_schemas() -> None:
    """评测集和样本修订同样必须生成稳定成功响应契约。"""
    paths = app.openapi()["paths"]
    base = "/control/v1/applications/{application_id}/environments/{environment_id}/evaluations"
    for path, method, status in (
        (f"{base}/sets", "get", "200"),
        (f"{base}/sets", "post", "201"),
        (f"{base}/sets/{{set_id}}/cases", "get", "200"),
        (f"{base}/sets/{{set_id}}/cases", "post", "201"),
        (f"{base}/sets/{{set_id}}/cases/{{case_code}}/revisions", "post", "201"),
        (f"{base}/sets/{{set_id}}/runs", "get", "200"),
        (f"{base}/sets/{{set_id}}/runs", "post", "201"),
        (f"{base}/runs/{{run_id}}/results", "get", "200"),
    ):
        schema = paths[path][method]["responses"][status]["content"]["application/json"]["schema"]
        assert schema["$ref"].startswith("#/components/schemas/ApiSuccess")


def test_openapi_declares_session_and_runtime_success_schemas() -> None:
    """会话和业务运行面也必须为调用方提供明确的成功响应契约。"""
    paths = app.openapi()["paths"]
    operations = (
        ("/control/v1/session/login", "post"),
        ("/control/v1/session/me", "get"),
        ("/control/v1/session", "delete"),
        ("/runtime/v1/retrieve", "post"),
        ("/runtime/v1/answer", "post"),
        ("/runtime/v1/feedback", "post"),
    )

    for path, method in operations:
        schema = paths[path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["$ref"].startswith("#/components/schemas/ApiSuccess")


def test_model_response_routes_preserve_existing_snake_case_runtime_contract() -> None:
    """历史模型数据保持 snake_case，但成功信封仍须维持 requestId。"""
    catalog_route = next(
        route
        for route in model_catalog_router.routes
        if route.path == "/control/v1/model-catalog" and "GET" in route.methods
    )
    configuration_route = next(
        route
        for route in model_configuration_router.routes
        if route.path == "/control/v1/platform/model-configuration" and "GET" in route.methods
    )
    catalog_payload = catalog_route.response_model.model_validate(
        success(
            {
                "items": [
                    {
                        "code": "legacy-chat",
                        "name": "历史 Chat",
                        "capability": "chat",
                        "provider": "openai_compatible",
                        "model": "example-model",
                        "base_url": "https://example.invalid/v1",
                        "api_key_env": "EXAMPLE_API_KEY",
                        "enabled": True,
                        "note": None,
                        "api_key_configured": True,
                    }
                ],
                "total": 1,
            }
        )
    ).model_dump(by_alias=catalog_route.response_model_by_alias)
    configuration_payload = configuration_route.response_model.model_validate(
        success(
            {
                "chat_provider": "disabled",
                "chat_model": "",
                "chat_base_url": "https://example.invalid/v1",
                "chat_api_key_env": "CHAT_API_KEY",
                "chat_api_key_configured": False,
                "embedding_provider": "local_hash",
                "embedding_model": "local-hash-v1",
                "embedding_base_url": "https://example.invalid/v1",
                "embedding_api_key_env": "EMBEDDING_API_KEY",
                "embedding_api_key_configured": False,
                "web_search_provider": "disabled",
                "web_search_base_url": "https://example.invalid/v1",
                "web_search_api_key_env": "WEB_SEARCH_API_KEY",
                "web_search_api_key_configured": False,
                "legacy_inline_credentials_detected": False,
            }
        )
    ).model_dump(by_alias=configuration_route.response_model_by_alias)

    assert catalog_payload["requestId"]
    assert catalog_payload["data"]["items"][0]["base_url"] == "https://example.invalid/v1"
    assert catalog_payload["data"]["items"][0]["api_key_configured"] is True
    assert configuration_payload["requestId"]
    assert configuration_payload["data"]["chat_provider"] == "disabled"
    assert configuration_payload["data"]["chat_api_key_env"] == "CHAT_API_KEY"
    assert configuration_payload["data"]["chat_api_key_configured"] is False


def test_session_cookie_is_http_only_and_request_id_is_returned(monkeypatch) -> None:
    monkeypatch.setattr(settings, "admin_email", "admin@example.com")
    monkeypatch.setattr(settings, "admin_password", "correct-password")
    with TestClient(app) as client:
        response = client.post(
            "/control/v1/session/login",
            json={"email": "admin@example.com", "password": "correct-password"},
        )
        assert response.status_code == 200
        assert response.headers["x-request-id"]
        assert "HttpOnly" in response.headers["set-cookie"]
        assert client.get("/control/v1/session/me").status_code == 200


def test_validation_error_uses_standard_envelope() -> None:
    with TestClient(app) as client:
        response = client.post("/control/v1/session/login", json={})
    payload = response.json()
    assert response.status_code == 422
    assert payload["success"] is False
    assert payload["requestId"]
    assert payload["error"]["code"] == "VALIDATION_ERROR"


async def test_worker_health_check_degrades_when_broker_is_offline(monkeypatch) -> None:
    class OfflineInspector:
        def ping(self) -> None:
            raise OperationalError("Redis offline")

    monkeypatch.setattr(
        "knowledge_core.main.celery_app.control.inspect",
        lambda timeout: OfflineInspector(),
    )

    assert await _check_worker() is False
