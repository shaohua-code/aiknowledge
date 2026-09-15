from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from knowledge_core.domains.applications.schemas import ApiKeyCreate
from knowledge_core.domains.intelligence.schemas import AnswerProfileCreate
from knowledge_core.domains.knowledge.ingestion import chunk_blocks, parse_content
from knowledge_core.domains.knowledge.schemas import (
    DocumentChunkView,
    DocumentRevisionView,
    RemoteDocumentCreate,
)


def test_api_key_rejects_unknown_scope() -> None:
    with pytest.raises(ValidationError, match="不支持的 Scope"):
        ApiKeyCreate(name="bad scope", scopes=["admin:all"])


def test_answer_profile_rejects_invalid_json_schema() -> None:
    with pytest.raises(ValidationError, match="JSON Schema"):
        AnswerProfileCreate(
            code="resume_answer",
            name="简历回答",
            retrieval_profile_id="7e680f08-d7d2-45b4-a2d7-5dd0e02052cb",
            output_schema={"type": "not-a-real-type"},
        )


def test_html_parser_removes_script_content() -> None:
    blocks = parse_content(
        b"<html><body><h1>Title</h1><script>secret()</script><p>Visible</p></body></html>",
        "text/html",
        "page.txt",
    )
    assert "Title" in blocks[0].text
    assert "Visible" in blocks[0].text
    assert "secret" not in blocks[0].text


def test_chunker_produces_overlapping_bounded_chunks() -> None:
    blocks = parse_content(("A" * 3000).encode(), "text/plain", "long.txt")
    chunks = chunk_blocks(blocks, target_chars=500, overlap_chars=50)
    assert len(chunks) > 1
    assert all(len(chunk["content"]) <= 500 for chunk in chunks)
    assert [chunk["chunk_index"] for chunk in chunks] == list(range(len(chunks)))


def test_remote_schema_rejects_get_request_body() -> None:
    with pytest.raises(ValidationError, match="GET 请求不能填写"):
        RemoteDocumentCreate(
            title="bad request",
            url="https://example.com/data",
            method="GET",
            json_body={"page": 1},
        )


def test_revision_and_chunk_schema_read_orm_metadata_column() -> None:
    """ORM 有同名表元数据属性时，版本和片段接口仍须返回业务 metadata JSON。"""
    now = datetime.now(UTC)
    revision = SimpleNamespace(
        id=uuid4(),
        document_id=uuid4(),
        version=1,
        content_hash="a" * 64,
        status="ready",
        char_count=12,
        # 模拟 SQLAlchemy Declarative 基类的 ``metadata``，它不是可序列化的业务字段。
        metadata=object(),
        metadata_={"source": "runtime-test"},
        published_at=now,
        created_at=now,
    )
    chunk = SimpleNamespace(
        id=uuid4(),
        revision_id=revision.id,
        chunk_index=0,
        content="无敏感片段",
        page_number=None,
        section=None,
        token_count=4,
        metadata=object(),
        metadata_={"source": "runtime-test"},
    )

    revision_payload = DocumentRevisionView.model_validate(revision).model_dump(by_alias=True)
    chunk_payload = DocumentChunkView.model_validate(chunk).model_dump(by_alias=True)

    assert revision_payload["metadata"] == {
        "source": "runtime-test"
    }
    assert chunk_payload["metadata"] == {
        "source": "runtime-test"
    }
    # FastAPI 会用 response_model 再校验一次路由已序列化的 JSON，此处覆盖该实际路径。
    assert DocumentRevisionView.model_validate(revision_payload).metadata_ == {
        "source": "runtime-test"
    }
    assert DocumentChunkView.model_validate(chunk_payload).metadata_ == {
        "source": "runtime-test"
    }


def test_json_xml_and_csv_are_parsed_into_readable_blocks() -> None:
    json_blocks = parse_content(
        b'[{"name":"Alice","role":"Engineer"}]',
        "application/json",
        "records.json",
    )
    assert "name: Alice" in json_blocks[0].text

    xml_blocks = parse_content(
        b"<rss><channel><item><title>News</title><description>Body</description></item></channel></rss>",
        "application/xml",
        "feed.xml",
    )
    assert "News" in xml_blocks[0].text

    csv_blocks = parse_content(
        "name,score\n张三,95\n".encode(),
        "text/csv",
        "rows.csv",
    )
    assert "name: 张三" in csv_blocks[0].text


def _build_docx(document_xml: str) -> bytes:
    """手工打包最小 DOCX，用于构造 python-docx 无法生成的文本框等结构。"""
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
            'relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        package.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        package.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def test_docx_extracts_table_text() -> None:
    # 简历等模板常把正文放在表格里，顶层段落为空时也必须能提取
    import io

    from docx import Document as WordDocument

    document = WordDocument()
    table = document.add_table(rows=1, cols=1)
    table.rows[0].cells[0].text = "表格里的项目经历"
    buffer = io.BytesIO()
    document.save(buffer)

    blocks = parse_content(buffer.getvalue(), "", "resume.docx")
    assert "表格里的项目经历" in blocks[0].text


def test_docx_extracts_textbox_text_without_fallback_duplication() -> None:
    # 设计类模板把内容放在文本框；mc:Fallback 分支是同内容简化副本，提取时不能重复
    document_xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
        'xmlns:v="urn:schemas-microsoft-com:vml">'
        "<w:body><w:p><mc:AlternateContent>"
        "<mc:Choice><w:drawing><wps:txbx><w:txbxContent>"
        "<w:p><w:r><w:t>文本框里的简历内容</w:t></w:r></w:p>"
        "</w:txbxContent></wps:txbx></w:drawing></mc:Choice>"
        "<mc:Fallback><w:pict><v:textbox><w:txbxContent>"
        "<w:p><w:r><w:t>文本框里的简历内容</w:t></w:r></w:p>"
        "</w:txbxContent></v:textbox></w:pict></mc:Fallback>"
        "</mc:AlternateContent></w:p></w:body></w:document>"
    )
    blocks = parse_content(_build_docx(document_xml), "", "textbox.docx")
    assert blocks[0].text.count("文本框里的简历内容") == 1
