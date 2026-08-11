"""域名过滤工具：基于白名单/黑名单过滤搜索结果。

对应 SubTask 12.4：实现 ``is_domain_allowed`` 与 ``extract_domain``，
供 ``WebResearchService`` 在联网搜索后过滤结果。

过滤规则
--------
1. **先检查黑名单（block 列表）**：命中即拒绝，无论是否在白名单中。
   黑名单优先级最高，用于屏蔽已知不可信或与业务无关的域名
   （如内容农场、竞品站点、含错误信息的页面）。
2. **再检查白名单（allow 列表）**：
   - 白名单非空时，仅允许列表内域名通过（强约束模式）。
   - 白名单为空时，所有非黑名单域名均通过（开放模式）。
3. **域名匹配规则**：
   - 提取 URL 的 ``netloc``，去除端口与 ``www.`` 前缀，得到根域名。
   - 列表中的域名若以 ``.`` 开头（如 ``.example.com``），匹配所有子域名。
   - 否则精确匹配（``example.com`` 仅匹配 ``example.com``，不匹配 ``sub.example.com``）。

为什么要域名过滤？
------------------
1. **可信来源控制**：金融、医疗等领域对信息来源可信度要求高，
   白名单确保仅采信权威站点（如央行官网、证监会公告）。
2. **禁用来源屏蔽**：黑名单屏蔽已知含错误信息或低质量内容的站点，
   避免污染研究结论。
3. **合规要求**：某些行业禁止引用特定来源，需在数据层强制过滤。
"""
from __future__ import annotations

from urllib.parse import urlparse


def extract_domain(url: str) -> str:
    """从 URL 提取根域名。

    提取规则：
        1. 用 ``urlparse`` 解析 URL，取 ``netloc`` 部分
        2. 去除端口（如 ``example.com:8080`` → ``example.com``）
        3. 去除 ``www.`` 前缀（如 ``www.example.com`` → ``example.com``）
        4. 转小写，保证匹配大小写不敏感

    Args:
        url: 完整 URL，如 ``https://www.example.com/path?query=1``。

    Returns:
        根域名（小写），如 ``example.com``。
        URL 格式异常时返回空字符串（不抛异常，避免中断过滤流程）。

    Examples:
        >>> extract_domain("https://www.example.com/path")
        'example.com'
        >>> extract_domain("http://sub.example.com:8080/")
        'sub.example.com'
        >>> extract_domain("invalid-url")
        ''
    """
    if not url:
        return ""

    try:
        parsed = urlparse(url)
        # netloc 含用户信息、主机、端口，如 user:pass@host:port
        # hostname 属性已去除端口与用户信息，仅保留主机名
        hostname = parsed.hostname or ""
    except Exception:
        # URL 解析异常：返回空字符串，调用方据此丢弃此结果
        return ""

    if not hostname:
        return ""

    # 转小写，保证匹配大小写不敏感
    hostname = hostname.lower()
    # 去除 www. 前缀：www.example.com → example.com
    # 仅去除开头的 www.，保留其他子域名（如 sub.example.com）
    if hostname.startswith("www."):
        hostname = hostname[4:]

    return hostname


def is_domain_allowed(
    url: str,
    allowed_domains: list[str],
    blocked_domains: list[str],
) -> bool:
    """判断 URL 的域名是否被允许（通过白/黑名单过滤）。

    过滤顺序（关键逻辑）：
        1. **先检查黑名单**：命中即拒绝（``return False``）。
           黑名单优先级最高，即使域名同时在白名单中也拒绝。
           这确保禁用来源绝对不可通过，避免"白名单误加"导致污染。
        2. **再检查白名单**：
           - 白名单非空：仅允许列表内域名通过（``return 域名在白名单中``）。
           - 白名单为空：所有非黑名单域名均通过（``return True``）。
           白名单为空表示"未配置强约束"，采用开放模式，避免误屏蔽所有结果。

    域名匹配规则：
        - 列表中的域名以 ``.`` 开头（如 ``.example.com``）：
          匹配所有子域名（``sub.example.com``、``a.b.example.com`` 均匹配）。
        - 否则精确匹配（``example.com`` 仅匹配 ``example.com``，
          不匹配 ``sub.example.com``）。
        - 匹配大小写不敏感（已统一转小写）。

    Args:
        url: 待校验的 URL。
        allowed_domains: 允许域名白名单，空列表表示不限制（开放模式）。
        blocked_domains: 禁用域名黑名单，命中即拒绝。

    Returns:
        True 表示域名被允许；False 表示被拒绝（在黑名单中，或不在非空白名单中）。

    Examples:
        >>> is_domain_allowed("https://example.com", [], [])
        True
        >>> is_domain_allowed("https://example.com", [], ["example.com"])
        False
        >>> is_domain_allowed("https://example.com", ["example.com"], [])
        True
        >>> is_domain_allowed("https://other.com", ["example.com"], [])
        False
        >>> is_domain_allowed("https://sub.example.com", [".example.com"], [])
        True
    """
    # 提取根域名：失败则拒绝（无法判定来源的 URL 不应采信）
    domain = extract_domain(url)
    if not domain:
        return False

    # 步骤 1：检查黑名单（优先级最高，命中即拒绝）
    if _match_domain_list(domain, blocked_domains):
        return False

    # 步骤 2：检查白名单
    # 白名单为空：开放模式，所有非黑名单域名均通过
    if not allowed_domains:
        return True

    # 白名单非空：仅允许列表内域名通过
    return _match_domain_list(domain, allowed_domains)


def _match_domain_list(domain: str, domain_list: list[str]) -> bool:
    """判断域名是否匹配列表中任一条目。

    匹配规则（内部辅助函数）：
        - 条目以 ``.`` 开头（如 ``.example.com``）：
          匹配所有子域名（domain == "example.com" 或 domain 以 ".example.com" 结尾）。
          注意：``.example.com`` 也匹配 ``example.com`` 本身（根域名）。
        - 否则精确匹配（domain == 条目）。
        - 大小写不敏感（统一转小写比较）。

    Args:
        domain: 待匹配的根域名（已转小写）。
        domain_list: 域名列表，可能含 ``.`` 前缀的通配条目。

    Returns:
        True 表示匹配到任一条目；False 表示均未匹配。
    """
    if not domain_list:
        return False

    # 统一转小写，保证大小写不敏感
    domain_lower = domain.lower()

    for entry in domain_list:
        if not entry:
            # 空条目跳过
            continue
        entry_lower = entry.lower()

        if entry_lower.startswith("."):
            # 通配子域名：.example.com 匹配 example.com 及所有子域名
            # 去除前导 .，得到根域名 example.com
            root = entry_lower[1:]
            if not root:
                continue
            # domain == root（根域名本身）或 domain 以 .root 结尾（子域名）
            if domain_lower == root or domain_lower.endswith("." + root):
                return True
        else:
            # 精确匹配：example.com 仅匹配 example.com
            if domain_lower == entry_lower:
                return True

    return False
