"""SSRF 防护测试（SubTask 25.4）。

对应 Task 25：验证 ``SSRFGuard`` 模块的 URL 安全校验逻辑。

测试设计要点
------------
1. **不依赖网络**
   所有测试通过 mock DNS 解析（``socket.getaddrinfo``）避免真实网络调用。
   公网域名测试通过 mock 返回公网 IP，内网域名测试通过 mock 返回内网 IP。

2. **覆盖所有 SSRF 攻击向量**
   - 环回地址：localhost / 127.0.0.1 / ::1
   - 内网地址：10.0.0.1 / 172.16.0.1 / 192.168.1.1
   - 云元数据：169.254.169.254 / 100.100.100.200
   - 非 HTTP/HTTPS 协议：file:// / ftp:// / data: / gopher://
   - DNS rebinding：域名解析到内网 IP
   - 域名白名单：不在 allowed_domains 中的域名被拒绝

3. **验证允许的场景**
   - 正常公网域名 https://example.com 通过
   - 域名白名单匹配（精确匹配与通配子域名）

测试场景对照
------------
- test_localhost_rejected：localhost / 127.0.0.1 / ::1 被拒绝
- test_private_ip_rejected：10.x / 172.16.x / 192.168.x 被拒绝
- test_cloud_metadata_rejected：169.254.169.254 / 100.100.100.200 被拒绝
- test_non_http_scheme_rejected：file:// / ftp:// / data: / gopher:// 被拒绝
- test_dns_rebinding_rejected：域名解析到内网 IP 被拒绝
- test_normal_public_domain_allowed：https://example.com 通过
- test_domain_whitelist_exact_match：精确匹配白名单通过
- test_domain_whitelist_wildcard_subdomain：通配子域名匹配通过
- test_domain_not_in_whitelist_rejected：不在白名单中被拒绝
- test_is_private_ip_helper：is_private_ip 辅助函数覆盖各类 IP
"""
from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from app.core.exceptions import CrawlUrlNotAllowedError
from app.modules.crawler.ssrf_guard import (
    is_private_ip,
    validate_redirect,
    validate_url,
)


# ============================================================================
# 辅助函数：mock DNS 解析返回指定 IP 列表
# ============================================================================
def _mock_getaddrinfo(ips: list[str]):
    """构造 mock 的 socket.getaddrinfo 返回值。

    用于让 ``_resolve_hostname`` 返回指定的 IP 列表，
    避免真实 DNS 查询，使测试可重复且不依赖网络。

    Args:
        ips: 要返回的 IP 地址列表。

    Returns:
        一个 mock 函数，模拟 socket.getaddrinfo 的返回结构。
    """

    def _fake_getaddrinfo(hostname, port, *args, **kwargs):
        """模拟 DNS 解析，返回固定 IP 列表。

        socket.getaddrinfo 返回 (family, type, proto, canonname, sockaddr) 元组列表
        sockaddr 对于 IPv4 是 (ip, port)，IPv6 是 (ip, port, flowinfo, scope_id)
        """
        results = []
        for ip in ips:
            # 判断 IPv4 还是 IPv6
            if ":" in ip:
                # IPv6 地址
                results.append((socket.AF_INET6, socket.SOCK_STREAM, 0, "", (ip, port, 0, 0)))
            else:
                # IPv4 地址
                results.append((socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, port)))
        return results

    return _fake_getaddrinfo


# ============================================================================
# 测试 1：localhost / 127.0.0.1 / ::1 被拒绝
# ============================================================================
@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "http://127.0.0.1/admin",
        "http://127.0.0.1:8080/admin",
        "http://[::1]/admin",
        "https://localhost:443/secret",
    ],
    ids=[
        "localhost",
        "127.0.0.1",
        "127.0.0.1_with_port",
        "ipv6_loopback",
        "localhost_https",
    ],
)
async def test_localhost_rejected(url: str):
    """环回地址（localhost / 127.0.0.1 / ::1）被拒绝。

    场景：
        - 攻击者试图让服务端访问本机管理接口
        - SSRF 防护应拒绝所有环回地址

    验证点：
        - 抛 CrawlUrlNotAllowedError
        - 错误码 CRAWL_URL_NOT_ALLOWED
        - details.reason 为 HOSTNAME_BLACKLISTED 或 PRIVATE_IP
    """
    with pytest.raises(CrawlUrlNotAllowedError) as exc_info:
        validate_url(url)

    assert exc_info.value.code == "CRAWL_URL_NOT_ALLOWED"
    assert exc_info.value.http_status == 403
    # reason 应标明是黑名单或内网 IP
    reason = exc_info.value.details.get("reason", "")
    assert reason in ("HOSTNAME_BLACKLISTED", "PRIVATE_IP"), (
        f"环回地址应被黑名单或内网 IP 校验拒绝，实际 reason：{reason}"
    )


