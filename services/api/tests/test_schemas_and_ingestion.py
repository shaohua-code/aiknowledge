from __future__ import annotations

import pytest
from pydantic import ValidationError

from knowledge_core.domains.applications.schemas import ApiKeyCreate
from knowledge_core.domains.intelligence.schemas import AnswerProfileCreate
from knowledge_core.domains.knowledge.ingestion import chunk_blocks, parse_content


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
