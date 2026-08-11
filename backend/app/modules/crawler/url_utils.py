"""URL 规范化与去重工具。

对应 SubTask 19.3：将不同形式的 URL 规范化为唯一形式，作为去重键。

为什么需要 URL 规范化？
-----------------------
同一页面在 Web 上可能以多种 URL 形式出现，但实际指向相同内容：
    - 大小写差异：``HTTP://Example.com/Path`` 与 ``http://example.com/Path``
      （scheme 与 netloc 大小写不敏感）
    - 跟踪参数：``?utm_source=google&utm_medium=cpc`` 与 ``?fbclid=xxx``
      这些参数仅用于广告追踪，不影响页面内容
    - 参数顺序：``?a=1&b=2`` 与 ``?b=2&a=1`` 等价
    - fragment：``#section1`` 与 ``#section2`` 指向同一页面的不同位置
    - 尾部斜杠：``/path`` 与 ``/path/`` 在多数服务器上等价
    - 默认端口：``http://example.com:80/`` 与 ``http://example.com/`` 等价

若不做规范化，同一页面会被识别为多个 URL，造成重复抓取与重复入库。
规范化后对 URL 计算 SHA-256 哈希，作为 ``crawl_pages.canonical_url_hash`` 去重键，
复合唯一索引 ``(project_id, crawl_source_id, canonical_url_hash)`` 保证幂等。

去重流程
--------
1. 抓取前：``normalize_url`` → ``compute_url_hash`` → 查 ``get_by_canonical_hash``
2. 不存在：新建 CrawlPage，记录 canonical_url_hash
3. 已存在且 content_hash 未变：跳过（duplicate_count++）
4. 已存在但 content_hash 变化：增量更新（创建新版本 Document）
"""
from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# ---------------------------------------------------------------------------
# 跟踪参数黑名单：这些参数仅用于广告/分析追踪，不影响页面内容
# 移除后可大幅减少同一页面的 URL 变体
# ---------------------------------------------------------------------------
# utm_*：Google Analytics 跟踪参数（utm_source/utm_medium/utm_campaign 等）
# fbclid：Facebook 点击 ID
# gclid：Google Ads 点击 ID
# mc_cid / mc_eid：Mailchimp 跟踪参数
# yclid：Yandex 点击 ID
# msclkid：Microsoft Advertising 点击 ID
_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAM_EXACT = frozenset({
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "yclid",
    "msclkid",
    "ref",  # 部分站点用作来源追踪
})

# 默认端口：移除以减少 URL 变体
_DEFAULT_PORTS = {
    "http": 80,
    "https": 443,
}


def _is_tracking_param(name: str) -> bool:
    """判断参数名是否为跟踪参数（需移除）。

    Args:
        name: 参数名（原始大小写）。

    Returns:
        True 表示是跟踪参数；False 表示保留。
    """
    name_lower = name.lower()
    # 精确匹配（fbclid、gclid 等）
    if name_lower in _TRACKING_PARAM_EXACT:
        return True
    # 前缀匹配（utm_*）
    for prefix in _TRACKING_PARAM_PREFIXES:
        if name_lower.startswith(prefix):
            return True
    return False


def normalize_url(url: str) -> str:
    """将 URL 规范化为唯一形式，作为去重键。

    规范化步骤（按顺序执行）：
        1. **大小写**：scheme 与 netloc（含域名）转小写，path/query/fragment 保留原大小写
           （部分服务器 path 大小写敏感，不能强制小写）
        2. **移除 fragment**：``#section`` 不影响页面内容，移除
        3. **移除跟踪参数**：``utm_*`` / ``fbclid`` / ``gclid`` 等，不影响内容
        4. **排序 query 参数**：``?a=1&b=2`` 与 ``?b=2&a=1`` 等价，排序后统一
        5. **移除默认端口**：``http://x:80/`` → ``http://x/``，``https://x:443/`` → ``https://x/``
        6. **移除尾部斜杠**：``/path/`` → ``/path``，根路径 ``/`` 保留

    特殊处理
    --------
    - **根路径保留**：``http://example.com/`` 不会变成 ``http://example.com``，
      因为空 path 与根 path 语义可能不同（部分服务器对空 path 返回 400）。
    - **空 URL**：返回空字符串，调用方据此跳过。
    - **解析失败**：返回原始 URL（降级处理，避免异常中断采集）。

    Args:
        url: 待规范化的 URL 字符串。

    Returns:
        规范化后的 URL 字符串。若 URL 为空或解析失败，返回空字符串或原始 URL。

    Examples:
        >>> normalize_url("HTTP://Example.com:80/Path/?utm_source=x&a=1&b=2#frag")
        'http://example.com/Path?a=1&b=2'
        >>> normalize_url("https://example.com/")
        'https://example.com/'
        >>> normalize_url("https://example.com/path/")
        'https://example.com/path'
    """
    if not url:
        return ""

    # 使用 urlsplit 解析（比 urlparse 更快，不解析 params）
    try:
        parsed = urlsplit(url)
    except Exception:
        # 解析异常：降级返回原始 URL，避免中断采集
        return url

    # 步骤 1：scheme 与 netloc 转小写
    scheme = (parsed.scheme or "").lower()
    netloc = (parsed.netloc or "").lower()

    # 步骤 5（提前处理）：移除默认端口
    # netloc 形如 "user:pass@host:port"，仅移除 host 后的默认端口
    if "@" in netloc:
        # 含用户信息：分割 user@host:port
        userinfo, _, hostport = netloc.rpartition("@")
        netloc = userinfo + "@" + _strip_default_port(hostport, scheme)
    else:
        netloc = _strip_default_port(netloc, scheme)

    # 步骤 2：移除 fragment（直接不写入新 URL）
    # 步骤 3 & 4：移除跟踪参数 + 排序 query
    query = _normalize_query(parsed.query)

    # 步骤 6：移除尾部斜杠（根路径除外）
    path = parsed.path or ""
    if len(path) > 1 and path.endswith("/"):
        # 非根路径且以 / 结尾：移除尾部斜杠
        # 根路径 "/" 保留（len == 1）
        path = path.rstrip("/")

    # 重新拼接为完整 URL（不含 fragment）
    return urlunsplit((scheme, netloc, path, query, ""))