# ============================================================================
# 测试 2：内网地址（10.x / 172.16.x / 192.168.x）被拒绝
# ============================================================================
@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.1/internal",
        "http://10.255.255.255/internal",
        "http://172.16.0.1/internal",
        "http://172.31.255.255/internal",
        "http://192.168.1.1/admin",
        "http://192.168.0.0/router",
    ],
    ids=[
        "10.0.0.1",
        "10.255.255.255",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        "192.168.0.0",
    ],
)
async def test_private_ip_rejected(url: str):
    """RFC 1918 私网地址被拒绝。

    场景：
        - 攻击者试图访问内网管理后台、数据库、Redis 等内部服务
        - SSRF 防护应拒绝所有 RFC 1918 私网地址

    验证点：
        - 抛 CrawlUrlNotAllowedError
        - details.reason 为 PRIVATE_IP
    """
    with pytest.raises(CrawlUrlNotAllowedError) as exc_info:
        validate_url(url)

    assert exc_info.value.code == "CRAWL_URL_NOT_ALLOWED"
    assert exc_info.value.details.get("reason") == "PRIVATE_IP"


# ============================================================================
# 测试 3：云元数据服务地址被拒绝
# ============================================================================
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.169.254/computeMetadata/v1/",
        "http://100.100.100.200/latest/meta-data/",
    ],
    ids=[
        "aws_metadata",
        "gcp_metadata",
        "aliyun_metadata",
    ],
)
async def test_cloud_metadata_rejected(url: str):
    """云元数据服务地址被拒绝。

    场景：
        - 攻击者试图读取 AWS / GCP / 阿里云的实例元数据
        - 元数据服务可能泄露临时凭证、IAM 角色等敏感信息
        - SSRF 防护应明确拒绝云元数据 IP

    验证点：
        - 抛 CrawlUrlNotAllowedError
        - details.reason 为 CLOUD_METADATA_IP 或 PRIVATE_IP
        - 169.254.169.254 同时是链路本地地址，会被 is_private_ip 拦截
    """
    with pytest.raises(CrawlUrlNotAllowedError) as exc_info:
        validate_url(url)

    assert exc_info.value.code == "CRAWL_URL_NOT_ALLOWED"
    reason = exc_info.value.details.get("reason", "")
    # 169.254.169.254 同时命中云元数据黑名单与链路本地地址
    assert reason in ("CLOUD_METADATA_IP", "PRIVATE_IP"), (
        f"云元数据 IP 应被拒绝，实际 reason：{reason}"
    )


# ============================================================================
# 测试 4：非 HTTP/HTTPS 协议被拒绝
# ============================================================================
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "data:text/html,<script>alert(1)</script>",
        "gopher://localhost:6379/_INFO",
        "dict://localhost:6379/INFO",
    ],
    ids=[
        "file_scheme",
        "ftp_scheme",
        "data_scheme",
        "gopher_scheme",
        "dict_scheme",
    ],
)
async def test_non_http_scheme_rejected(url: str):
    """非 HTTP/HTTPS 协议被拒绝。

    场景：
        - 攻击者试图用 file:// 读取本地文件
        - 攻击者试图用 gopher:// 发起 Redis 攻击
        - 攻击者试图用 data: 注入恶意内容
        - SSRF 防护应仅允许 http/https 协议

    验证点：
        - 抛 CrawlUrlNotAllowedError
        - details.reason 为 SCHEME_NOT_ALLOWED
    """
    with pytest.raises(CrawlUrlNotAllowedError) as exc_info:
        validate_url(url)

    assert exc_info.value.code == "CRAWL_URL_NOT_ALLOWED"
    assert exc_info.value.details.get("reason") == "SCHEME_NOT_ALLOWED"


# ============================================================================
# 测试 5：DNS 解析到内网 IP 的域名被拒绝（DNS rebinding 防护）
# ============================================================================
async def test_dns_rebinding_rejected():
    """域名 DNS 解析到内网 IP 时被拒绝。

    场景：
        - 攻击者控制的域名 ``evil.example.com`` DNS 解析返回内网 IP（如 10.0.0.1）
        - SSRF 防护在 DNS 解析后再次校验 IP，拒绝内网地址
        - 防止 DNS rebinding 攻击（首次解析公网，后续解析内网）

    验证点：
        - 抛 CrawlUrlNotAllowedError
        - details.reason 为 PRIVATE_IP
        - details.ip 为解析到的内网 IP
    """
    # mock DNS 解析返回内网 IP
    with patch(
        "app.modules.crawler.ssrf_guard.socket.getaddrinfo",
        side_effect=_mock_getaddrinfo(["10.0.0.1"]),
    ):
        with pytest.raises(CrawlUrlNotAllowedError) as exc_info:
            validate_url("http://evil.example.com/admin")

    assert exc_info.value.code == "CRAWL_URL_NOT_ALLOWED"
    assert exc_info.value.details.get("reason") == "PRIVATE_IP"
    assert exc_info.value.details.get("ip") == "10.0.0.1"


