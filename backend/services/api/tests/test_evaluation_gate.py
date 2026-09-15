"""验证发布门禁不会将部分通过或技术成功错误放行。"""

from __future__ import annotations

from knowledge_core.domains.evaluations.gate import evaluate_deterministic_checks, summarize_run


def test_gate_requires_every_configured_deterministic_check() -> None:
    """模式正确但缺少预期文档证据时，门禁必须失败。"""
    result = evaluate_deterministic_checks(
        expected_document_ids=["document-a"],
        expected_answer_modes=["KNOWLEDGE_GROUNDED"],
        expected_output_schema={"type": "object", "required": ["answer"]},
        actual_answer_mode="KNOWLEDGE_GROUNDED",
        structured_output={"answer": "已找到"},
        citation_document_ids=[],
    )

    assert result["answerMode"]["passed"] is True
    assert result["requiredEvidence"]["passed"] is False
    assert result["outputSchema"]["passed"] is True
    assert result["gatePassed"] is False


def test_gate_reports_schema_failure_without_claiming_pass() -> None:
    """结构不满足样本 Schema 时，门禁应留下失败字段并拒绝通过。"""
    result = evaluate_deterministic_checks(
        expected_document_ids=[],
        expected_answer_modes=[],
        expected_output_schema={"type": "object", "required": ["score"]},
        actual_answer_mode="MODEL_ONLY",
        structured_output={},
        citation_document_ids=[],
    )

    assert result["outputSchema"]["passed"] is False
    assert result["gatePassed"] is False


def test_run_summary_rejects_empty_and_partially_failed_runs() -> None:
    """没有样本或任一样本技术/门禁失败时，运行均不能放行发布。"""
    assert summarize_run([])["gatePassed"] is False
    result = summarize_run(
        [
            {"status": "succeeded", "checks": {"gatePassed": True}},
            {"status": "failed", "checks": {"gatePassed": False}},
        ]
    )

    assert result == {
        "totalCases": 2,
        "succeededCases": 1,
        "passedCases": 1,
        "failedCases": 1,
        "gatePassed": False,
    }
