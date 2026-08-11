"""证据合并与评分：将内部、网络、工具三类证据统一格式、去重、评分、截断。

对应 SubTask 15.2：``EvidenceMerger`` 负责在并行取证完成后，
将三类异构证据合并为统一列表，供大模型生成时引用。

设计理念（务必阅读）
--------------------
1. **统一格式**
   三类证据来源结构差异显著：
   - 内部证据：HybridSearcher 返回 chunk 字典（含 content / merged_score / document_id）
   - 网络证据：WebResearchService 返回 WebEvidence（含 title / url / snippet / content）
   - 工具证据：ToolExecutor 返回 data dict（结构由工具 output_schema 决定）
   合并前必须统一为相同 schema（type / title / snippet / source_url / published_at /
   data_as_of / score），便于大模型按统一格式引用与裁剪。

2. **去重**
   - 按 ``source_url`` 去重：同一网页可能被内部知识与联网搜索同时召回
   - 按 ``content`` 哈希去重：相同内容不同 URL（如镜像站、转载）需合并
   保留首次出现的版本（已按优先级排序），丢弃后续重复项

3. **截断**
   - snippet 超过 ``max_snippet_length``（默认 1500 字符）截断
   - 为什么 1500？大模型上下文有限，证据过多会稀释关键信息且增加 token 成本
     1500 字符约 500 token，8 条证据 ≈ 4000 token，留足空间给系统提示词与生成

4. **评分策略**（关键）
   - 内部证据：0.7~0.9（按检索分归一化）
     内部知识库经过审核入库，可信度最高；但不同片段相关性不同，按 RRF 分数归一化
   - 网络证据：0.5~0.7
     联网内容未审核、来源不可控，可信度低于内部知识；
     0.5~0.7 让模型在引用时保持审慎，结合内部证据交叉验证
   - 工具证据：0.8~0.95
     工具返回的是结构化实时数据（如基金净值、市场行情），数据本身可信度高；
     但工具可能配置错误或返回异常值，故略低于内部知识峰值

   **为什么内部证据优先级高于网络证据？**
   内部知识库是企业审核过的可信内容，且通过 RRF 混合检索保证相关性；
   网络证据虽然时效性好，但来源未知、内容可能未经核验。
   评分差异让模型在证据冲突时优先采信内部知识，网络证据作为补充与时效性增强。

5. **排序与截取**
   - 按 score 降序排序，高可信度证据在前
   - 截取 Top N（默认 8，``settings.max_evidence``）
     控制上下文规模，避免 token 超限与生成发散
"""
from __future__ import annotations

import hashlib
from typing import Any


