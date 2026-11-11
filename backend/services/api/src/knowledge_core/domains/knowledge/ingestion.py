from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser

import pymupdf
from docx import Document as WordDocument


@dataclass(slots=True)
class ParsedBlock:
    text: str
    page_number: int | None = None
    section: str | None = None


def parse_content(content: bytes, mime_type: str | None, storage_key: str) -> list[ParsedBlock]:
    lower_key = storage_key.lower()
    if mime_type == "application/pdf" or lower_key.endswith(".pdf"):
        return _parse_pdf(content)
    if (
        mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        or lower_key.endswith(".docx")
    ):
        return _parse_docx(content)
    if mime_type == "text/html":
        return [ParsedBlock(_html_to_text(_decode_text(content)))]
    if mime_type in {
        "application/json",
        "application/ld+json",
        "application/x-ndjson",
    } or lower_key.endswith(".json"):
        return _parse_json(content)
    if mime_type in {
        "application/xml",
        "application/rss+xml",
        "application/atom+xml",
        "text/xml",
    } or lower_key.endswith(".xml"):
        return _parse_xml(content)
    if mime_type in {"text/csv", "application/csv"} or lower_key.endswith(".csv"):
        return _parse_csv(content)
    if (mime_type or "").startswith("text/") or lower_key.endswith((".txt", ".md")):
        return [ParsedBlock(_decode_text(content))]
    raise ValueError(f"不支持的文档类型：{mime_type or lower_key}")


