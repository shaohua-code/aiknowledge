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


def _parse_docx(content: bytes) -> list[ParsedBlock]:
    document = WordDocument(io.BytesIO(content))
    paragraphs = [
        paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()
    ]
    if not paragraphs:
        raise ValueError("DOCX 没有可提取文本")
    return [ParsedBlock("\n\n".join(paragraphs))]


def normalize_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value)
    value = "\n".join(line.rstrip() for line in value.splitlines())
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def chunk_blocks(
    blocks: list[ParsedBlock], *, target_chars: int = 1200, overlap_chars: int = 180
) -> list[dict]:
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


def _chunk_dict(text: str, block: ParsedBlock, index: int) -> dict:
    return {
        "chunk_index": index,
        "content": text,
        "page_number": block.page_number,
        "section": block.section,
        "token_count": max(1, len(text) // 3),
        "metadata_": {},
    }