# ============================================================================
# 测试 6：DNS 解析结果中混入内网 IP 也被拒绝
# ============================================================================
async def test_dns_mixed_ips_rejected():
    """DNS 解析结果中混入内网 IP 时被拒绝。

    场景：
        - 域名 DNS 返回多个 A 记录，其中包含内网 IP
        - 攻击者可能在多 IP 中混入内网地址
        - SSRF 防护必须对所有解析结果 IP 校验，任一命中内网即拒绝

    验证点：
        - 抛 CrawlUrlNotAllowedError
        - details.reason 为 PRIVATE_IP 或 CLOUD_METADATA_IP
    """
    # mock DNS 解析返回公网 + 内网混合 IP
    with patch(
        "app.modules.crawler.ssrf_guard.socket.getaddrinfo",
        side_effect=_mock_getaddrinfo(["8.8.8.8", "10.0.0.1"]),
    ):
        with pytest.raises(CrawlUrlNotAllowedError) as exc_info:
            validate_url("http://mixed.example.com/")

    assert exc_info.value.code == "CRAWL_URL_NOT_ALLOWED"
    reason = exc_info.value.details.get("reason", "")
    assert reason in ("PRIVATE_IP", "CLOUD_METADATA_IP"), (
        f"混合 IP 中含内网应被拒绝，实际 reason：{reason}"
    )


# ============================================================================
# 测试 7：正常公网域名通过校验
# ============================================================================
async def test_normal_public_domain_allowed():
    """正常公网域名通过 SSRF 校验。

    场景：
        - 域名 ``example.com`` DNS 解析返回公网 IP（如 93.184.216.34）
        - SSRF 防护校验通过，不抛异常

    验证点：
        - validate_url 不抛异常
        - validate_redirect 同样通过
    """
    # mock DNS 解析返回公网 IP
    with patch(
        "app.modules.crawler.ssrf_guard.socket.getaddrinfo",
        side_effect=_mock_getaddrinfo(["93.184.216.34"]),
    ):
        # 不抛异常即通过
        validate_url("https://example.com/article/1")
        # 重定向校验也应通过
        validate_redirect("https://example.com/redirected")


# ============================================================================
# 测试 8：域名白名单 - 精确匹配通过
# ============================================================================
async def test_domain_whitelist_exact_match():
    """域名精确匹配白名单时通过。

    场景：
        - allowed_domains=["example.com"]
        - URL 域名为 example.com（精确匹配）
        - 校验通过

    验证点：
        - validate_url 不抛异常
    """
    with patch(
        "app.modules.crawler.ssrf_guard.socket.getaddrinfo",
        side_effect=_mock_getaddrinfo(["93.184.216.34"]),
    ):
        validate_url("https://example.com/path", allowed_domains=["example.com"])


# ============================================================================
# 测试 9：域名白名单 - 通配子域名匹配通过
# ============================================================================
async def test_domain_whitelist_wildcard_subdomain():
    """通配子域名白名单匹配通过。

    场景：
        - allowed_domains=[".example.com"]（以 . 开头表示通配子域名）
        - URL 域名为 sub.example.com（子域名）
        - 校验通过

    验证点：
        - validate_url 不抛异常
    """
    with patch(
        "app.modules.crawler.ssrf_guard.socket.getaddrinfo",
        side_effect=_mock_getaddrinfo(["93.184.216.34"]),
    ):
        # .example.com 匹配 sub.example.com
        validate_url(
            "https://sub.example.com/path",
            allowed_domains=[".example.com"],
        )
        # .example.com 也匹配根域名 example.com
        validate_url(
            "https://example.com/path",
            allowed_domains=[".example.com"],
        )
        # 多级子域名也应匹配
        validate_url(
            "https://a.b.example.com/path",
            allowed_domains=[".example.com"],
        )