def _decode_text(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("utf-8", errors="replace")


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden_depth = 0
        self.main_depth = 0
        self.has_main = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript", "svg", "nav", "footer", "aside", "form"}:
            self.hidden_depth += 1
        elif tag in {"main", "article"}:
            self.has_main = True
            self.main_depth += 1
            self.parts.append("\n")
        elif tag in {"p", "div", "section", "article", "li", "br", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if (
            tag in {"script", "style", "noscript", "svg", "nav", "footer", "aside", "form"}
            and self.hidden_depth
        ):
            self.hidden_depth -= 1
        elif tag in {"main", "article"} and self.main_depth:
            self.main_depth -= 1
            self.parts.append("\n")
        elif tag in {"p", "div", "section", "article", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def _html_to_text(value: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(value)
    return normalize_text(" ".join(parser.parts))


def _parse_json(content: bytes) -> list[ParsedBlock]:
    try:
        payload = json.loads(_decode_text(content))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 第 {exc.lineno} 行、第 {exc.colno} 列附近格式错误") from exc
    blocks: list[ParsedBlock] = []
    rows = payload if isinstance(payload, list) else [payload]
    for index, row in enumerate(rows):
        if isinstance(row, dict):
            text = "\n".join(
                f"{key}: {_json_value_text(value)}"
                for key, value in row.items()
                if value not in (None, "", [], {})
            )
        else:
            text = _json_value_text(row)
        if text.strip():
            blocks.append(ParsedBlock(text=text, section=f"记录 {index + 1}"))
    if not blocks:
        raise ValueError("JSON 中没有可转换为知识的有效字段")
    return blocks


def _json_value_text(value) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _parse_xml(content: bytes) -> list[ParsedBlock]:
    try:
        root = ET.fromstring(_decode_text(content))
    except ET.ParseError as exc:
        raise ValueError(f"XML 在第 {exc.position[0]} 行附近格式错误") from exc
    candidates = [
        node
        for node in root.iter()
        if node.tag.split("}")[-1].lower() in {"item", "entry", "article", "record"}
    ]
    if not candidates:
        candidates = [root]
    blocks: list[ParsedBlock] = []
    for index, node in enumerate(candidates):
        parts: list[str] = []
        for child in node.iter():
            value = normalize_text(" ".join(child.itertext()))
            if not value or child is node:
                continue
            name = child.tag.split("}")[-1]
            if len(value) <= 20_000:
                parts.append(f"{name}: {value}")
        text = normalize_text("\n".join(dict.fromkeys(parts)))
        if text:
            blocks.append(ParsedBlock(text=text, section=f"条目 {index + 1}"))
    if not blocks:
        root_text = normalize_text(" ".join(root.itertext()))
        if root_text:
            blocks.append(ParsedBlock(text=root_text))
    if not blocks:
        raise ValueError("XML/RSS 中没有可提取文本")
    return blocks


def _parse_csv(content: bytes) -> list[ParsedBlock]:
    text = _decode_text(content)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("CSV 缺少表头，无法识别每列含义")
    blocks: list[ParsedBlock] = []
    for index, row in enumerate(reader):
        values = [
            f"{key}: {value}" for key, value in row.items() if key and value and value.strip()
        ]
        if values:
            blocks.append(ParsedBlock(text="\n".join(values), section=f"第 {index + 1} 行"))
    if not blocks:
        raise ValueError("CSV 除表头外没有有效数据")
    return blocks


def _parse_pdf(content: bytes) -> list[ParsedBlock]:
    document: pymupdf.Document = pymupdf.open(stream=content, filetype="pdf")
    try:
        blocks: list[ParsedBlock] = []
        for index in range(document.page_count):
            page: pymupdf.Page = document.load_page(index)
            text = page.get_text("text")
            if text.strip():
                blocks.append(ParsedBlock(text, page_number=index + 1))
    finally:
        document.close()
    if not blocks:
        raise ValueError("PDF 没有可提取文本，扫描件需要先经过 OCR")
    return blocks


_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MARKUP_NS = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"


def _iter_visible_paragraphs(element):
    """按文档顺序产出 w:p，跳过 mc:Fallback 子树（其中是 mc:Choice 内容的简化副本）。"""
    for child in element:
        if child.tag == f"{_MARKUP_NS}Fallback":
            continue
        if child.tag == f"{_WORD_NS}p":
            yield child
        else:
            yield from _iter_visible_paragraphs(child)


def _paragraph_visible_text(paragraph) -> str:
    """聚合 w:p 内可见文本；mc:AlternateContent 可能嵌在段落内部（run 级），
    其 mc:Fallback 分支装着同样内容的简化副本，必须一并跳过避免重复。"""
    parts: list[str] = []

    def walk(element) -> None:
        for child in element:
            if child.tag == f"{_MARKUP_NS}Fallback":
                continue
            if child.tag == f"{_WORD_NS}t":
                parts.append(child.text or "")
            else:
                walk(child)

    walk(paragraph)
    return "".join(parts).strip()


def _parse_docx(content: bytes) -> list[ParsedBlock]:
    document = WordDocument(io.BytesIO(content))
    paragraphs = [
        paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()
    ]
    # 简历等排版模板常把正文放在表格里，python-docx 的 paragraphs 覆盖不到，需要补表格文本
    seen_cells: set[int] = set()
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                # 合并单元格会被重复遍历，按底层 XML 节点去重
                if id(cell._tc) in seen_cells:
                    continue
                seen_cells.add(id(cell._tc))
                text = cell.text.strip()
                if text:
                    paragraphs.append(text)
    if not paragraphs:
        # 设计类模板（如简历）常把全部内容放在文本框/形状里，python-docx 不暴露这些对象，
        # 退回 XML 级提取：按 w:p 聚合内部 w:t 文本节点，保留行文结构
        seen_texts: set[str] = set()
        for paragraph in _iter_visible_paragraphs(document.element.body):
            text = _paragraph_visible_text(paragraph)
            if not text:
                continue
            # 兜底去重：同一段文字被多个文本框重复引用时只保留第一份
            key = re.sub(r"\s+", "", text)
            if key in seen_texts:
                continue
            seen_texts.add(key)
            paragraphs.append(text)
    if not paragraphs:
        raise ValueError("DOCX 没有可提取文本，若内容全在图片中需要先 OCR")
    return [ParsedBlock("\n\n".join(paragraphs))]


def normalize_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value)
    value = "\n".join(line.rstrip() for line in value.splitlines())
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def chunk_blocks(
    blocks: list[ParsedBlock],
    *,
    target_chars: int = 1200,
    overlap_chars: int = 180,
    chunking_strategy: str | None = None,
) -> list[dict]:
    """将解析后的文档块切分为检索片段。

    支持可配置的分块策略（通过 chunking_strategy 参数）：
    - None 或 "fixed_size"：固定字符数分块（默认，向后兼容）
    - "paragraph"：按段落分块
    - "sentence"：按句子分块

    Args:
        blocks: 解析后的文档块列表
        target_chars: 目标字符数（默认 1200）
        overlap_chars: 重叠字符数（默认 180，仅 fixed_size 策略使用）
        chunking_strategy: 分块策略，可选值见上

    Returns:
        片段字典列表，每个字典包含 chunk_index、content、page_number、section 等

    Raises:
        ValueError: 文档清洗和切割后没有有效内容
    """
    # 显式策略才进入 RAG 分块；缺省和 fixed_size 继续走原来的固定字符切分。
    if chunking_strategy and chunking_strategy != "fixed_size":
        return _chunk_blocks_with_strategy(
            blocks,
            chunking_strategy,
            target_chars,
            overlap_chars,
        )

    # 默认固定字符数分块（向后兼容）
    chunks: list[dict] = []
    for block in blocks:
        text = normalize_text(block.text)
        if not text:
            continue
        paragraphs = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
        buffer = ""
        for paragraph in paragraphs:
            candidate = f"{buffer}\n\n{paragraph}".strip() if buffer else paragraph
            if len(candidate) <= target_chars:
                buffer = candidate
                continue
            if buffer:
                chunks.append(_chunk_dict(buffer, block, len(chunks)))
                overlap = buffer[-overlap_chars:] if overlap_chars else ""
                buffer = f"{overlap}\n{paragraph}".strip()
            else:
                start = 0
                while start < len(paragraph):
                    end = start + target_chars
                    chunks.append(_chunk_dict(paragraph[start:end], block, len(chunks)))
                    start = max(end - overlap_chars, start + 1)
                buffer = ""
        if buffer:
            chunks.append(_chunk_dict(buffer, block, len(chunks)))
    if not chunks:
        raise ValueError("文档清洗和切割后没有有效内容")
    return chunks


def resolve_chunking_options(metadata: dict | None) -> tuple[str | None, int, int]:
    """从文档版本 metadata 读取分块开关。

    未配置或策略为 fixed_size 时返回 (None, 1200, 180)，入库继续使用原切分。
    支持顶层 chunkingStrategy，以及 rag.chunking.strategy / targetChars / overlapChars。
    """
    default_target = 1200
    default_overlap = 180
    if not isinstance(metadata, dict):
        return None, default_target, default_overlap
    rag = metadata.get("rag") if isinstance(metadata.get("rag"), dict) else {}
    chunking = rag.get("chunking") if isinstance(rag.get("chunking"), dict) else {}
    raw_strategy = metadata.get("chunkingStrategy") or chunking.get("strategy")
    if not isinstance(raw_strategy, str) or raw_strategy.strip() in {"", "fixed_size"}:
        strategy = None
    else:
        strategy = raw_strategy.strip()
    raw_target = chunking.get("targetChars", metadata.get("chunkTargetChars", default_target))
    raw_overlap = chunking.get("overlapChars", metadata.get("chunkOverlapChars", default_overlap))
    try:
        target_chars = int(raw_target)
        overlap_chars = int(raw_overlap)
    except (TypeError, ValueError):
        return strategy, default_target, default_overlap
    if target_chars < 50:
        target_chars = default_target
    if overlap_chars < 0:
        overlap_chars = default_overlap
    return strategy, target_chars, overlap_chars


def _chunk_blocks_with_strategy(
    blocks: list[ParsedBlock],
    strategy: str,
    target_chars: int,
    overlap_chars: int,
) -> list[dict]:
    """使用 RAG 增强的分块策略处理文档块。

    策略把短文本全部滤掉时回退为保留原文，避免短文档入库失败。

    Args:
        blocks: 解析后的文档块列表
        strategy: 分块策略（paragraph、sentence、semantic）
        target_chars: 目标字符数
        overlap_chars: 重叠字符数，仅在回退到固定切分时使用

    Returns:
        片段字典列表
    """
    from knowledge_core.domains.intelligence.rag_enhancements import (
        ChunkingStrategy,
        RAGEnhancementConfig,
        chunk_by_fixed_size,
        chunk_text,
    )

    try:
        chunking_strategy = ChunkingStrategy(strategy)
    except ValueError:
        # 未知策略名回退到固定大小，不让配置笔误中断入库。
        chunking_strategy = ChunkingStrategy.FIXED_SIZE

    config = RAGEnhancementConfig(
        chunking_strategy=chunking_strategy,
        chunk_target_chars=target_chars,
        chunk_overlap_chars=overlap_chars,
        # 入库不能因为策略的默认最小长度丢掉段尾。切分仍由目标长度控制。
        chunk_min_chars=1,
    )

    chunks: list[dict] = []
    chunk_index = 0

    for block in blocks:
        text = normalize_text(block.text)
        if not text:
            continue

        text_chunks = chunk_text(text, config)
        if not text_chunks:
            # 段落/句子策略有最小长度。短文档达不到时保留全文，而不是拒绝入库。
            text_chunks = chunk_by_fixed_size(
                text,
                target_chars=target_chars,
                overlap_chars=overlap_chars,
                min_chars=1,
            ) or [text]

        for chunk_text_content in text_chunks:
            chunks.append(
                {
                    "chunk_index": chunk_index,
                    "content": chunk_text_content,
                    "page_number": block.page_number,
                    "section": block.section,
                    "token_count": max(1, len(chunk_text_content) // 3),
                    "metadata_": {},
                }
            )
            chunk_index += 1

    if not chunks:
        raise ValueError("文档清洗和切割后没有有效内容")

    return chunks


def _chunk_dict(text: str, block: ParsedBlock, index: int) -> dict:
    return {
        "chunk_index": index,
        "content": text,
        "page_number": block.page_number,
        "section": block.section,
        "token_count": max(1, len(text) // 3),
        "metadata_": {},
    }
