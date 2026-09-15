"""把既有策略主表安全映射为首个不可变修订的受控工具。"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from knowledge_core.config import settings
from knowledge_core.infrastructure.models import (
    AnswerProfile,
    AnswerProfileRevision,
    ProfileRelease,
    RetrievalProfile,
    RetrievalProfileRevision,
)


class ProfileRevisionMigrationError(ValueError):
    """策略主表与既有修订状态不满足可确定性回填条件。"""


def retrieval_configuration(profile: RetrievalProfile) -> dict[str, Any]:
    """从旧检索策略提取完整运行参数，且不依赖 ORM 内部状态。"""
    return {
        "code": profile.code,
        "name": profile.name,
        "collection_ids": profile.collection_ids,
        "top_k": profile.top_k,
        "minimum_score": profile.minimum_score,
        "vector_weight": profile.vector_weight,
        "lexical_weight": profile.lexical_weight,
        "metadata_filters": profile.metadata_filters,
    }


def answer_configuration(profile: AnswerProfile) -> dict[str, Any]:
    """从旧回答策略提取完整运行参数，关联的检索修订另列保存。"""
    return {
        "code": profile.code,
        "name": profile.name,
        "retrieval_profile_id": str(profile.retrieval_profile_id),
        "system_prompt": profile.system_prompt,
        "output_schema": profile.output_schema,
        "tool_codes": profile.tool_codes,
        "knowledge_required": profile.knowledge_required,
        "model_fallback_allowed": profile.model_fallback_allowed,
        "web_fallback_allowed": profile.web_fallback_allowed,
        "minimum_evidence_count": profile.minimum_evidence_count,
        "minimum_evidence_score": profile.minimum_evidence_score,
        "require_fresh_data": profile.require_fresh_data,
        "maximum_data_age_seconds": profile.maximum_data_age_seconds,
    }


async def _count_missing_revisions(
    session: AsyncSession,
    profile_type: type[RetrievalProfile] | type[AnswerProfile],
    revision_type: type[RetrievalProfileRevision] | type[AnswerProfileRevision],
) -> int:
    """只返回尚无任何修订的策略数，用于不写库的预检摘要。"""
    return int(
        await session.scalar(
            select(func.count())
            .select_from(profile_type)
            .where(
                ~select(revision_type.id)
                .where(revision_type.profile_id == profile_type.id)
                .exists()
            )
        )
        or 0
    )


async def preflight_profile_revisions(session: AsyncSession) -> dict[str, int]:
    """统计待回填策略；摘要不包含提示词、Schema 或任何配置内容。"""
    retrieval_total = int(
        await session.scalar(select(func.count()).select_from(RetrievalProfile)) or 0
    )
    answer_total = int(await session.scalar(select(func.count()).select_from(AnswerProfile)) or 0)
    return {
        "retrievalProfileCount": retrieval_total,
        "answerProfileCount": answer_total,
        "retrievalMissingRevisionCount": await _count_missing_revisions(
            session, RetrievalProfile, RetrievalProfileRevision
        ),
        "answerMissingRevisionCount": await _count_missing_revisions(
            session, AnswerProfile, AnswerProfileRevision
        ),
    }


async def apply_profile_revisions(session: AsyncSession) -> dict[str, int]:
    """在调用方的单一事务内回填缺失首修订和对应发布事件，不改写旧策略。"""
    retrieval_profiles = list(await session.scalars(select(RetrievalProfile)))
    answer_profiles = list(await session.scalars(select(AnswerProfile)))
    existing_retrieval = list(await session.scalars(select(RetrievalProfileRevision)))
    existing_answer = list(await session.scalars(select(AnswerProfileRevision)))
    retrieval_by_profile = {revision.profile_id: revision for revision in existing_retrieval}
    answer_by_profile = {revision.profile_id: revision for revision in existing_answer}
    now = datetime.now(UTC)
    created_retrieval = 0
    for profile in retrieval_profiles:
        if profile.id in retrieval_by_profile:
            continue
        state = "published" if profile.status == "active" else "retired"
        revision = RetrievalProfileRevision(
            application_id=profile.application_id,
            environment_id=profile.environment_id,
            profile_id=profile.id,
            revision=1,
            state=state,
            configuration=retrieval_configuration(profile),
            published_at=now if state == "published" else None,
        )
        session.add(revision)
        retrieval_by_profile[profile.id] = revision
        created_retrieval += 1
    await session.flush()

    created_answer = 0
    for profile in answer_profiles:
        if profile.id in answer_by_profile:
            continue
        retrieval_revision = retrieval_by_profile.get(profile.retrieval_profile_id)
        if retrieval_revision is None:
            raise ProfileRevisionMigrationError("回答策略关联的检索策略缺少可回填修订")
        state = "published" if profile.status == "active" else "retired"
        revision = AnswerProfileRevision(
            application_id=profile.application_id,
            environment_id=profile.environment_id,
            profile_id=profile.id,
            retrieval_revision_id=retrieval_revision.id,
            revision=1,
            state=state,
            configuration=answer_configuration(profile),
            published_at=now if state == "published" else None,
        )
        session.add(revision)
        answer_by_profile[profile.id] = revision
        created_answer += 1
    await session.flush()

    existing_releases = {
        (release.profile_kind, release.profile_id, release.revision_id)
        for release in await session.scalars(select(ProfileRelease))
    }
    created_releases = 0
    for profile in retrieval_profiles:
        revision = retrieval_by_profile[profile.id]
        key = ("retrieval", profile.id, revision.id)
        if profile.status == "active" and key not in existing_releases:
            session.add(
                ProfileRelease(
                    application_id=profile.application_id,
                    environment_id=profile.environment_id,
                    profile_kind="retrieval",
                    profile_id=profile.id,
                    revision_id=revision.id,
                    action="migrated",
                )
            )
            created_releases += 1
    for profile in answer_profiles:
        revision = answer_by_profile[profile.id]
        key = ("answer", profile.id, revision.id)
        if profile.status == "active" and key not in existing_releases:
            session.add(
                ProfileRelease(
                    application_id=profile.application_id,
                    environment_id=profile.environment_id,
                    profile_kind="answer",
                    profile_id=profile.id,
                    revision_id=revision.id,
                    action="migrated",
                )
            )
            created_releases += 1
    return {
        "createdRetrievalRevisions": created_retrieval,
        "createdAnswerRevisions": created_answer,
        "createdReleaseEvents": created_releases,
    }


async def _run(arguments: argparse.Namespace) -> dict[str, object]:
    """使用一次性连接执行预检或写入，避免命令行遗留跨事件循环连接。"""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            summary: dict[str, object] = await preflight_profile_revisions(session)
            summary["applied"] = False
            if arguments.apply:
                # 预检 SELECT 会隐式开启事务；先结束只读事务，才能明确建立唯一写事务。
                await session.rollback()
                async with session.begin():
                    summary |= await apply_profile_revisions(session)
                summary["applied"] = True
            return summary
    finally:
        await engine.dispose()


def main() -> int:
    """默认只读预检；只有显式 --apply 才创建首修订和发布事件。"""
    parser = argparse.ArgumentParser(description="安全回填既有策略的首个不可变修订")
    parser.add_argument("--apply", action="store_true", help="在单一事务内创建缺失修订和发布事件")
    arguments = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(_run(arguments)), ensure_ascii=False))
    except ProfileRevisionMigrationError as exc:
        print(json.dumps({"applied": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
