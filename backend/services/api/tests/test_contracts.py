from __future__ import annotations

from fastapi.testclient import TestClient
from kombu.exceptions import OperationalError

from knowledge_core.config import settings
from knowledge_core.main import _check_worker, app


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
        "/control/v1/applications/{application_id}/environments/{environment_id}/operations/traces/{request_id}",
        "/runtime/v1/retrieve",
        "/runtime/v1/answer",
        "/runtime/v1/feedback",
    }
    assert required <= set(paths)


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