def _strip_default_port(hostport: str, scheme: str) -> str:
    """移除 netloc 中的默认端口（:80、:443）。

    Args:
        hostport: 形如 ``host:port`` 的字符串，可能不含端口。
        scheme: 小写协议名，用于判断默认端口。

    Returns:
        移除默认端口后的 hostport 字符串。若端口非默认或无端口，原样返回。
    """
    if ":" not in hostport:
        # 无端口
        return hostport
    # IPv6 地址形如 [::1]:80，需特殊处理
    if hostport.startswith("["):
        # IPv6：[addr]:port
        bracket_end = hostport.find("]")
        if bracket_end == -1:
            return hostport
        host = hostport[: bracket_end + 1]
        port_part = hostport[bracket_end + 1 :]
        if port_part.startswith(":"):
            port_str = port_part[1:]
            if _is_default_port(port_str, scheme):
                return host
        return hostport

    # 普通 IPv4/域名：host:port
    host, _, port_str = hostport.rpartition(":")
    if _is_default_port(port_str, scheme):
        return host
    return hostport


def _is_default_port(port_str: str, scheme: str) -> bool:
    """判断端口字符串是否为指定协议的默认端口。

    Args:
        port_str: 端口字符串，如 ``"80"``。
        scheme: 协议名（小写），如 ``"http"``。

    Returns:
        True 表示是默认端口；False 表示非默认或无法判断。
    """
    try:
        port = int(port_str)
    except ValueError:
        return False
    return _DEFAULT_PORTS.get(scheme) == port


def _normalize_query(query: str) -> str:
    """规范化 query 字符串：移除跟踪参数 + 排序。

    流程：
        1. ``parse_qsl`` 解析为 (key, value) 列表（保留重复参数）
        2. 过滤掉跟踪参数（utm_*、fbclid 等）
        3. 按 key 排序（key 相同时按 value 排序）
        4. ``urlencode`` 重新编码（``doseq=True``，``quote_via=quote_plus`` 默认）

    Args:
        query: 原始 query 字符串（不含 ?），如 ``"b=2&a=1&utm_source=x"``。

    Returns:
        规范化后的 query 字符串。无有效参数时返回空字符串。
    """
    if not query:
        return ""

    # parse_qsl 解析为 (key, value) 列表，keep_blank_values=True 保留空值参数
    pairs = parse_qsl(query, keep_blank_values=True)

    # 过滤跟踪参数
    filtered = [(k, v) for k, v in pairs if not _is_tracking_param(k)]

    # 按 key 排序，key 相同时按 value 排序
    filtered.sort()

    # 重新编码为 query 字符串
    return urlencode(filtered, doseq=True)


def compute_url_hash(url: str) -> str:
    """对规范化后的 URL 计算 SHA-256 哈希，作为去重键。

    为什么先规范化再哈希？
        直接对原始 URL 哈希会因大小写/参数顺序/跟踪参数差异产生不同哈希，
        无法识别"同一页面的不同 URL 形式"。先规范化统一形式，再哈希，
        保证同一页面的所有 URL 变体产生相同哈希，作为稳定的去重键。

    为什么用 SHA-256 而非 MD5？
        - 安全性：SHA-256 抗碰撞性更强，避免攻击者构造相同哈希的不同 URL
          绕过去重（虽概率极低，但安全优先）。
        - 长度：64 字符十六进制，与 ``crawl_pages.canonical_url_hash`` 列定义
          ``Char(64)`` 一致。

    Args:
        url: 原始 URL 字符串。

    Returns:
        规范化后 URL 的 SHA-256 十六进制摘要（64 字符）。空 URL 返回空字符串。

    Examples:
        >>> h1 = compute_url_hash("HTTP://Example.com:80/Path?a=1&b=2")
        >>> h2 = compute_url_hash("http://example.com/Path?b=2&a=1")
        >>> h1 == h2
        True
    """
    if not url:
        return ""
    # 先规范化，再计算 SHA-256
    canonical = normalize_url(url)
    if not canonical:
        return ""
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_content_hash(content: str) -> str:
    """对正文内容计算 SHA-256 哈希，用于正文去重与增量更新检测。

    用途
    ----
    1. **正文去重**：同一 URL 多次抓取时，若 content_hash 相同，跳过入库。
    2. **增量更新检测**：URL 相同但 content_hash 变化时，创建新版本 Document，
       保留历史版本以支持回溯。

    规范化处理
    ----------
    计算前对正文做轻量规范化，避免无关空白差异导致哈希变化：
        - 去除首尾空白
        - 多个连续空白（含换行）替换为单个空格

    Args:
        content: 正文文本字符串。

    Returns:
        规范化后正文的 SHA-256 十六进制摘要（64 字符）。空正文返回空字符串。

    Examples:
        >>> h1 = compute_content_hash("hello  world")
        >>> h2 = compute_content_hash("hello world")
        >>> h1 == h2
        True
    """
    if not content:
        return ""
    # 轻量规范化：去除首尾空白 + 合并连续空白
    import re

    normalized = re.sub(r"\s+", " ", content.strip())
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
