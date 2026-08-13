from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from knowledge_core.domains.intelligence.policy import (
    AnswerMode,
    EvidenceSufficiency,
    decide_answer_mode,
    evaluate_sufficiency,
)


@dataclass
class Hit:
    score: float
    published_at: datetime | None = None


@dataclass
class Profile:
    knowledge_required: bool = False
    model_fallback_allowed: bool = True
    web_fallback_allowed: bool = False
    minimum_evidence_count: int = 1
    minimum_evidence_score: float = 0.55
    require_fresh_data: bool = False
    maximum_data_age_seconds: int | None = None


@pytest.mark.parametrize(
    ("hits", "profile", "model", "web", "expected"),
    [
        ([Hit(0.8)], Profile(), True, False, AnswerMode.KNOWLEDGE_GROUNDED),
        ([Hit(0.4)], Profile(), True, False, AnswerMode.HYBRID),
        ([], Profile(), True, False, AnswerMode.MODEL_ONLY),
        ([], Profile(knowledge_required=True), True, False, AnswerMode.INSUFFICIENT_EVIDENCE),
        ([], Profile(model_fallback_allowed=False), True, False, AnswerMode.INSUFFICIENT_EVIDENCE),
        ([], Profile(web_fallback_allowed=True), True, True, AnswerMode.WEB_GROUNDED),
    ],
)
def test_answer_policy_modes(hits, profile, model, web, expected) -> None:
    decision = decide_answer_mode(
        hits,
        profile,
        model_available=model,
        web_available=web,
    )
    assert decision.mode == expected


def test_stale_evidence_is_not_treated_as_grounded() -> None:
    profile = Profile(
        require_fresh_data=True,
        maximum_data_age_seconds=60,
        web_fallback_allowed=False,
    )
    hits = [Hit(0.9, datetime.now(UTC) - timedelta(minutes=5))]
    assert evaluate_sufficiency(hits, profile) == EvidenceSufficiency.STALE
    assert (
        decide_answer_mode(hits, profile, model_available=True).mode
        == AnswerMode.INSUFFICIENT_EVIDENCE
    )
