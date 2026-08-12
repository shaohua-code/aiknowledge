from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from urllib.parse import urlsplit

import httpx

from knowledge_core.shared.errors import ConflictError, ProviderUnavailableError

MAX_REMOTE_BYTES = 20 * 1024 * 1024


async def _assert_public_host(hostname: str) -> None:
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(
            hostname,
            None,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ConflictError("数据源域名无法解析") from exc
    if not addresses:
        raise ConflictError("数据源域名没有可用地址")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ConflictError("数据源地址指向内网、回环或保留网络，已拒绝访问")


async def fetch_public_source(url: str, source_type: str) -> tuple[bytes, str, str]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConflictError("数据源 URL 必须是有效的 HTTP(S) 地址")
    if parsed.username or parsed.password:
        raise ConflictError("数据源 URL 不能包含用户名或密码")
    await _assert_public_host(parsed.hostname)

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.get(url, headers={"User-Agent": "AIKnowledgeBot/2.0"})
    except httpx.HTTPError as exc:
        raise ProviderUnavailableError("远程数据源暂时不可访问") from exc
    if 300 <= response.status_code < 400:
        raise ConflictError("远程数据源发生重定向；请提交最终公开 URL")
    if response.status_code >= 400:
        raise ProviderUnavailableError(
            "远程数据源返回错误",
            details={"statusCode": response.status_code},
        )
    content = response.content
    if len(content) > MAX_REMOTE_BYTES:
        raise ConflictError("远程数据源内容不能超过 20MB")

    content_type = response.headers.get("content-type", "text/plain").split(";", 1)[0]
    if source_type == "api" or "json" in content_type:
        try:
            parsed_json = response.json()
        except ValueError as exc:
            raise ConflictError("API 数据源没有返回有效 JSON") from exc
        content = json.dumps(parsed_json, ensure_ascii=False, indent=2).encode()
        content_type = "application/json"
    elif not (content_type.startswith("text/") or content_type == "application/xml"):
        raise ConflictError("网页数据源当前仅支持文本、HTML、XML 或 JSON 响应")
    return content, content_type, ".txt"
