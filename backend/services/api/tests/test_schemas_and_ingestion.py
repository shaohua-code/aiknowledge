from __future__ import annotations

import pytest
from pydantic import ValidationError

from knowledge_core.domains.applications.schemas import ApiKeyCreate
from knowledge_core.domains.intelligence.schemas import AnswerProfileCreate
from knowledge_core.domains.knowledge.ingestion import chunk_blocks, parse_content
from knowledge_core.domains.knowledge.schemas import RemoteDocumentCreate


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
