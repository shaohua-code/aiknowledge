from __future__ import annotations

from fastapi.testclient import TestClient

from knowledge_core.config import settings
from knowledge_core.main import app


def test_openapi_contains_control_and_runtime_contracts() -> None:
    paths = app.openapi()["paths"]
    required = {
        "/control/v1/applications",
        "/control/v1/session/login",
        "/control/v1/applications/{application_id}/environments/{environment_id}/collections",
        "/control/v1/applications/{application_id}/environments/{environment_id}/ingestion-runs/{run_id}/retry",
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
