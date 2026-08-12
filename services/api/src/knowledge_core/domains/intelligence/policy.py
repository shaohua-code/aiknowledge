from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol


class EvidenceSufficiency(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    NONE = "NONE"
    STALE = "STALE"
    CONFLICTING = "CONFLICTING"


class AnswerMode(StrEnum):
    KNOWLEDGE_GROUNDED = "KNOWLEDGE_GROUNDED"
    HYBRID = "HYBRID"
    MODEL_ONLY = "MODEL_ONLY"
    WEB_GROUNDED = "WEB_GROUNDED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    DEGRADED = "DEGRADED"


class HitLike(Protocol):
    score: float
    published_at: datetime | None


class ProfileLike(Protocol):
    knowledge_required: bool
    model_fallback_allowed: bool
    web_fallback_allowed: bool
    minimum_evidence_count: int
    minimum_evidence_score: float
    require_fresh_data: bool
    maximum_data_age_seconds: int | None


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    sufficiency: EvidenceSufficiency
    mode: AnswerMode
    model_allowed: bool
    reason: str


def evaluate_sufficiency(hits: Sequence[HitLike], profile: ProfileLike) -> EvidenceSufficiency:
    if not hits:
        return EvidenceSufficiency.NONE
    qualified = [hit for hit in hits if hit.score >= profile.minimum_evidence_score]
    if profile.require_fresh_data and profile.maximum_data_age_seconds:
        cutoff = datetime.now(UTC) - timedelta(seconds=profile.maximum_data_age_seconds)
        fresh = [
            hit for hit in qualified if hit.published_at is not None and hit.published_at >= cutoff
        ]
        if not fresh and qualified:
            return EvidenceSufficiency.STALE
        qualified = fresh
    if len(qualified) >= profile.minimum_evidence_count:
        return EvidenceSufficiency.SUFFICIENT
    return EvidenceSufficiency.PARTIAL


def decide_answer_mode(
    hits: Sequence[HitLike],
    profile: ProfileLike,
    *,
    model_available: bool,
    web_available: bool = False,
) -> PolicyDecision:
    sufficiency = evaluate_sufficiency(hits, profile)
    if sufficiency == EvidenceSufficiency.SUFFICIENT:
        return PolicyDecision(
            sufficiency,
            AnswerMode.KNOWLEDGE_GROUNDED,
            model_available,
            "知识证据达到回答策略门槛",
        )
    if sufficiency == EvidenceSufficiency.PARTIAL:
        return PolicyDecision(
            sufficiency,
            AnswerMode.HYBRID if model_available else AnswerMode.DEGRADED,
            model_available,
            "知识只覆盖部分问题，需要模型分析补充",
        )
    if sufficiency in {EvidenceSufficiency.STALE, EvidenceSufficiency.CONFLICTING}:
        if profile.web_fallback_allowed and web_available:
            return PolicyDecision(
                sufficiency,
                AnswerMode.WEB_GROUNDED,
                model_available,
                "内部知识已过期或存在冲突，切换到允许的联网证据",
            )
        return PolicyDecision(
            sufficiency,
            AnswerMode.INSUFFICIENT_EVIDENCE,
            False,
            "现有知识不满足时效或一致性要求",
        )
    if profile.web_fallback_allowed and web_available:
        return PolicyDecision(
            sufficiency,
            AnswerMode.WEB_GROUNDED,
            model_available,
            "内部知识未命中，切换到允许的联网证据",
        )
    if profile.knowledge_required:
        return PolicyDecision(
            sufficiency,
            AnswerMode.INSUFFICIENT_EVIDENCE,
            False,
            "回答策略要求知识证据，但当前未命中",
        )
    if profile.model_fallback_allowed and model_available:
        return PolicyDecision(
            sufficiency,
            AnswerMode.MODEL_ONLY,
            True,
            "知识未命中，回答策略允许模型通用能力兜底",
        )
    return PolicyDecision(
        sufficiency,
        AnswerMode.INSUFFICIENT_EVIDENCE,
        False,
        "知识未命中且没有可用的受控兜底能力",
    )
