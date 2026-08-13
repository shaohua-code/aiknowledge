from __future__ import annotations

import asyncio
import codecs
import ipaddress
import json
import re
import socket
import ssl
from dataclasses import dataclass, field
from email.message import Message
from hashlib import sha256
from html import unescape
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

import httpx

from knowledge_core.config import settings
from knowledge_core.shared.errors import CoreError

REDIRECT_CODES = {301, 302, 303, 307, 308}
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
BLOCKED_REQUEST_HEADERS = {
    "authorization",
    "cookie",
    "host",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-real-ip",
}
SENSITIVE_PARAMETER_MARKERS = {
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "signature",
}
SUPPORTED_TEXT_TYPES = {
    "application/json",
    "application/ld+json",
    "application/xml",
    "application/rss+xml",
    "application/atom+xml",
    "application/x-ndjson",
    "application/csv",
    "text/csv",
    "text/html",
    "text/markdown",
    "text/plain",
    "text/xml",
}


class RemoteSourceError(CoreError):
    """可直接返回给控制台和入库运行记录的远程数据源错误。"""

    def __init__(
        self,
        code: str,
        title: str,
        message: str,
        *,
        http_status: int = 422,
        retryable: bool = False,
        suggestion: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, suggestion=suggestion, details=details)
        self.code = code
        self.title = title
        self.http_status = http_status
        self.retryable = retryable


@dataclass(slots=True)
class RemoteFetchRequest:
    url: str
    source_type: str = "auto"
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    query_params: dict[str, str] = field(default_factory=dict)
    json_body: Any = None
    json_path: str | None = None
    timeout_seconds: int = settings.remote_fetch_timeout_seconds
    max_bytes: int = settings.remote_fetch_max_bytes
    max_redirects: int = settings.remote_fetch_max_redirects
    retry_count: int = settings.remote_fetch_retries


@dataclass(slots=True)
class RemoteFetchResult:
    content: bytes
    mime_type: str
    extension: str
    final_url: str
    status_code: int
    size_bytes: int
    content_hash: str
    detected_title: str | None
    attempts: int


def validate_remote_request(request: RemoteFetchRequest) -> None:
    parsed = urlsplit(request.url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RemoteSourceError(
            "REMOTE_URL_INVALID",
            "数据源地址无效",
            "请输入以 http:// 或 https:// 开头的完整公开地址。",
            suggestion="例如：https://example.com/article 或 https://api.example.com/data",
        )
    if parsed.username or parsed.password:
        raise RemoteSourceError(
            "REMOTE_URL_CREDENTIALS_FORBIDDEN",
            "URL 中不能包含账号密码",
            "检测到 URL 内嵌了用户名或密码，为避免凭证泄漏已拒绝请求。",
            suggestion="请删除 URL 中的账号密码；当前公开数据源不保存认证凭证。",
        )
    if request.method not in {"GET", "POST"}:
        raise RemoteSourceError(
            "REMOTE_METHOD_UNSUPPORTED",
            "请求方式不支持",
            f"当前仅支持 GET 和 POST，收到的是 {request.method}。",
        )
    if request.method == "GET" and request.json_body is not None:
        raise RemoteSourceError(
            "REMOTE_GET_BODY_FORBIDDEN",
            "GET 请求不能携带 JSON 请求体",
            "请改用 POST，或者把参数填写到查询参数中。",
        )
    if request.source_type not in {"auto", "web", "api", "feed", "text"}:
        raise RemoteSourceError(
            "REMOTE_SOURCE_TYPE_UNSUPPORTED",
            "数据源类型不支持",
            f"无法识别数据源类型：{request.source_type}。",
        )
    for name, value in request.headers.items():
        normalized_name = name.strip().lower()
        if normalized_name in BLOCKED_REQUEST_HEADERS or normalized_name.startswith("proxy-"):
            raise RemoteSourceError(
                "REMOTE_SENSITIVE_HEADER_FORBIDDEN",
                "请求头包含敏感凭证",
                f"请求头 {name} 可能包含身份凭证，当前公开数据源禁止保存或发送该请求头。",
                suggestion="请改用无需登录的公开 API，或后续接入专用加密凭证能力。",
            )
        if not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,80}", name):
            raise RemoteSourceError(
                "REMOTE_HEADER_INVALID",
                "请求头名称无效",
                f"请求头名称 {name!r} 不符合 HTTP 规范。",
            )
        if "\r" in value or "\n" in value or len(value) > 2000:
            raise RemoteSourceError(
                "REMOTE_HEADER_INVALID",
                "请求头内容无效",
                f"请求头 {name} 包含换行符或内容过长。",
            )
    query_parameter_names = [name for name, _ in parse_qsl(parsed.query, keep_blank_values=True)]
    query_parameter_names.extend(request.query_params)
    for name in query_parameter_names:
        normalized_name = name.strip().lower()
        if normalized_name in SENSITIVE_PARAMETER_MARKERS or any(
            marker in normalized_name for marker in {"password", "secret", "token"}
        ):
            raise RemoteSourceError(
                "REMOTE_SENSITIVE_PARAMETER_FORBIDDEN",
                "查询参数可能包含访问凭证",
                f"参数 {name} 看起来像密码、Token 或签名，当前公开数据源不会保存该内容。",
                suggestion="请使用无需认证的公开地址；需要私有 API 时应接入后续的加密凭证方案。",
            )


