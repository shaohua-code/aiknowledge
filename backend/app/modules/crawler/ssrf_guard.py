"""SSRF 防护模块：URL 安全校验、重定向校验、内网 IP 判定。

对应 SubTask 19.2：防止服务器端请求伪造（Server-Side Request Forgery）攻击。

什么是 SSRF？为什么要防护？
----------------------------
SSRF 是一种攻击手法，攻击者让服务端去访问本应由攻击者无法直接访问的内网资源：
1. 读取云元数据服务（如 AWS ``http://169.254.169.254/latest/meta-data/``）
   获取临时凭证、IAM 角色，进而横向渗透。
2. 访问内网管理后台、Redis、数据库等内部服务，绕过外网防火墙。
3. 探测内网拓扑（端口扫描、IP 存活性）。
4. 利用 ``file://``、``gopher://`` 等协议读取本地文件或发起其他协议请求。

防护策略（多层次纵深防御）
--------------------------
1. **协议白名单**：仅允许 ``http`` / ``https``，拒绝 ``file://`` / ``ftp://`` /
   ``data:`` / ``gopher://`` 等危险协议。
2. **主机名黑名单**：拒绝 ``localhost`` / ``127.0.0.1`` / ``0.0.0.0`` /
   ``::1`` 等环回地址。
3. **内网 IP 黑名单**：拒绝 RFC 1918 私网地址（10.x、172.16-31.x、192.168.x）、
   链路本地（169.254.x）、环回（127.x）。
4. **云元数据黑名单**：明确拒绝
   - AWS/GCP：``169.254.169.254``
   - 阿里云：``100.100.100.200``
   - GCP 内部域名：``metadata.google.internal``
5. **DNS 解析二次校验**：域名解析得到的 IP 必须再次通过内网检查，
   防止 DNS rebinding 攻击（攻击者控制 DNS 让首次解析返回公网、后续解析返回内网）。
6. **域名白名单**：若 ``allowed_domains`` 非空，仅允许列表内域名通过，
   限制采集范围在可信站点内。

为什么不只靠协议+黑名单？
    DNS rebinding 攻击可在解析后变更 IP，必须在 DNS 解析后再校验 IP，
    且解析后立即用于请求（避免解析与请求之间 IP 被切换）。
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from app.core.exceptions import CrawlUrlNotAllowedError

# ---------------------------------------------------------------------------
# 协议白名单：仅允许 http/https
# 其他协议（file/ftp/data/gopher等）一律拒绝，避免协议层 SSRF
# ---------------------------------------------------------------------------
_ALLOWED_SCHEMES = frozenset({"http", "https"})

# ---------------------------------------------------------------------------
# 主机名黑名单：环回域名与特殊域名
# ---------------------------------------------------------------------------
# localhost：本机域名，解析到 127.0.0.1 或 ::1
# metadata.google.internal：GCP 元数据服务内部域名
_HOSTNAME_BLACKLIST = frozenset({
    "localhost",
    "metadata.google.internal",
})

# ---------------------------------------------------------------------------
# 云元数据 IP 黑名单
# 这些 IP 是云厂商提供的元数据服务，可被攻击者用来获取实例凭证
# ---------------------------------------------------------------------------
# AWS / GCP / Azure 云元数据服务地址：169.254.169.254（链路本地）
_CLOUD_METADATA_AWS = "169.254.169.254"
# 阿里云元数据服务地址：100.100.100.200
_CLOUD_METADATA_ALIYUN = "100.100.100.200"
# 云元数据 IP 黑名单集合
_CLOUD_METADATA_IPS = frozenset({
    _CLOUD_METADATA_AWS,
    _CLOUD_METADATA_ALIYUN,
})


def is_private_ip(ip: str) -> bool:
    """判断 IP 是否为内网/环回/链路本地/保留地址。

    判定依据（按 RFC 标准）：
        - ``private``：RFC 1918 私网地址
            - 10.0.0.0/8（A 类私网）
            - 172.16.0.0/12（B 类私网，172.16.x ~ 172.31.x）
            - 192.168.0.0/16（C 类私网）
        - ``loopback``：环回地址 127.0.0.0/8（IPv4）、::1/128（IPv6）
        - ``link_local``：链路本地 169.254.0.0/16（IPv4）、fe80::/10（IPv6）
        - ``reserved``：保留地址（240.0.0.0/4 等）
        - ``unspecified``：0.0.0.0、::

    为什么这么判？
        Python 标准库 ``ipaddress`` 模块提供了 ``is_private`` / ``is_loopback`` /
        ``is_link_local`` / ``is_reserved`` / ``is_unspecified`` 等属性，
        覆盖了所有需要拒绝的特殊地址段。``is_private`` 在 Python 3.11+ 已包含
        loopback 与 link_local，但为兼容旧版本与显式可读，分别判断后取或。

    Args:
        ip: IP 地址字符串（IPv4 或 IPv6），如 ``"10.0.0.1"``、``"127.0.0.1"``。

    Returns:
        True 表示 IP 属于内网/环回/链路本地/保留地址，必须拒绝；
        False 表示公网 IP，允许访问。
        无法解析的 IP 字符串返回 True（保守拒绝，避免异常 IP 绕过校验）。

    Examples:
        >>> is_private_ip("10.0.0.1")
        True
        >>> is_private_ip("192.168.1.1")
        True
        >>> is_private_ip("8.8.8.8")
        False
        >>> is_private_ip("169.254.169.254")
        True
    """
    if not ip:
        # 空 IP：保守拒绝
        return True
    try:
        # 解析为 IPv4/IPv6 地址对象
        addr = ipaddress.ip_address(ip)
    except ValueError:
        # 无法解析为合法 IP：保守拒绝（避免畸形 IP 绕过校验）
        return True

    # 任一特殊属性命中即视为不可访问
    # is_private：RFC 1918 私网地址
    # is_loopback：环回地址（127.x、::1）
    # is_link_local：链路本地（169.254.x、fe80::）
    # is_reserved：保留地址
    # is_unspecified：0.0.0.0、::
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_unspecified
    )


def _resolve_hostname(hostname: str) -> list[str]:
    """解析主机名为 IP 列表（DNS 查询）。

    使用 ``socket.getaddrinfo`` 进行 DNS 查询，返回所有 A/AAAA 记录。
    返回所有记录的原因：
        DNS 可能返回多个 IP（CDN、负载均衡），攻击者可能在多个 IP 中混入内网 IP，
        必须校验所有解析结果，任一命中内网即拒绝。

    Args:
        hostname: 主机名，如 ``"example.com"``。

    Returns:
        IP 地址字符串列表（已去重）。DNS 解析失败时返回空列表。
    """
    try:
        # getaddrinfo 返回 (family, type, proto, canonname, sockaddr) 元组列表
        # sockaddr 对于 IPv4 是 (ip, port)，IPv6 是 (ip, port, flowinfo, scope_id)
        results = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        # DNS 解析失败：返回空列表，调用方据此拒绝
        return []

    # 提取 IP 并去重（保持顺序）
    ips: list[str] = []
    seen: set[str] = set()
    for result in results:
        sockaddr = result[4]
        if not sockaddr:
            continue
        ip = sockaddr[0]
        if ip and ip not in seen:
            seen.add(ip)
            ips.append(ip)
    return ips


def _check_hostname(hostname: str, allowed_domains: list[str] | None) -> None:
    """校验主机名是否在黑名单或白名单中。

    校验顺序：
        1. 主机名非空校验
        2. 黑名单校验（localhost、metadata.google.internal 等）
        3. 域名白名单校验（若 allowed_domains 非空，仅允许列表内域名）

    白名单匹配规则：
        - 条目以 ``.`` 开头（如 ``.example.com``）：匹配所有子域名
          （``sub.example.com``、``a.b.example.com`` 均匹配），也匹配根域名 ``example.com``
        - 否则精确匹配（``example.com`` 仅匹配 ``example.com``）
        - 大小写不敏感

    Args:
        hostname: 主机名（小写）。
        allowed_domains: 域名白名单，None 或空表示不限制。

    Raises:
        CrawlUrlNotAllowedError: 主机名在黑名单中，或不在白名单中。
    """
    if not hostname:
        # 主机名为空：URL 格式异常，拒绝
        raise CrawlUrlNotAllowedError(
            "URL 主机名为空",
            details={"reason": "EMPTY_HOSTNAME"},
        )

    # 黑名单校验：localhost、metadata.google.internal 等
    if hostname in _HOSTNAME_BLACKLIST:
        raise CrawlUrlNotAllowedError(
            f"URL 主机名 {hostname} 在黑名单中（禁止访问本地/元数据服务）",
            details={"reason": "HOSTNAME_BLACKLISTED", "hostname": hostname},
        )

    # 白名单校验：仅当 allowed_domains 非空时启用
    if allowed_domains:
        # 检查是否在白名单中
        if not _match_allowed_domain(hostname, allowed_domains):
            raise CrawlUrlNotAllowedError(
                f"URL 域名 {hostname} 不在采集源允许域名列表中",
                details={
                    "reason": "DOMAIN_NOT_ALLOWED",
                    "hostname": hostname,
                    "allowed_domains": list(allowed_domains),
                },
            )


def _match_allowed_domain(hostname: str, allowed_domains: list[str]) -> bool:
    """判断主机名是否匹配域名白名单。

    匹配规则：
        - 条目以 ``.`` 开头（如 ``.example.com``）：
          匹配根域名 ``example.com`` 及所有子域名 ``sub.example.com``。
        - 否则精确匹配（``example.com`` 仅匹配 ``example.com``）。
        - 大小写不敏感（统一小写比较）。

    Args:
        hostname: 待匹配的主机名（已转小写）。
        allowed_domains: 域名白名单。

    Returns:
        True 表示匹配到白名单；False 表示未匹配。
    """
    hostname_lower = hostname.lower()
    for entry in allowed_domains:
        if not entry:
            continue
        entry_lower = entry.lower()
        if entry_lower.startswith("."):
            # 通配子域名：.example.com 匹配 example.com 及所有子域名
            root = entry_lower[1:]
            if not root:
                continue
            # hostname == root 或 hostname 以 .root 结尾（子域名）
            if hostname_lower == root or hostname_lower.endswith("." + root):
                return True
        else:
            # 精确匹配
            if hostname_lower == entry_lower:
                return True
    return False


def _check_ip(ip: str) -> None:
    """校验 IP 是否为云元数据地址或内网 IP。

    校验顺序：
        1. 云元数据 IP 黑名单（169.254.169.254、100.100.100.200）
        2. 内网/环回/链路本地 IP（is_private_ip 综合判断）

    Args:
        ip: IP 地址字符串。

    Raises:
        CrawlUrlNotAllowedError: IP 命中云元数据或内网黑名单。
    """
    # 云元数据 IP 黑名单校验
    if ip in _CLOUD_METADATA_IPS:
        raise CrawlUrlNotAllowedError(
            f"URL 解析到云元数据服务 IP {ip}（禁止访问元数据服务）",
            details={"reason": "CLOUD_METADATA_IP", "ip": ip},
        )

    # 内网/环回/链路本地 IP 校验
    if is_private_ip(ip):
        raise CrawlUrlNotAllowedError(
            f"URL 解析到内网/环回/链路本地 IP {ip}（禁止访问内网）",
            details={"reason": "PRIVATE_IP", "ip": ip},
        )


def validate_url(url: str, allowed_domains: list[str] | None = None) -> None:
    """校验 URL 是否安全可访问（SSRF 防护主入口）。

    校验流程（六步纵深防御）：
        1. **解析 URL**：``urlparse`` 拆分为 scheme/netloc/path/query/fragment
        2. **协议白名单**：仅允许 ``http`` / ``https``，拒绝 ``file://`` / ``ftp://`` /
           ``data:`` 等危险协议
        3. **主机名黑名单**：拒绝 ``localhost`` / ``metadata.google.internal``
        4. **域名白名单**：若 ``allowed_domains`` 非空，校验域名在列表中
        5. **直接 IP 校验**：若主机名本身是 IP 字面量（如 ``http://10.0.0.1/``），
           直接校验 IP 不在黑名单
        6. **DNS 解析 + IP 校验**：对主机名做 DNS 解析，对每个解析结果 IP 再次校验，
           防止 DNS rebinding 攻击

    DNS Rebinding 攻击与防护
    ------------------------
    攻击场景：攻击者控制的域名首次 DNS 解析返回公网 IP（通过校验），
    后续解析返回内网 IP（实际请求时被解析到内网）。
    防护：对所有解析结果 IP 都校验，任一命中内网即拒绝。
    局限：本函数校验与实际请求之间存在时间窗口，理论上仍可能被 rebinding。
    生产级防护应在 ``httpx`` 的 transport 层接管 DNS 解析，校验后立即复用 IP。
    本实现作为基础防护，覆盖绝大多数场景。

    Args:
        url: 待校验的 URL 字符串。
        allowed_domains: 域名白名单，None 或空表示不限制（仅校验协议与 IP）。

    Raises:
        CrawlUrlNotAllowedError: URL 不通过任一校验。details 中 ``reason`` 字段
            标明具体原因（SCHEME_NOT_ALLOWED / HOSTNAME_BLACKLISTED /
            DOMAIN_NOT_ALLOWED / PRIVATE_IP / CLOUD_METADATA_IP / DNS_FAILED 等）。

    Examples:
        >>> validate_url("https://example.com/article/1")
        # 通过
        >>> validate_url("http://127.0.0.1/admin")
        # 抛 CrawlUrlNotAllowedError（PRIVATE_IP）
        >>> validate_url("file:///etc/passwd")
        # 抛 CrawlUrlNotAllowedError（SCHEME_NOT_ALLOWED）
        >>> validate_url("http://evil.com", allowed_domains=["example.com"])
        # 抛 CrawlUrlNotAllowedError（DOMAIN_NOT_ALLOWED）
    """
    # ------------------------------------------------------------------
    # 步骤 1：解析 URL
    # ------------------------------------------------------------------
    try:
        parsed = urlparse(url)
    except Exception as exc:
        # URL 解析异常：拒绝
        raise CrawlUrlNotAllowedError(
            f"URL 解析失败: {exc}",
            details={"reason": "URL_PARSE_FAILED", "url": url},
        ) from exc

    # ------------------------------------------------------------------
    # 步骤 2：协议白名单校验
    # ------------------------------------------------------------------
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        # 非 http/https 协议：拒绝（file/ftp/data/gopher 等均不允许）
        raise CrawlUrlNotAllowedError(
            f"URL 协议 {scheme!r} 不被允许（仅允许 http/https）",
            details={"reason": "SCHEME_NOT_ALLOWED", "scheme": scheme, "url": url},
        )

    # ------------------------------------------------------------------
    # 步骤 3 & 4：主机名黑名单 + 域名白名单校验
    # ------------------------------------------------------------------
    # hostname 属性已去除端口与用户信息，仅保留主机名
    hostname = (parsed.hostname or "").lower()
    _check_hostname(hostname, allowed_domains)

    # ------------------------------------------------------------------
    # 步骤 5：直接 IP 校验（若主机名本身是 IP 字面量）
    # ------------------------------------------------------------------
    # 尝试解析 hostname 为 IP，若成功则直接校验（跳过 DNS）
    # 这一步覆盖 http://10.0.0.1/admin 这种直接用 IP 的场景
    try:
        ip_obj = ipaddress.ip_address(hostname)
        # 是合法 IP 字面量：校验
        _check_ip(str(ip_obj))
        # IP 字面量校验通过，无需 DNS 解析
        return
    except ValueError:
        # 不是 IP 字面量，是域名：继续 DNS 解析校验
        pass

    # ------------------------------------------------------------------
    # 步骤 6：DNS 解析 + IP 校验（防 DNS rebinding）
    # ------------------------------------------------------------------
    ips = _resolve_hostname(hostname)
    if not ips:
        # DNS 解析失败或无结果：拒绝（无法判定 IP 安全性）
        raise CrawlUrlNotAllowedError(
            f"URL 主机名 {hostname} DNS 解析失败",
            details={"reason": "DNS_FAILED", "hostname": hostname},
        )

    # 对所有解析结果 IP 校验，任一命中内网即拒绝
    # 攻击者可能在多个 A 记录中混入内网 IP，必须全量校验
    for ip in ips:
        _check_ip(ip)


def validate_redirect(url: str, allowed_domains: list[str] | None = None) -> None:
    """重定向后重新校验 URL（复用 validate_url 全部校验逻辑）。

    为什么要对重定向 URL 重新校验？
        攻击者可能配置一个看似合法的 URL（如 ``https://example.com/redirect``），
        服务端返回 302 跳转到 ``http://127.0.0.1/admin`` 或 ``http://169.254.169.254/``。
        若不校验重定向目标，等于绕过了 SSRF 防护。
        因此每次重定向后必须重新执行完整的 SSRF 校验。

    实现说明：
        本函数直接复用 ``validate_url`` 的全部校验逻辑。
        ``CrawlerService`` 在使用 ``httpx`` 时应关闭自动重定向（``follow_redirects=False``），
        手动处理重定向：每收到 3xx 响应，取 ``Location`` 头调用本函数校验，
        校验通过后再发起下一次请求，确保每一跳都经过 SSRF 校验。

    Args:
        url: 重定向目标 URL。
        allowed_domains: 域名白名单，None 或空表示不限制。

    Raises:
        CrawlUrlNotAllowedError: 重定向 URL 不通过校验。
    """
    # 复用 validate_url 的全部校验逻辑
    validate_url(url, allowed_domains=allowed_domains)