class EvidenceMerger:
    """证据合并器：统一格式、去重、评分、截断。

    使用方式
    --------
    .. code-block:: python

        merger = EvidenceMerger()
        merged = merger.merge(
            internal_evidence=internal_dicts,
            web_evidence=web_dicts,
            tool_evidence=tool_dicts,
            max_evidence=8,
        )
        # merged 已按 score 降序，长度 ≤ 8

    合并流程
    --------
        1. 三类证据统一格式化
        2. 评分（按类型分配分数区间）
        3. 合并为单一列表
        4. 去重（source_url + content 哈希）
        5. snippet 截断
        6. 按 score 降序排序
        7. 截取 Top N
    """

    # 内部证据评分区间：内部知识库经审核，可信度最高
    INTERNAL_SCORE_MIN = 0.7
    INTERNAL_SCORE_MAX = 0.9
    # 网络证据评分区间：未审核来源，可信度中等
    WEB_SCORE_MIN = 0.5
    WEB_SCORE_MAX = 0.7
    # 工具证据评分区间：结构化实时数据，可信度高但可能配置错误
    TOOL_SCORE_MIN = 0.8
    TOOL_SCORE_MAX = 0.95

    def merge(
        self,
        internal_evidence: list[dict[str, Any]],
        web_evidence: list[dict[str, Any]],
        tool_evidence: list[dict[str, Any]],
        max_evidence: int = 8,
        max_snippet_length: int = 1500,
    ) -> list[dict[str, Any]]:
        """合并三类证据，返回统一格式、去重、评分、截断后的列表。

        Args:
            internal_evidence: 内部知识库证据列表，来自 HybridSearcher.search。
                每项含 ``content`` / ``merged_score`` / ``document_id`` 等字段。
            web_evidence: 联网搜索证据列表，来自 WebResearchService。
                每项含 ``title`` / ``url`` / ``snippet`` / ``content`` / ``published_at``。
            tool_evidence: 工具证据列表，来自 ToolExecutor。
                每项含 ``tool_code`` / ``data`` / ``title`` 等。
            max_evidence: 最大返回证据数，默认 8（对应 ``settings.max_evidence``）。
            max_snippet_length: snippet 最大字符数，默认 1500。超长截断。

        Returns:
            合并后的证据列表，每项字段：
            - ``type``：internal / web / tool
            - ``title``：证据标题
            - ``snippet``：证据摘要（已截断）
            - ``source_url``：来源 URL（内部证据为 None）
            - ``published_at``：发布时间（可空）
            - ``data_as_of``：数据截止时间（可空）
            - ``score``：评分（0~1），按 score 降序排序
        """
        # 步骤 1 & 2：分别格式化三类证据并评分
        normalized: list[dict[str, Any]] = []
        # 内部证据：评分按 RRF 分数归一化到 [0.7, 0.9]
        normalized.extend(self._normalize_internal(internal_evidence))
        # 网络证据：评分按出现顺序线性映射到 [0.7, 0.5]（首位最高，末位最低）
        normalized.extend(self._normalize_web(web_evidence))
        # 工具证据：评分按出现顺序线性映射到 [0.95, 0.8]
        normalized.extend(self._normalize_tool(tool_evidence))

        # 步骤 3：去重（source_url + content 哈希）
        deduped = self._dedup(normalized)

        # 步骤 4：snippet 截断
        for ev in deduped:
            snippet = ev.get("snippet") or ""
            if len(snippet) > max_snippet_length:
                # 截断并追加省略号，提示模型内容已裁剪
                ev["snippet"] = snippet[:max_snippet_length] + "..."

        # 步骤 5：按 score 降序排序，高可信度在前
        deduped.sort(key=lambda x: x.get("score", 0.0), reverse=True)

        # 步骤 6：截取 Top N
        return deduped[:max_evidence]

    def _normalize_internal(
        self, internal_evidence: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """格式化内部证据并评分。

        内部证据来自 HybridSearcher，结构为 chunk 字典，含：
        - ``content``：片段原文
        - ``merged_score``：RRF 合并分数
        - ``document_id``：所属文档
        - ``page_number``：页码
        - ``metadata``：片段元数据

        评分策略：RRF 分数范围不固定（取决于两路是否都命中），需归一化。
        - 找到当前批次的 max_score 与 min_score
        - 线性映射到 [INTERNAL_SCORE_MIN, INTERNAL_SCORE_MAX] = [0.7, 0.9]
        - 单条证据时直接给中位数 0.8

        Args:
            internal_evidence: HybridSearcher 返回的 chunk 字典列表。

        Returns:
            统一格式化的证据字典列表。
        """
        if not internal_evidence:
            return []

        # 提取 RRF 分数用于归一化
        scores = [float(ev.get("merged_score", 0.0)) for ev in internal_evidence]
        max_score = max(scores) if scores else 0.0
        min_score = min(scores) if scores else 0.0

        result: list[dict[str, Any]] = []
        for ev in internal_evidence:
            raw_score = float(ev.get("merged_score", 0.0))
            # 归一化到 [0.7, 0.9]
            if max_score > min_score:
                # 线性映射：(raw - min) / (max - min) → [0, 1] → [0.7, 0.9]
                normalized_score = (
                    self.INTERNAL_SCORE_MIN
                    + (raw_score - min_score)
                    / (max_score - min_score)
                    * (self.INTERNAL_SCORE_MAX - self.INTERNAL_SCORE_MIN)
                )
            else:
                # 单条证据或分数相同：给中位数 0.8
                normalized_score = (self.INTERNAL_SCORE_MIN + self.INTERNAL_SCORE_MAX) / 2

            # 构造标题：优先用 metadata.title，否则用 document_id + page
            metadata = ev.get("metadata") or {}
            title = (
                metadata.get("title")
                or metadata.get("source")
                or f"知识库片段 {ev.get('document_id', '')}"
            )

            result.append({
                "type": "internal",
                "title": str(title),
                "snippet": str(ev.get("content", "")),
                "source_url": metadata.get("source_url"),  # 内部证据通常无 URL
                "published_at": metadata.get("published_at"),
                "data_as_of": metadata.get("data_as_of"),
                "score": round(normalized_score, 4),
            })
        return result

    def _normalize_web(
        self, web_evidence: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """格式化网络证据并评分。

        网络证据来自 WebResearchService.WebEvidence 转 dict，含：
        - ``title`` / ``url`` / ``snippet`` / ``content`` / ``published_at`` / ``score``

        评分策略：WebResearchService 已给默认 0.5，这里按出现顺序线性映射到 [0.7, 0.5]。
        - 首位（最相关）→ 0.7
        - 末位 → 0.5
        - 中间线性插值
        这样保证搜索结果中靠前的网页评分略高，但仍低于内部证据峰值 0.9。

        Args:
            web_evidence: WebEvidence 字典列表。

        Returns:
            统一格式化的证据字典列表。
        """
        if not web_evidence:
            return []

        total = len(web_evidence)
        result: list[dict[str, Any]] = []
        for idx, ev in enumerate(web_evidence):
            # 线性映射：idx=0 → 0.7，idx=total-1 → 0.5
            if total > 1:
                ratio = idx / (total - 1)
                score = self.WEB_SCORE_MAX - ratio * (
                    self.WEB_SCORE_MAX - self.WEB_SCORE_MIN
                )
            else:
                # 单条证据：给区间中位数 0.6
                score = (self.WEB_SCORE_MIN + self.WEB_SCORE_MAX) / 2

            # snippet 优先使用搜索摘要，无摘要时用正文截取
            snippet = ev.get("snippet") or ""
            content = ev.get("content")
            if not snippet and content:
                # 无摘要但有正文：取正文前 500 字符作为 snippet
                snippet = str(content)[:500]

            result.append({
                "type": "web",
                "title": str(ev.get("title", "")),
                "snippet": snippet,
                "source_url": ev.get("url"),
                "published_at": ev.get("published_at"),
                "data_as_of": None,  # 网络证据通常无 data_as_of
                "score": round(score, 4),
            })
        return result

    def _normalize_tool(
        self, tool_evidence: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """格式化工具证据并评分。

        工具证据来自 ToolExecutor 返回的 ToolExecutionResult.data 转 dict，含：
        - ``tool_code`` / ``data`` / ``title``

        评分策略：工具返回的是结构化实时数据，可信度高。按出现顺序映射到 [0.95, 0.8]。
        - 首位 → 0.95
        - 末位 → 0.8
        工具数据通常用于补充时效性信息（如基金净值），评分高于网络证据。

        Args:
            tool_evidence: 工具证据字典列表。

        Returns:
            统一格式化的证据字典列表。
        """
        if not tool_evidence:
            return []

        total = len(tool_evidence)
        result: list[dict[str, Any]] = []
        for idx, ev in enumerate(tool_evidence):
            # 线性映射：idx=0 → 0.95，idx=total-1 → 0.8
            if total > 1:
                ratio = idx / (total - 1)
                score = self.TOOL_SCORE_MAX - ratio * (
                    self.TOOL_SCORE_MAX - self.TOOL_SCORE_MIN
                )
            else:
                # 单条证据：给区间中位数 0.875
                score = (self.TOOL_SCORE_MIN + self.TOOL_SCORE_MAX) / 2

            # 工具数据序列化为 snippet 文本，便于模型引用
            data = ev.get("data") or {}
            tool_code = ev.get("tool_code", "tool")
            title = ev.get("title") or f"工具 {tool_code} 返回数据"
            # 将 data dict 序列化为可读文本，便于模型理解
            snippet = self._format_tool_data(data)

            result.append({
                "type": "tool",
                "title": str(title),
                "snippet": snippet,
                "source_url": None,  # 工具证据无 URL
                "published_at": None,
                "data_as_of": data.get("data_as_of") or data.get("as_of"),
                "score": round(score, 4),
            })
        return result

    @staticmethod
    def _format_tool_data(data: dict[str, Any]) -> str:
        """将工具返回的 data dict 格式化为可读文本。

        工具数据结构由各工具的 output_schema 决定，这里采用通用策略：
        - 若 data 含 ``items`` 列表：逐项格式化为 "key: value" 行
        - 否则：直接序列化为 str(data)，截断到 1500 字符

        Args:
            data: 工具返回的数据 dict。

        Returns:
            可读文本，供模型作为 snippet 引用。
        """
        if not isinstance(data, dict):
            return str(data)[:1500]

        # 含 items 列表：逐项格式化（常见于行情、基金列表工具）
        items = data.get("items") or data.get("results")
        if isinstance(items, list) and items:
            lines: list[str] = []
            for item in items[:5]:  # 最多展示 5 项
                if isinstance(item, dict):
                    # 取关键字段：name / title / code / value / price 等
                    key_fields = ["name", "title", "code", "value", "price", "amount"]
                    parts = [
                        f"{k}={item.get(k)}"
                        for k in key_fields
                        if item.get(k) is not None
                    ]
                    if parts:
                        lines.append(" | ".join(parts))
            if lines:
                return "\n".join(lines)

        # 兜底：直接序列化为字符串，截断到 1500 字符
        text = str(data)
        return text[:1500]

    def _dedup(self, evidences: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """证据去重：按 source_url 或 content 哈希去重。

        去重策略
        --------
        - 有 source_url：按 URL 去重（同一网页只保留一条）
        - 无 source_url：按 snippet 内容哈希去重（相同内容不同来源合并）
        - 保留首次出现的版本（已按类型优先级排序：internal > tool > web）

        Args:
            evidences: 待去重的证据列表。

        Returns:
            去重后的证据列表，顺序保持不变。
        """
        seen_urls: set[str] = set()
        seen_hashes: set[str] = set()
        result: list[dict[str, Any]] = []

        for ev in evidences:
            url = ev.get("source_url")
            snippet = ev.get("snippet") or ""

            if url:
                # 有 URL：按 URL 去重
                if url in seen_urls:
                    continue
                seen_urls.add(url)
            else:
                # 无 URL：按 snippet 内容哈希去重
                content_hash = hashlib.md5(snippet.encode("utf-8")).hexdigest()
                if content_hash in seen_hashes:
                    continue
                seen_hashes.add(content_hash)

            result.append(ev)

        return result
