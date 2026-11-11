from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.infrastructure.models import (
    AnswerProfile,
    AnswerProfileRevision,
    ProfileRelease,
    RetrievalProfile,
    RetrievalProfileRevision,
)
from knowledge_core.shared.context import ApplicationContext


@dataclass(frozen=True, slots=True)
class PublishedRetrievalProfile:
    """运行面可用的检索配置，永远来自一个不可变发布修订。"""

    id: UUID
    revision_id: UUID
    code: str
    collection_ids: list[str]
    top_k: int
    minimum_score: float
    vector_weight: float
    lexical_weight: float
    metadata_filters: dict[str, str | int | float | bool]
    # 发布快照中的可选 RAG 开关。旧修订没有该键时为 None，检索保持原行为。
    rag_config: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PublishedAnswerProfile:
    """运行面可用的回答配置，并钉住所依赖的检索修订。"""

    id: UUID
    revision_id: UUID
    code: str
    retrieval_revision_id: UUID
    system_prompt: str
    output_schema: dict[str, Any]
    tool_codes: list[str]
    knowledge_required: bool
    model_fallback_allowed: bool
    web_fallback_allowed: bool
    minimum_evidence_count: int
    minimum_evidence_score: float
    require_fresh_data: bool
    maximum_data_age_seconds: int | None


class IntelligenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_retrieval_profiles(
        self, application_id: UUID, environment_id: UUID
    ) -> list[RetrievalProfile]:
        result = await self.session.scalars(
            select(RetrievalProfile)
            .where(
                RetrievalProfile.application_id == application_id,
                RetrievalProfile.environment_id == environment_id,
            )
            .order_by(RetrievalProfile.created_at.desc())
        )
        return list(result)

    async def list_answer_profiles(
        self, application_id: UUID, environment_id: UUID
    ) -> list[AnswerProfile]:
        result = await self.session.scalars(
            select(AnswerProfile)
            .where(
                AnswerProfile.application_id == application_id,
                AnswerProfile.environment_id == environment_id,
            )
            .order_by(AnswerProfile.created_at.desc())
        )
        return list(result)

    async def get_retrieval_profile_by_code(
        self, context: ApplicationContext, code: str
    ) -> RetrievalProfile | None:
        return await self.session.scalar(
            select(RetrievalProfile).where(
                RetrievalProfile.application_id == context.application_id,
                RetrievalProfile.environment_id == context.environment_id,
                RetrievalProfile.code == code,
                RetrievalProfile.status == "active",
            )
        )

    async def get_answer_profile_by_code(
        self, context: ApplicationContext, code: str
    ) -> AnswerProfile | None:
        return await self.session.scalar(
            select(AnswerProfile).where(
                AnswerProfile.application_id == context.application_id,
                AnswerProfile.environment_id == context.environment_id,
                AnswerProfile.code == code,
                AnswerProfile.status == "active",
            )
        )

    @staticmethod
    def _published_retrieval(
        profile: RetrievalProfile, revision: RetrievalProfileRevision
    ) -> PublishedRetrievalProfile:
        """从已验证的 JSON 快照构造运行面配置，不回退读取可变主策略参数。"""
        configuration = revision.configuration
        raw_rag = configuration.get("ragEnhancement", configuration.get("rag_enhancement"))
        # 只接受对象配置；缺省或类型不对时关闭增强，避免脏数据改变检索。
        rag_config = raw_rag if isinstance(raw_rag, dict) else None
        return PublishedRetrievalProfile(
            id=profile.id,
            revision_id=revision.id,
            code=str(configuration["code"]),
            collection_ids=[str(item) for item in configuration["collection_ids"]],
            top_k=int(configuration["top_k"]),
            minimum_score=float(configuration["minimum_score"]),
            vector_weight=float(configuration["vector_weight"]),
            lexical_weight=float(configuration["lexical_weight"]),
            metadata_filters=dict(configuration["metadata_filters"]),
            rag_config=rag_config,
        )

    @staticmethod
    def _published_answer(
        profile: AnswerProfile, revision: AnswerProfileRevision
    ) -> PublishedAnswerProfile:
        """从回答修订快照构造运行面配置，所有策略开关随版本冻结。"""
        if revision.retrieval_revision_id is None:
            raise ValueError("已发布回答修订缺少检索修订引用")
        configuration = revision.configuration
        return PublishedAnswerProfile(
            id=profile.id,
            revision_id=revision.id,
            code=str(configuration["code"]),
            retrieval_revision_id=revision.retrieval_revision_id,
            system_prompt=str(configuration["system_prompt"]),
            output_schema=dict(configuration["output_schema"]),
            tool_codes=[str(item) for item in configuration["tool_codes"]],
            knowledge_required=bool(configuration["knowledge_required"]),
            model_fallback_allowed=bool(configuration["model_fallback_allowed"]),
            web_fallback_allowed=bool(configuration["web_fallback_allowed"]),
            minimum_evidence_count=int(configuration["minimum_evidence_count"]),
            minimum_evidence_score=float(configuration["minimum_evidence_score"]),
            require_fresh_data=bool(configuration["require_fresh_data"]),
            maximum_data_age_seconds=configuration["maximum_data_age_seconds"],
        )

    async def get_published_retrieval_profile_by_code(
        self, context: ApplicationContext, code: str
    ) -> PublishedRetrievalProfile | None:
        """按最近发布事件解析检索策略，拒绝从可变 active 主表直接运行。"""
        profile = await self.get_retrieval_profile_by_code(context, code)
        if profile is None:
            return None
        release = await self.session.scalar(
            select(ProfileRelease)
            .where(
                ProfileRelease.application_id == context.application_id,
                ProfileRelease.environment_id == context.environment_id,
                ProfileRelease.profile_kind == "retrieval",
                ProfileRelease.profile_id == profile.id,
            )
            .order_by(ProfileRelease.created_at.desc())
            .limit(1)
        )
        if release is None:
            return None
        revision = await self.get_retrieval_revision(
            context.application_id, context.environment_id, release.revision_id
        )
        if revision is None or revision.profile_id != profile.id or revision.state != "published":
            return None
        return self._published_retrieval(profile, revision)

    async def get_published_answer_profile_by_code(
        self, context: ApplicationContext, code: str
    ) -> PublishedAnswerProfile | None:
        """按最近发布事件解析回答策略，草稿与未发布主策略不能进入生产。"""
        profile = await self.get_answer_profile_by_code(context, code)
        if profile is None:
            return None
        release = await self.session.scalar(
            select(ProfileRelease)
            .where(
                ProfileRelease.application_id == context.application_id,
                ProfileRelease.environment_id == context.environment_id,
                ProfileRelease.profile_kind == "answer",
                ProfileRelease.profile_id == profile.id,
            )
            .order_by(ProfileRelease.created_at.desc())
            .limit(1)
        )
        if release is None:
            return None
        revision = await self.get_answer_revision(
            context.application_id, context.environment_id, release.revision_id
        )
        if revision is None or revision.profile_id != profile.id or revision.state != "published":
            return None
        return self._published_answer(profile, revision)

    async def get_published_retrieval_profile_by_revision(
        self, context: ApplicationContext, revision_id: UUID
    ) -> PublishedRetrievalProfile | None:
        """回答运行使用已钉住的检索版本，不受后来检索发布事件影响。"""
        revision = await self.get_retrieval_revision(
            context.application_id, context.environment_id, revision_id
        )
        if revision is None or revision.state != "published":
            return None
        profile = await self.get_retrieval_profile(
            context.application_id, context.environment_id, revision.profile_id
        )
        if profile is None:
            return None
        return self._published_retrieval(profile, revision)

    async def get_retrieval_profile_for_evaluation(
        self, context: ApplicationContext, revision_id: UUID
    ) -> PublishedRetrievalProfile | None:
        """评测可解析 draft/validated 候选修订，但仍严格限制当前应用环境。"""
        revision = await self.get_retrieval_revision(
            context.application_id, context.environment_id, revision_id
        )
        if revision is None or revision.state not in {"draft", "validated", "published"}:
            return None
        profile = await self.get_retrieval_profile(
            context.application_id, context.environment_id, revision.profile_id
        )
        return self._published_retrieval(profile, revision) if profile else None

    async def get_answer_profile_for_evaluation(
        self, context: ApplicationContext, revision_id: UUID
    ) -> PublishedAnswerProfile | None:
        """评测可读取候选回答修订，生产运行面仍只读取发布事件。"""
        revision = await self.get_answer_revision(
            context.application_id, context.environment_id, revision_id
        )
        if revision is None or revision.state not in {"draft", "validated", "published"}:
            return None
        profile = await self.get_answer_profile(
            context.application_id, context.environment_id, revision.profile_id
        )
        return self._published_answer(profile, revision) if profile else None

    async def get_retrieval_profile(
        self, application_id: UUID, environment_id: UUID, profile_id: UUID
    ) -> RetrievalProfile | None:
        return await self.session.scalar(
            select(RetrievalProfile).where(
                RetrievalProfile.application_id == application_id,
                RetrievalProfile.environment_id == environment_id,
                RetrievalProfile.id == profile_id,
            )
        )

    async def get_answer_profile(
        self, application_id: UUID, environment_id: UUID, profile_id: UUID
    ) -> AnswerProfile | None:
        """按应用和环境读取回答策略，供控制面修订接口做归属校验。"""
        return await self.session.scalar(
            select(AnswerProfile).where(
                AnswerProfile.application_id == application_id,
                AnswerProfile.environment_id == environment_id,
                AnswerProfile.id == profile_id,
            )
        )

    async def create_retrieval_profile(self, **values: object) -> RetrievalProfile:
        row = RetrievalProfile(**values)
        self.session.add(row)
        await self.session.flush()
        return row

    async def create_answer_profile(self, **values: object) -> AnswerProfile:
        row = AnswerProfile(**values)
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_retrieval_revisions(
        self, application_id: UUID, environment_id: UUID, profile_id: UUID
    ) -> list[RetrievalProfileRevision]:
        """只列出当前应用环境中指定检索策略的修订，避免跨环境读取配置。"""
        result = await self.session.scalars(
            select(RetrievalProfileRevision)
            .where(
                RetrievalProfileRevision.application_id == application_id,
                RetrievalProfileRevision.environment_id == environment_id,
                RetrievalProfileRevision.profile_id == profile_id,
            )
            .order_by(RetrievalProfileRevision.revision.desc())
        )
        return list(result)

    async def list_answer_revisions(
        self, application_id: UUID, environment_id: UUID, profile_id: UUID
    ) -> list[AnswerProfileRevision]:
        """只列出当前应用环境中指定回答策略的修订，配置不跨租户泄露。"""
        result = await self.session.scalars(
            select(AnswerProfileRevision)
            .where(
                AnswerProfileRevision.application_id == application_id,
                AnswerProfileRevision.environment_id == environment_id,
                AnswerProfileRevision.profile_id == profile_id,
            )
            .order_by(AnswerProfileRevision.revision.desc())
        )
        return list(result)

    async def list_profile_releases(
        self, application_id: UUID, environment_id: UUID, profile_id: UUID
    ) -> list[ProfileRelease]:
        """返回单个策略的发布事件，回滚不会篡改此前记录。"""
        result = await self.session.scalars(
            select(ProfileRelease)
            .where(
                ProfileRelease.application_id == application_id,
                ProfileRelease.environment_id == environment_id,
                ProfileRelease.profile_id == profile_id,
            )
            .order_by(ProfileRelease.created_at.desc())
        )
        return list(result)

    async def get_retrieval_revision(
        self, application_id: UUID, environment_id: UUID, revision_id: UUID
    ) -> RetrievalProfileRevision | None:
        """读取当前上下文内的检索修订，供回答草稿绑定和发布操作复用。"""
        return await self.session.scalar(
            select(RetrievalProfileRevision).where(
                RetrievalProfileRevision.application_id == application_id,
                RetrievalProfileRevision.environment_id == environment_id,
                RetrievalProfileRevision.id == revision_id,
            )
        )

    async def get_answer_revision(
        self, application_id: UUID, environment_id: UUID, revision_id: UUID
    ) -> AnswerProfileRevision | None:
        """读取当前上下文内的回答修订，拒绝跨应用或环境发布。"""
        return await self.session.scalar(
            select(AnswerProfileRevision).where(
                AnswerProfileRevision.application_id == application_id,
                AnswerProfileRevision.environment_id == environment_id,
                AnswerProfileRevision.id == revision_id,
            )
        )

    async def create_retrieval_draft(
        self, profile: RetrievalProfile, configuration: dict[str, object]
    ) -> RetrievalProfileRevision:
        """追加新的检索草稿版本，历史修订不更新或覆盖。"""
        revisions = await self.list_retrieval_revisions(
            profile.application_id, profile.environment_id, profile.id
        )
        row = RetrievalProfileRevision(
            application_id=profile.application_id,
            environment_id=profile.environment_id,
            profile_id=profile.id,
            revision=(max((item.revision for item in revisions), default=0) + 1),
            state="draft",
            configuration=configuration,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def create_answer_draft(
        self,
        profile: AnswerProfile,
        retrieval_revision_id: UUID,
        configuration: dict[str, object],
    ) -> AnswerProfileRevision:
        """追加新的回答草稿，并将其永久关联到指定检索修订。"""
        revisions = await self.list_answer_revisions(
            profile.application_id, profile.environment_id, profile.id
        )
        row = AnswerProfileRevision(
            application_id=profile.application_id,
            environment_id=profile.environment_id,
            profile_id=profile.id,
            retrieval_revision_id=retrieval_revision_id,
            revision=(max((item.revision for item in revisions), default=0) + 1),
            state="draft",
            configuration=configuration,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def update_retrieval_draft(
        self, row: RetrievalProfileRevision, configuration: dict[str, object]
    ) -> None:
        """只允许更新 draft 内容；一旦发布，配置快照永久冻结。"""
        if row.state != "draft":
            raise ValueError("只有 draft 检索修订可以编辑")
        row.configuration = configuration
        row.updated_at = datetime.now(UTC)

    async def update_answer_draft(
        self,
        row: AnswerProfileRevision,
        retrieval_revision_id: UUID,
        configuration: dict[str, object],
    ) -> None:
        """更新回答草稿及其检索版本绑定，发布后的版本不可再编辑。"""
        if row.state != "draft":
            raise ValueError("只有 draft 回答修订可以编辑")
        row.retrieval_revision_id = retrieval_revision_id
        row.configuration = configuration
        row.updated_at = datetime.now(UTC)

    async def publish_retrieval_revision(self, row: RetrievalProfileRevision) -> None:
        """仅将草稿转换为发布态；先前发布版本仍由事件历史保留。"""
        if row.state != "draft":
            raise ValueError("只有 draft 检索修订可以发布")
        row.state = "published"
        row.published_at = datetime.now(UTC)

    async def publish_answer_revision(self, row: AnswerProfileRevision) -> None:
        """仅将草稿转换为发布态；回滚不重写此前发布的配置快照。"""
        if row.state != "draft":
            raise ValueError("只有 draft 回答修订可以发布")
        row.state = "published"
        row.published_at = datetime.now(UTC)

    async def append_profile_release(
        self,
        *,
        application_id: UUID,
        environment_id: UUID,
        profile_kind: str,
        profile_id: UUID,
        revision_id: UUID,
        action: str,
    ) -> ProfileRelease:
        """追加不可变发布事件；当前生效修订由最近事件而不是覆写旧事件决定。"""
        row = ProfileRelease(
            application_id=application_id,
            environment_id=environment_id,
            profile_kind=profile_kind,
            profile_id=profile_id,
            revision_id=revision_id,
            action=action,
        )
        self.session.add(row)
        await self.session.flush()
        return row
