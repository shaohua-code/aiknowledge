"""联网研究模块：Web 搜索、网页提取、可信域名过滤。

对应 Task 12：实现联网搜索与网页提取模块，为研究链路提供
"联网搜索"环节的完整能力，包括：
1. ``WebSearchProvider``：联网搜索 Provider 抽象（Serper / DuckDuckGo）
2. ``WebPageExtractor``：网页正文提取（Trafilatura + 降级机制）
3. ``WebResearchService``：编排搜索 → 过滤 → 提取的完整流程
4. ``domain_filter``：基于白名单/黑名单的域名过滤工具

模块导出
--------
- ``WebResearchService``：联网搜索服务，研究链路统一入口
- ``WebEvidence``：联网证据数据结构
- ``WebResearchResult``：联网研究结果数据结构
- ``WebPageExtractor``：网页提取器（可单独使用）
- ``WebPageContent``：网页提取结果数据结构
"""
from __future__ import annotations

from app.modules.web_research.extractor import WebPageContent, WebPageExtractor
from app.modules.web_research.service import (
    WebEvidence,
    WebResearchResult,
    WebResearchService,
)

__all__ = [
    "WebEvidence",
    "WebPageContent",
    "WebPageExtractor",
    "WebResearchResult",
    "WebResearchService",
]
