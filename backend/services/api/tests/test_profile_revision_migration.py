"""验证策略首修订回填时的配置快照边界。"""

from __future__ import annotations

from uuid import uuid4

from knowledge_core.infrastructure.models import AnswerProfile, RetrievalProfile
from knowledge_core.infrastructure.profile_revision_migration import (
    answer_configuration,
    retrieval_configuration,
)


def test_retrieval_configuration_contains_all_runtime_fields() -> None:
    """首修订必须冻结旧检索策略的全部运行参数，而不是只保存编码。"""
    profile = RetrievalProfile(
        code="resume_retrieval",
        name="简历检索",
        collection_ids=["collection-a"],
        top_k=6,
        minimum_score=0.3,
        vector_weight=0.7,
        lexical_weight=0.3,
        metadata_filters={"category": "resume"},
    )

    assert retrieval_configuration(profile) == {
        "code": "resume_retrieval",
        "name": "简历检索",
        "collection_ids": ["collection-a"],
        "top_k": 6,
        "minimum_score": 0.3,
        "vector_weight": 0.7,
        "lexical_weight": 0.3,
        "metadata_filters": {"category": "resume"},
    }


def test_answer_configuration_keeps_link_and_policy_fields() -> None:
    """回答首修订既保留原检索关联，也冻结提示词和所有回答策略开关。"""
    retrieval_profile_id = uuid4()
    profile = AnswerProfile(
        code="resume_answer",
        name="简历回答",
        retrieval_profile_id=retrieval_profile_id,
        system_prompt="仅根据证据回答",
        output_schema={"type": "object"},
        tool_codes=["search"],
        knowledge_required=True,
        model_fallback_allowed=False,
        web_fallback_allowed=True,
        minimum_evidence_count=2,
        minimum_evidence_score=0.7,
        require_fresh_data=True,
        maximum_data_age_seconds=3600,
    )

    configuration = answer_configuration(profile)

    assert configuration["retrieval_profile_id"] == str(retrieval_profile_id)
    assert configuration["system_prompt"] == "仅根据证据回答"
    assert configuration["output_schema"] == {"type": "object"}
    assert configuration["knowledge_required"] is True
    assert configuration["maximum_data_age_seconds"] == 3600
