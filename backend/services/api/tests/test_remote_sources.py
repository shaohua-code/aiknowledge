from __future__ import annotations

import httpx
import pytest

from knowledge_core.infrastructure import http_safety
from knowledge_core.infrastructure.http_safety import (
    RemoteFetchRequest,
    RemoteSourceError,
    fetch_public_source,
    validate_remote_request,
)


@pytest.fixture
def allow_public_host(monkeypatch):
    async def allow(_hostname: str) -> None:
        return None

    monkeypatch.setattr(http_safety, "_assert_public_host", allow)


@pytest.mark.asyncio
async def test_json_api_supports_path_and_chinese_text(allow_public_host) -> None:
    payload = '{"data":{"items":[{"title":"中文资料","score":9}]}}'.encode()
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers={"content-type": "application/json; charset=utf-8"},
            content=payload,
        )
    )
    result = await fetch_public_source(
        RemoteFetchRequest(
            url="https://example.com/api",
            source_type="api",
            json_path="data.items",
        ),
        transport=transport,
    )
    assert result.mime_type == "application/json"
    assert result.extension == ".json"
    assert "中文资料" in result.content.decode()


@pytest.mark.asyncio
async def test_fetch_follows_safe_redirect_and_retries_server_error(allow_public_host) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"})
        if calls == 2:
            return httpx.Response(503)
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="ready")

    result = await fetch_public_source(
        RemoteFetchRequest(url="https://example.com/start", retry_count=1),
        transport=httpx.MockTransport(handler),
    )
    assert result.content == b"ready"
    assert result.final_url.endswith("/final")
    assert result.attempts == 3


@pytest.mark.asyncio
async def test_fetch_decodes_gb18030_html(allow_public_host) -> None:
    html = "<html><head><title>招聘规范</title></head><body>中文正文</body></html>"
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers={"content-type": "text/html; charset=gb18030"},
            content=html.encode("gb18030"),
        )
    )
    result = await fetch_public_source(
        RemoteFetchRequest(url="https://example.com/page"),
        transport=transport,
    )
    assert result.detected_title == "招聘规范"
    assert "中文正文" in result.content.decode()


@pytest.mark.asyncio
async def test_private_address_has_clear_security_error() -> None:
    with pytest.raises(RemoteSourceError) as captured:
        await fetch_public_source(RemoteFetchRequest(url="http://127.0.0.1/private"))
    assert captured.value.code == "REMOTE_PRIVATE_ADDRESS_BLOCKED"
    assert "内网" in captured.value.message


@pytest.mark.asyncio
async def test_api_html_response_explains_type_mismatch(allow_public_host) -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><body>login</body></html>",
        )
    )
    with pytest.raises(RemoteSourceError) as captured:
        await fetch_public_source(
            RemoteFetchRequest(url="https://example.com/api", source_type="api"),
            transport=transport,
        )
    assert captured.value.code == "REMOTE_API_NOT_JSON"
    assert "自动识别" in (captured.value.suggestion or "")


@pytest.mark.asyncio
async def test_content_length_limit_stops_download_early(allow_public_host) -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers={"content-type": "text/plain", "content-length": "2048"},
            content=b"small",
        )
    )
    with pytest.raises(RemoteSourceError) as captured:
        await fetch_public_source(
            RemoteFetchRequest(url="https://example.com/large", max_bytes=100),
            transport=transport,
        )
    assert captured.value.code == "REMOTE_CONTENT_TOO_LARGE"
    assert captured.value.http_status == 413


def test_sensitive_headers_and_query_tokens_are_rejected() -> None:
    with pytest.raises(RemoteSourceError) as header_error:
        validate_remote_request(
            RemoteFetchRequest(
                url="https://example.com/data",
                headers={"Authorization": "Bearer secret"},
            )
        )
    assert header_error.value.code == "REMOTE_SENSITIVE_HEADER_FORBIDDEN"

    with pytest.raises(RemoteSourceError) as parameter_error:
        validate_remote_request(
            RemoteFetchRequest(
                url="https://example.com/data",
                query_params={"access_token": "secret"},
            )
        )
    assert parameter_error.value.code == "REMOTE_SENSITIVE_PARAMETER_FORBIDDEN"

    with pytest.raises(RemoteSourceError) as url_parameter_error:
        validate_remote_request(RemoteFetchRequest(url="https://example.com/data?api_key=secret"))
    assert url_parameter_error.value.code == "REMOTE_SENSITIVE_PARAMETER_FORBIDDEN"
