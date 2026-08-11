"""HTML 清洗与正文提取模块。

对应 SubTask 19.4：从抓取到的不可信 HTML 中清洗危险标签/属性，
并提取干净正文用于入库。

为什么页面内容是不可信数据？
----------------------------
采集的网页来自外部站点，可能包含：
1. **XSS 攻击载荷**：``<script>``、``<iframe>``、``on*`` 事件属性、``javascript:``
   协议链接。若直接存储并展示给用户，会触发存储型 XSS。
2. **跟踪脚本**：第三方分析脚本、广告脚本，影响内容纯净度。
3. **样式污染**：``<style>`` 标签可能包含 CSS 注入（如 ``expression()``、
   ``@import`` 外部样式表），影响渲染安全。
4. **页面噪音**：导航、广告、侧边栏、页脚等非正文内容，干扰向量化与检索召回。

清洗策略
--------
1. **移除危险标签**：``<script>`` / ``<iframe>`` / ``<object>`` / ``<embed>``
   / ``<noscript>``，连同标签内容一起删除（避免脚本执行）。
2. **移除事件属性**：所有 ``on*`` 属性（onclick、onload、onerror 等），
   防止事件触发执行恶意代码。
3. **移除危险协议**：``href="javascript:..."`` / ``src="javascript:..."``，
   替换为 ``#`` 中和。
4. **移除 ``<style>`` 标签**：默认移除（避免 CSS 注入与样式干扰）。
5. **提取纯文本**：复用 Trafilatura 或 BeautifulSoup，去除导航/广告等噪音，
   仅保留主内容区正文。
"""
from __future__ import annotations

import logging
import re

import trafilatura
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 危险标签黑名单：这些标签连同内容一起移除
# - script：JavaScript 脚本，XSS 主要载体
# - iframe：内嵌框架，可加载任意页面（点击劫持、钓鱼）
# - object / embed：插件嵌入（Flash、Java），已废弃且高风险
# - noscript：无脚本时的替代内容，可能含恶意 HTML
# - style：CSS 样式，可能含 expression() / @import 注入
# ---------------------------------------------------------------------------
_DANGEROUS_TAGS = frozenset({
    "script",
    "iframe",
    "object",
    "embed",
    "noscript",
    "style",
})

# ---------------------------------------------------------------------------
# 事件属性正则：匹配所有 on* 属性（onclick、onload、onerror 等）
# 形如 onclick="..."、onload='...'、onerror=foo
# 使用 \b 确保只匹配属性名开头，避免误伤 "icon" 等普通属性
# ---------------------------------------------------------------------------
# 匹配 on + 字母 + 等号 + 可选引号 + 值
# 不直接用正则替换属性（HTML 结构复杂，正则易误伤），
# 改用 BeautifulSoup 遍历所有标签的属性，删除 on* 开头的属性
# 这里保留正则作为文档说明，实际清洗用 BeautifulSoup 遍历

# 危险协议前缀：javascript:、vbscript:、data:（部分浏览器可执行 data:text/html）
_DANGEROUS_PROTOCOLS = ("javascript:", "vbscript:", "data:text/html")


def sanitize_html(html: str, keep_style: bool = False) -> str:
    """清洗 HTML，移除危险标签与属性，返回安全的 HTML 字符串。

    清洗规则（按顺序执行）：
        1. **移除危险标签**：``<script>`` / ``<iframe>`` / ``<object>`` /
           ``<embed>`` / ``<noscript>`` / ``<style>``（可选保留）
        2. **移除事件属性**：所有 ``on*`` 属性（onclick、onload 等）
        3. **中和危险协议**：``href="javascript:..."`` → ``href="#"``
        4. **返回清洗后的 HTML 字符串**

    为什么用 BeautifulSoup 而非正则？
        HTML 结构复杂（嵌套、属性顺序、引号变体），正则难以可靠匹配所有情况，
        且易误伤合法内容。BeautifulSoup 解析为 DOM 树后逐节点处理，
        能可靠识别标签与属性，避免遗漏与误伤。

    Args:
        html: 原始 HTML 字符串（不可信数据）。
        keep_style: 是否保留 ``<style>`` 标签。默认 False（移除），
            避免 CSS 注入与样式干扰。设为 True 时保留 ``<style>``，
            适用于需要保留原页面样式的场景（如快照存档）。

    Returns:
        清洗后的 HTML 字符串。若输入为空或解析失败，返回空字符串。

    Examples:
        >>> sanitize_html('<script>alert(1)</script><p onclick="x()">hello</p>')
        '<p>hello</p>'
        >>> sanitize_html('<a href="javascript:alert(1)">x</a>')
        '<a href="#">x</a>'
    """
    if not html:
        return ""

    try:
        # 解析 HTML 为 DOM 树，使用 html.parser（内置解析器，无需额外依赖）
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:
        # 解析异常：记录日志并返回空字符串（保守处理，避免异常中断采集）
        logger.warning("HTML 解析失败：%s", exc)
        return ""

    # 步骤 1：移除危险标签（连同内容）
    tags_to_remove = list(_DANGEROUS_TAGS)
    if keep_style:
        # 保留 <style>：从移除列表中排除
        tags_to_remove = [t for t in tags_to_remove if t != "style"]

    for tag_name in tags_to_remove:
        # find_all 找到所有匹配标签，extract() 连同内容移除
        for tag in soup.find_all(tag_name):
            tag.decompose()  # decompose 彻底移除标签及其内容

    # 步骤 2 & 3：遍历所有标签，移除 on* 事件属性 + 中和危险协议
    for tag in soup.find_all(True):  # True 匹配所有标签
        # 复制 attrs 字典避免修改时迭代报错
        attrs = dict(tag.attrs)
        for attr_name, attr_value in attrs.items():
            # 移除 on* 事件属性（onclick、onload、onerror 等）
            if attr_name.lower().startswith("on"):
                del tag.attrs[attr_name]
                continue

            # 中和 href/src 属性中的危险协议
            if attr_name.lower() in ("href", "src"):
                _neutralize_dangerous_protocol(tag, attr_name, attr_value)

    # 返回清洗后的 HTML 字符串
    return str(soup)


