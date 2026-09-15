"""固定评测样本的确定性发布门禁检查。"""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


def evaluate_deterministic_checks(
    *,
    expected_document_ids: list[str],
    expected_answer_modes: list[str],
    expected_output_schema: dict[str, Any],
    actual_answer_mode: str,
    structured_output: dict[str, Any],
    citation_document_ids: list[str],
) -> dict[str, Any]:
    """返回可审计的模式、结构和证据检查；任一启用检查失败即不通过。"""
    checks: dict[str, Any] = {}
    if expected_answer_modes:
        checks["answerMode"] = {
            "required": expected_answer_modes,
            "actual": actual_answer_mode,
            "passed": actual_answer_mode in expected_answer_modes,
        }
    if expected_document_ids:
        actual_documents = set(citation_document_ids)
        missing = sorted(set(expected_document_ids) - actual_documents)
        checks["requiredEvidence"] = {
            "required": expected_document_ids,
            "actual": sorted(actual_documents),
            "missing": missing,
            "passed": not missing,
        }
    if expected_output_schema:
        try:
            Draft202012Validator(expected_output_schema).validate(structured_output)
            checks["outputSchema"] = {"passed": True}
        except ValidationError as exc:
            checks["outputSchema"] = {
                "passed": False,
                "path": ".".join(str(item) for item in exc.absolute_path),
                "reason": exc.message,
            }
    enabled = [item["passed"] for item in checks.values()]
    checks["gatePassed"] = bool(enabled) and all(enabled)
    return checks


def summarize_run(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总运行门禁：空运行、技术失败或任一样本失败都不能发布。"""
    total = len(case_results)
    succeeded = [item for item in case_results if item["status"] == "succeeded"]
    passed = [item for item in succeeded if item["checks"].get("gatePassed") is True]
    failed = total - len(passed)
    return {
        "totalCases": total,
        "succeededCases": len(succeeded),
        "passedCases": len(passed),
        "failedCases": failed,
        "gatePassed": total > 0 and failed == 0,
    }