# ============================================================================
# 测试 10：域名不在白名单中被拒绝
# ============================================================================
async def test_domain_not_in_whitelist_rejected():
    """域名不在白名单中被拒绝。

    场景：
        - allowed_domains=["example.com"]
        - URL 域名为 evil.com（不在白名单）
        - 抛 CrawlUrlNotAllowedError

    验证点：
        - 抛 CrawlUrlNotAllowedError
        - details.reason 为 DOMAIN_NOT_ALLOWED
    """
    with pytest.raises(CrawlUrlNotAllowedError) as exc_info:
        validate_url("https://evil.com/path", allowed_domains=["example.com"])

    assert exc_info.value.code == "CRAWL_URL_NOT_ALLOWED"
    assert exc_info.value.details.get("reason") == "DOMAIN_NOT_ALLOWED"
    assert exc_info.value.details.get("hostname") == "evil.com"


# ============================================================================
# 测试 11：is_private_ip 辅助函数覆盖各类 IP
# ============================================================================
@pytest.mark.parametrize(
    "ip,expected",
    [
        # 环回地址
        ("127.0.0.1", True),
        ("127.255.255.255", True),
        ("::1", True),
        # RFC 1918 私网地址
        ("10.0.0.1", True),
        ("10.255.255.255", True),
        ("172.16.0.1", True),
        ("172.31.255.255", True),
        ("192.168.1.1", True),
        ("192.168.0.0", True),
        # 链路本地
        ("169.254.169.254", True),
        ("169.254.0.1", True),
        # 保留地址
        ("240.0.0.1", True),
        # 未指定地址
        ("0.0.0.0", True),
        # 公网 IP（应通过）
        ("8.8.8.8", False),
        ("1.1.1.1", False),
        ("93.184.216.34", False),
        # 边界：172.15.x 不在私网范围（172.16-31 才是私网）
        ("172.15.0.1", False),
        ("172.32.0.1", False),
        # 空 IP 与非法 IP：保守拒绝
        ("", True),
        ("not-an-ip", True),
    ],
    ids=[
        "loopback_127.0.0.1",
        "loopback_127.255",
        "ipv6_loopback",
        "private_10.0.0.1",
        "private_10.255",
        "private_172.16",
        "private_172.31",
        "private_192.168.1.1",
        "private_192.168.0.0",
        "link_local_169.254.169.254",
        "link_local_169.254.0.1",
        "reserved_240",
        "unspecified_0.0.0.0",
        "public_8.8.8.8",
        "public_1.1.1.1",
        "public_93.184",
        "boundary_172.15_not_private",
        "boundary_172.32_not_private",
        "empty_ip_rejected",
        "invalid_ip_rejected",
    ],
)
async def test_is_private_ip_helper(ip: str, expected: bool):
    """is_private_ip 辅助函数覆盖各类 IP 地址。

    验证点：
        - 环回、私网、链路本地、保留、未指定地址返回 True
        - 公网 IP 返回 False
        - 边界 IP（172.15 / 172.32）正确识别为非私网
        - 空与非法 IP 保守返回 True（拒绝）
    """
    assert is_private_ip(ip) == expected, (
        f"IP {ip} 的 is_private_ip 判定应为 {expected}"
    )


# ============================================================================
# 测试 12：DNS 解析失败时拒绝
# ============================================================================
async def test_dns_resolution_failed_rejected():
    """DNS 解析失败时拒绝（保守策略）。

    场景：
        - 域名 DNS 解析抛 socket.gaierror（如域名不存在）
        - _resolve_hostname 返回空列表
        - validate_url 拒绝（无法判定 IP 安全性）

    验证点：
        - 抛 CrawlUrlNotAllowedError
        - details.reason 为 DNS_FAILED
    """
    # mock DNS 解析抛 gaierror
    with patch(
        "app.modules.crawler.ssrf_guard.socket.getaddrinfo",
        side_effect=socket.gaierror("DNS resolution failed"),
    ):
        with pytest.raises(CrawlUrlNotAllowedError) as exc_info:
            validate_url("http://nonexistent.invalid.example.com/")

    assert exc_info.value.code == "CRAWL_URL_NOT_ALLOWED"
    assert exc_info.value.details.get("reason") == "DNS_FAILED"


# ============================================================================
# 测试 13：重定向到内网地址被拒绝
# ============================================================================
async def test_redirect_to_internal_rejected():
    """重定向到内网地址被拒绝。

    场景：
        - 服务端请求 https://example.com/redirect
        - 响应 302 跳转到 http://10.0.0.1/admin
        - validate_redirect 校验重定向 URL，拒绝内网地址

    验证点：
        - 抛 CrawlUrlNotAllowedError
        - details.reason 为 PRIVATE_IP
    """
    with pytest.raises(CrawlUrlNotAllowedError) as exc_info:
        validate_redirect("http://10.0.0.1/admin")

    assert exc_info.value.code == "CRAWL_URL_NOT_ALLOWED"
    assert exc_info.value.details.get("reason") == "PRIVATE_IP"