def _neutralize_dangerous_protocol(tag, attr_name: str, attr_value) -> None:
    """中和标签属性中的危险协议（javascript:、vbscript: 等）。

    若属性值以危险协议开头，替换为 ``#`` 中和，避免触发脚本执行。
    多值属性（如 class）跳过，仅处理单值属性。

    Args:
        tag: BeautifulSoup 标签对象。
        attr_name: 属性名（href 或 src）。
        attr_value: 属性值（可能是字符串或多值列表）。
    """
    # 多值属性（如 class）跳过
    if isinstance(attr_value, list):
        return

    value_lower = str(attr_value).strip().lower()
    for protocol in _DANGEROUS_PROTOCOLS:
        if value_lower.startswith(protocol):
            # 命中危险协议：替换为 # 中和
            tag.attrs[attr_name] = "#"
            return


def extract_text(html: str) -> str:
    """从 HTML 提取纯文本正文，去除导航/广告等噪音。

    提取优先级：
        1. **Trafilatura**（优先）：专业的网页正文提取库，自动识别主内容区，
           去除导航、广告、侧边栏、页脚等噪音。保留段落、列表、引用等语义结构。
        2. **BeautifulSoup 降级**：Trafilatura 失败时，提取所有 ``<p>`` 标签文本拼接。
        3. **空字符串兜底**：所有方法均失败时返回空字符串。

    为什么优先 Trafilatura？
        Trafilatura 是专业的网页正文提取库，相比 BeautifulSoup 手写规则：
        1. 自动识别主内容区，去除导航/广告/侧边栏等噪音。
        2. 对各种页面结构鲁棒（新闻、博客、文档、论文）。
        3. 保留语义结构（段落、列表、引用），便于向量化与检索。
        4. 纯 Python 实现，无外部服务依赖。

    Args:
        html: 原始 HTML 字符串（建议先经 ``sanitize_html`` 清洗，但本函数也可直接处理）。

    Returns:
        提取的正文文本字符串。提取失败时返回空字符串。

    Examples:
        >>> text = extract_text('<html><body><p>hello</p></body></html>')
        >>> 'hello' in text
        True
    """
    if not html:
        return ""

    # 步骤 1：用 Trafilatura 提取正文
    try:
        # include_links=False：去除链接（避免链接文本污染正文）
        # include_tables=False：去除表格（表格内容噪音较多，且向量检索不友好）
        # include_images=False：去除图片
        # favor_precision=True：精度优先（宁可少提取也不要噪音）
        content = trafilatura.extract(
            html,
            include_links=False,
            include_tables=False,
            include_images=False,
            favor_precision=True,
        )
        if content:
            return content.strip()
    except Exception as exc:
        # Trafilatura 异常：记录日志，尝试降级
        logger.warning("Trafilatura 正文提取异常：%s", exc)

    # 步骤 2：BeautifulSoup 降级，提取所有 <p> 标签文本拼接
    try:
        soup = BeautifulSoup(html, "html.parser")
        # 提取所有 <p> 标签，去除空白，过滤空段落
        paragraphs = soup.find_all("p")
        text = "\n".join(
            p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)
        )
        if text:
            return text
    except Exception as exc:
        logger.warning("BeautifulSoup 降级提取失败：%s", exc)

    # 步骤 3：兜底返回空字符串
    return ""


def extract_title(html: str) -> str:
    """从 HTML 提取页面标题。

    提取优先级：
        1. ``<title>`` 标签（最常见）
        2. ``<meta property="og:title">``（社交分享标题，部分站点使用）
        3. ``<h1>`` 标签（页面主标题）
        4. 空字符串兜底

    Args:
        html: 原始 HTML 字符串。

    Returns:
        页面标题字符串。提取失败返回空字符串。
    """
    if not html:
        return ""

    try:
        soup = BeautifulSoup(html, "html.parser")

        # 优先级 1：<title> 标签
        title_tag = soup.find("title")
        if title_tag and title_tag.get_text(strip=True):
            return title_tag.get_text(strip=True)

        # 优先级 2：og:title meta
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            return og_title["content"].strip()

        # 优先级 3：<h1> 标签
        h1_tag = soup.find("h1")
        if h1_tag and h1_tag.get_text(strip=True):
            return h1_tag.get_text(strip=True)
    except Exception as exc:
        logger.warning("标题提取异常：%s", exc)

    return ""