async def _assert_public_host(hostname: str) -> None:
    try:
        direct_ip = ipaddress.ip_address(hostname.strip("[]"))
        addresses = [direct_ip]
    except ValueError:
        try:
            resolved = await asyncio.get_running_loop().getaddrinfo(
                hostname,
                None,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise RemoteSourceError(
                "REMOTE_DNS_FAILED",
                "数据源域名无法解析",
                f"无法找到域名 {hostname} 对应的服务器地址。",
                http_status=502,
                retryable=True,
                suggestion="检查域名拼写和 DNS 状态，确认网站可以从公网访问后重试。",
                details={"host": hostname},
            ) from exc
        addresses = [ipaddress.ip_address(item[4][0]) for item in resolved]
    if not addresses:
        raise RemoteSourceError(
            "REMOTE_DNS_EMPTY",
            "数据源域名没有可用地址",
            f"域名 {hostname} 没有返回 IPv4 或 IPv6 地址。",
            http_status=502,
            retryable=True,
        )
    if any(not address.is_global for address in addresses):
        raise RemoteSourceError(
            "REMOTE_PRIVATE_ADDRESS_BLOCKED",
            "数据源指向非公网地址",
            "该地址指向内网、回环、链路本地或保留网络，平台已按安全策略阻止访问。",
            suggestion=(
                "请提供可从公网直接访问的地址；localhost、局域网 IP 和云元数据地址均不允许。"
            ),
            details={"host": hostname},
        )


def _safe_url_for_error(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _content_type(response: httpx.Response, content: bytes) -> str:
    raw = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if raw in {"application/octet-stream", "binary/octet-stream"} or not raw:
        sample = content.lstrip()[:80].lower()
        if sample.startswith((b"<!doctype html", b"<html")):
            return "text/html"
        if sample.startswith((b"<?xml", b"<rss", b"<feed")):
            return "application/xml"
        if sample.startswith((b"{", b"[")):
            return "application/json"
        if b"," in sample or b"\t" in sample:
            return "text/csv"
        if b"\x00" not in sample:
            return "text/plain"
    return raw


def _header_charset(content_type_header: str) -> str | None:
    message = Message()
    message["content-type"] = content_type_header
    return message.get_content_charset()


def _decode_text(content: bytes, content_type_header: str) -> str:
    if content.startswith(codecs.BOM_UTF8):
        return content.decode("utf-8-sig")
    if content.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return content.decode("utf-16")
    declared = _header_charset(content_type_header)
    html_head = content[:4096].decode("ascii", errors="ignore")
    meta = re.search(r"charset\s*=\s*[\"']?([A-Za-z0-9._-]+)", html_head, re.IGNORECASE)
    candidates = [declared, meta.group(1) if meta else None, "utf-8", "gb18030", "big5", "cp1252"]
    attempted: set[str] = set()
    for encoding in candidates:
        if not encoding:
            continue
        normalized = encoding.lower()
        if normalized in attempted:
            continue
        attempted.add(normalized)
        try:
            return content.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("utf-8", errors="replace")


def _json_path_tokens(path: str) -> list[str | int]:
    value = path.strip()
    if value in {"", "$"}:
        return []
    value = value.removeprefix("$").removeprefix(".")
    tokens: list[str | int] = []
    for key, index in re.findall(r"(?:^|\.)([^.\[\]]+)|\[(\d+)\]", value):
        tokens.append(int(index) if index else key)
    reconstructed = "".join(
        f"[{token}]" if isinstance(token, int) else ("." if tokens[:i] else "") + token
        for i, token in enumerate(tokens)
    )
    if not tokens or reconstructed.replace(".", "").replace("[", "").replace(
        "]", ""
    ) != value.replace(".", "").replace("[", "").replace("]", ""):
        raise RemoteSourceError(
            "REMOTE_JSON_PATH_INVALID",
            "JSON 数据路径格式不正确",
            f"无法解析 JSON 路径：{path}。",
            suggestion="使用 data.items、$.data.items 或 results[0] 这样的简单路径。",
        )
    return tokens


def _extract_json_path(payload: Any, path: str | None) -> Any:
    if not path:
        return payload
    current = payload
    for token in _json_path_tokens(path):
        try:
            current = current[token]
        except (KeyError, IndexError, TypeError) as exc:
            raise RemoteSourceError(
                "REMOTE_JSON_PATH_NOT_FOUND",
                "JSON 中找不到指定数据",
                f"响应 JSON 中不存在路径 {path}。",
                suggestion="先使用“测试连接”查看响应预览，再修改数据路径。",
                details={"jsonPath": path},
            ) from exc
    return current


def _normalize_content(
    content: bytes,
    response: httpx.Response,
    request: RemoteFetchRequest,
) -> tuple[bytes, str, str, str | None]:
    if not content.strip():
        raise RemoteSourceError(
            "REMOTE_CONTENT_EMPTY",
            "数据源返回了空内容",
            "服务器请求成功，但响应正文为空。",
            suggestion="检查 URL、查询参数和请求方式，确认接口确实返回数据。",
        )
    media_type = _content_type(response, content)
    header = response.headers.get("content-type", media_type)
    is_json = media_type.endswith("+json") or media_type in {
        "application/json",
        "application/ld+json",
        "application/x-ndjson",
    }
    if request.source_type == "api" and not is_json:
        raise RemoteSourceError(
            "REMOTE_API_NOT_JSON",
            "API 没有返回 JSON",
            f"当前响应类型是 {media_type or '未知类型'}，但数据源被设置为 JSON API。",
            suggestion=(
                "确认接口地址、请求方式和参数；如果它返回网页、XML 或文本，请把类型改为“自动识别”。"
            ),
            details={"contentType": media_type},
        )
    if is_json:
        text = _decode_text(content, header)
        if media_type == "application/x-ndjson":
            try:
                payload = [json.loads(line) for line in text.splitlines() if line.strip()]
            except json.JSONDecodeError as exc:
                raise RemoteSourceError(
                    "REMOTE_NDJSON_INVALID",
                    "NDJSON 数据格式不正确",
                    f"第 {exc.lineno} 行附近不是有效 JSON。",
                    details={"line": exc.lineno},
                ) from exc
        else:
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise RemoteSourceError(
                    "REMOTE_JSON_INVALID",
                    "API 返回的 JSON 无法解析",
                    f"JSON 在第 {exc.lineno} 行、第 {exc.colno} 列附近格式错误。",
                    suggestion="检查接口是否返回了登录页、网关错误页或不完整 JSON。",
                    details={"line": exc.lineno, "column": exc.colno},
                ) from exc
        extracted = _extract_json_path(payload, request.json_path)
        if isinstance(extracted, str):
            normalized = extracted.strip().encode("utf-8")
            return normalized, "text/plain", ".txt", None
        normalized = json.dumps(extracted, ensure_ascii=False, indent=2).encode("utf-8")
        return normalized, "application/json", ".json", None
    if request.json_path:
        raise RemoteSourceError(
            "REMOTE_JSON_PATH_REQUIRES_JSON",
            "当前响应不能使用 JSON 数据路径",
            f"响应内容类型是 {media_type or '未知类型'}。",
            suggestion="清空 JSON 数据路径，或确认接口返回 application/json。",
        )
    if media_type not in SUPPORTED_TEXT_TYPES and not media_type.startswith("text/"):
        raise RemoteSourceError(
            "REMOTE_CONTENT_TYPE_UNSUPPORTED",
            "远程内容类型暂不支持",
            f"服务器返回 {media_type or '未知二进制类型'}，平台不会把它当作文本知识处理。",
            http_status=415,
            suggestion=(
                "请使用 HTML、JSON、XML/RSS、CSV、Markdown 或纯文本地址；PDF/DOCX 请使用文件上传。"
            ),
            details={"contentType": media_type},
        )
    text = _decode_text(content, header).strip()
    if not text:
        raise RemoteSourceError(
            "REMOTE_TEXT_DECODE_EMPTY",
            "远程内容解码后为空",
            "响应存在字节数据，但使用常见编码解码后没有有效文本。",
            suggestion="确认数据不是压缩包、图片或其他二进制文件。",
        )
    detected_title = None
    if media_type == "text/html":
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
        if title_match:
            detected_title = re.sub(r"\s+", " ", unescape(title_match.group(1))).strip()[:240]
        if (
            re.search(r"<script[^>]+(?:src=|type=[\"']module)", text, re.IGNORECASE)
            and len(re.sub(r"<[^>]+>", "", text).strip()) < 80
        ):
            raise RemoteSourceError(
                "REMOTE_JS_RENDER_REQUIRED",
                "网页主要内容需要 JavaScript 渲染",
                "服务器只返回了网页框架，没有返回可直接提取的正文。",
                suggestion=(
                    "优先接入网站公开 API、RSS 或服务端渲染页面；当前安全抓取器不执行网页脚本。"
                ),
            )
        return text.encode("utf-8"), "text/html", ".html", detected_title
    if media_type in {"application/xml", "application/rss+xml", "application/atom+xml", "text/xml"}:
        return text.encode("utf-8"), "application/xml", ".xml", detected_title
    if media_type in {"text/csv", "application/csv"}:
        return text.encode("utf-8"), "text/csv", ".csv", detected_title
    if media_type == "text/markdown":
        return text.encode("utf-8"), "text/markdown", ".md", detected_title
    return text.encode("utf-8"), "text/plain", ".txt", detected_title


def _status_error(response: httpx.Response) -> RemoteSourceError:
    status = response.status_code
    safe_url = _safe_url_for_error(str(response.url))
    details = {"statusCode": status, "url": safe_url}
    if status in {401, 403}:
        return RemoteSourceError(
            "REMOTE_ACCESS_DENIED",
            "远程数据源拒绝访问",
            f"目标服务器返回 HTTP {status}，该地址可能需要登录、API Key 或允许名单。",
            http_status=422,
            suggestion=(
                "请使用无需登录的公开地址；当前版本不会保存 Authorization、Cookie 或 API Key。"
            ),
            details=details,
        )
    if status == 404:
        return RemoteSourceError(
            "REMOTE_NOT_FOUND",
            "远程数据不存在",
            "目标服务器返回 HTTP 404，请检查 URL 路径是否正确。",
            details=details,
        )
    if status == 429:
        return RemoteSourceError(
            "REMOTE_RATE_LIMITED",
            "远程服务器限制了抓取频率",
            "目标服务器返回 HTTP 429，短时间内不允许继续请求。",
            http_status=503,
            retryable=True,
            suggestion="稍后重试，或降低该数据源的同步频率。",
            details=details,
        )
    if status >= 500:
        return RemoteSourceError(
            "REMOTE_SERVER_ERROR",
            "远程服务器发生错误",
            f"目标服务器返回 HTTP {status}。",
            http_status=503,
            retryable=True,
            suggestion="平台已经自动重试；如果仍失败，请稍后再次提交。",
            details=details,
        )
    return RemoteSourceError(
        "REMOTE_HTTP_ERROR",
        "远程请求未成功",
        f"目标服务器返回 HTTP {status}。",
        details=details,
    )


async def fetch_public_source(
    request: RemoteFetchRequest,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> RemoteFetchResult:
    """安全抓取公开文本数据，并返回标准 UTF-8 内容。"""

    validate_remote_request(request)
    current_url = request.url.strip()
    method = request.method
    attempts = 0
    retries = 0
    redirects = 0
    headers = {
        "User-Agent": settings.remote_fetch_user_agent,
        "Accept": (
            "text/html,application/json,application/xml,text/xml,text/csv,"
            "text/plain,text/markdown,*/*;q=0.2"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        **request.headers,
    }
    timeout = httpx.Timeout(
        request.timeout_seconds,
        connect=min(10, request.timeout_seconds),
        pool=min(5, request.timeout_seconds),
    )
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        transport=transport,
    ) as client:
        while True:
            parsed = urlsplit(current_url)
            if not parsed.hostname:
                raise RemoteSourceError(
                    "REMOTE_REDIRECT_INVALID",
                    "重定向地址无效",
                    "目标服务器返回了无法解析的跳转地址。",
                )
            await _assert_public_host(parsed.hostname)
            attempts += 1
            try:
                async with client.stream(
                    method,
                    current_url,
                    headers=headers,
                    params=request.query_params if redirects == 0 else None,
                    json=request.json_body if method == "POST" else None,
                ) as response:
                    if response.status_code in REDIRECT_CODES:
                        location = response.headers.get("location")
                        if not location:
                            raise RemoteSourceError(
                                "REMOTE_REDIRECT_MISSING_LOCATION",
                                "服务器返回了无效重定向",
                                f"HTTP {response.status_code} 响应中没有 Location 地址。",
                            )
                        if redirects >= request.max_redirects:
                            raise RemoteSourceError(
                                "REMOTE_REDIRECT_LIMIT_EXCEEDED",
                                "网页重定向次数过多",
                                f"地址连续跳转超过 {request.max_redirects} 次，已停止抓取。",
                                suggestion="请直接填写浏览器最终打开的公开地址。",
                            )
                        next_url = urljoin(current_url, location)
                        next_parsed = urlsplit(next_url)
                        if parsed.scheme == "https" and next_parsed.scheme == "http":
                            raise RemoteSourceError(
                                "REMOTE_INSECURE_REDIRECT_BLOCKED",
                                "重定向降低了连接安全等级",
                                "目标地址从 HTTPS 跳转到 HTTP，平台已停止访问。",
                                suggestion="请使用最终的 HTTPS 地址。",
                            )
                        current_url = next_url
                        redirects += 1
                        if response.status_code == 303:
                            method = "GET"
                        continue
                    if response.status_code >= 400:
                        error = _status_error(response)
                        if (
                            response.status_code in RETRYABLE_STATUS_CODES
                            and retries < request.retry_count
                        ):
                            retries += 1
                            retry_after = response.headers.get("retry-after", "")
                            delay = (
                                min(float(retry_after), 5.0)
                                if retry_after.isdigit()
                                else min(0.4 * retries, 1.5)
                            )
                            await asyncio.sleep(delay)
                            continue
                        raise error
                    declared_length = response.headers.get("content-length")
                    if (
                        declared_length
                        and declared_length.isdigit()
                        and int(declared_length) > request.max_bytes
                    ):
                        raise RemoteSourceError(
                            "REMOTE_CONTENT_TOO_LARGE",
                            "远程内容超过大小限制",
                            (
                                "服务器声明内容大小为 "
                                f"{int(declared_length) / 1024 / 1024:.1f}MB，"
                                f"当前限制为 {request.max_bytes / 1024 / 1024:.0f}MB。"
                            ),
                            http_status=413,
                            suggestion="请缩小 API 返回范围、分页请求，或拆分成多个数据源。",
                            details={
                                "contentLength": int(declared_length),
                                "maxBytes": request.max_bytes,
                            },
                        )
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > request.max_bytes:
                            raise RemoteSourceError(
                                "REMOTE_CONTENT_TOO_LARGE",
                                "远程内容超过大小限制",
                                (
                                    f"下载内容超过 {request.max_bytes / 1024 / 1024:.0f}MB，"
                                    "平台已停止接收。"
                                ),
                                http_status=413,
                                suggestion="请使用 API 分页、筛选时间范围，或拆分数据源。",
                                details={"maxBytes": request.max_bytes},
                            )
                        chunks.append(chunk)
                    raw_content = b"".join(chunks)
                    normalized, mime_type, extension, title = _normalize_content(
                        raw_content, response, request
                    )
                    return RemoteFetchResult(
                        content=normalized,
                        mime_type=mime_type,
                        extension=extension,
                        final_url=str(response.url),
                        status_code=response.status_code,
                        size_bytes=len(normalized),
                        content_hash=sha256(normalized).hexdigest(),
                        detected_title=title,
                        attempts=attempts,
                    )
            except RemoteSourceError:
                raise
            except httpx.TimeoutException as exc:
                if retries < request.retry_count:
                    retries += 1
                    await asyncio.sleep(min(0.4 * retries, 1.5))
                    continue
                raise RemoteSourceError(
                    "REMOTE_TIMEOUT",
                    "远程数据源响应超时",
                    f"在 {request.timeout_seconds} 秒内没有完成请求。",
                    http_status=504,
                    retryable=True,
                    suggestion="稍后重试；如果 API 数据量很大，请增加筛选条件或分页。",
                    details={"url": _safe_url_for_error(current_url)},
                ) from exc
            except httpx.ConnectError as exc:
                cause = exc.__cause__
                if isinstance(cause, ssl.SSLError) or "certificate" in str(exc).lower():
                    raise RemoteSourceError(
                        "REMOTE_TLS_FAILED",
                        "HTTPS 证书校验失败",
                        "目标网站的 HTTPS 证书无效、过期或证书链不完整。",
                        http_status=502,
                        suggestion="请让网站管理员修复证书；平台不会跳过 HTTPS 安全校验。",
                    ) from exc
                if retries < request.retry_count:
                    retries += 1
                    await asyncio.sleep(min(0.4 * retries, 1.5))
                    continue
                raise RemoteSourceError(
                    "REMOTE_CONNECT_FAILED",
                    "无法连接远程服务器",
                    "域名可以解析，但无法建立网络连接。",
                    http_status=502,
                    retryable=True,
                    suggestion="检查网站是否在线、端口是否开放，稍后重试。",
                    details={"url": _safe_url_for_error(current_url)},
                ) from exc
            except httpx.HTTPError as exc:
                raise RemoteSourceError(
                    "REMOTE_PROTOCOL_ERROR",
                    "远程服务器响应异常",
                    "连接已建立，但服务器返回了不完整或不符合 HTTP 规范的响应。",
                    http_status=502,
                    retryable=True,
                    suggestion="稍后重试，或改用该网站提供的正式 API/RSS 地址。",
                ) from exc
