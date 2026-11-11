"""AI 能力控制面。

检索草稿可携带可选 ragEnhancement。未传时不写入快照，运行面保持原检索。
策略发布和回滚成功后投递 Webhook；没有订阅或投递失败不影响发布结果。
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.control.auth import (
    require_admin,
    require_control_permission,
    require_permission,
)
from knowledge_core.control.permissions import Permission
from knowledge_core.domains.applications.repository import ApplicationRepository
from knowledge_core.domains.evaluations.repository import EvaluationRepository
from knowledge_core.domains.intelligence.repository import IntelligenceRepository
from knowledge_core.domains.intelligence.schemas import (
    AnswerDebugRequest,
    AnswerOptions,
    AnswerProfileCreate,
    AnswerProfileRevisionDraft,
    AnswerProfileView,
    AnswerRequest,
    AnswerResponse,
    ProfileReleaseView,
    ProfileRevisionView,
    RetrievalProfileCreate,
    RetrievalProfileRevisionDraft,
    RetrievalProfileView,
)
from knowledge_core.domains.intelligence.service import AnswerService
from knowledge_core.domains.knowledge.repository import KnowledgeRepository
from knowledge_core.infrastructure.database import get_session
from knowledge_core.infrastructure.models import AnswerProfile, RetrievalProfile
from knowledge_core.shared.context import ApplicationContext
from knowledge_core.shared.errors import ConflictError, NotFoundError
from knowledge_core.shared.response import success
from knowledge_core.shared.schema import ApiSuccess

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/control/v1/applications/{application_id}/environments/{environment_id}",
    tags=["AI 能力"],
    dependencies=[
        Depends(require_admin),
        Depends(require_control_permission(Permission.PROFILE_READ, Permission.PROFILE_WRITE)),
    ],
)


async def _ensure_environment(
    session: AsyncSession, application_id: UUID, environment_id: UUID
) -> None:
    if not await ApplicationRepository(session).get_environment(application_id, environment_id):
        raise NotFoundError()


def _retrieval_draft_configuration(profile, payload: RetrievalProfileRevisionDraft) -> dict:
    """组装检索草稿快照。

    ragEnhancement 缺省时不写入，旧快照的必填字段和运行面行为保持不变。
    """
    configuration = payload.model_dump()
    rag = configuration.pop("rag_enhancement", None)
    configuration["code"] = profile.code
    configuration["name"] = profile.name
    configuration["collection_ids"] = [str(item) for item in payload.collection_ids]
    if isinstance(rag, dict):
        configuration["ragEnhancement"] = rag
    return configuration


async def _notify_profile_release(
    *,
    action: str,
    application_id: UUID,
    environment_id: UUID,
    profile_kind: str,
    profile_id: UUID,
    revision_id: UUID,
    revision: int,
) -> None:
    """发布或回滚成功后通知订阅方。投递失败只记日志。"""
    try:
        from knowledge_core.infrastructure.webhooks import (
            create_profile_published_event,
            create_profile_rolled_back_event,
            notify_configured_webhooks,
        )

        factory = (
            create_profile_published_event
            if action == "published"
            else create_profile_rolled_back_event
        )
        await notify_configured_webhooks(
            factory(
                application_id,
                environment_id,
                profile_kind,
                profile_id,
                revision_id,
                revision,
            )
        )
    except Exception:
        logger.warning("策略 %s Webhook 投递失败，发布结果已保留", action, exc_info=True)


@router.get("/retrieval-profiles", response_model=ApiSuccess[list[RetrievalProfileView]])
async def list_retrieval_profiles(
    application_id: UUID,
    environment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _ensure_environment(session, application_id, environment_id)
    rows = await IntelligenceRepository(session).list_retrieval_profiles(
        application_id, environment_id
    )
    return success(
        [RetrievalProfileView.model_validate(row).model_dump(by_alias=True) for row in rows]
    )


@router.post(
    "/retrieval-profiles",
    status_code=201,
    response_model=ApiSuccess[RetrievalProfileView],
)
async def create_retrieval_profile(
    application_id: UUID,
    environment_id: UUID,
    payload: RetrievalProfileCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _ensure_environment(session, application_id, environment_id)
    repository = IntelligenceRepository(session)
    existing = await session.scalar(
        select(RetrievalProfile).where(
            RetrievalProfile.application_id == application_id,
            RetrievalProfile.environment_id == environment_id,
            RetrievalProfile.code == payload.code,
        )
    )
    if existing:
        raise ConflictError("检索策略编码已存在")
    knowledge = KnowledgeRepository(session)
    for collection_id in payload.collection_ids:
        if not await knowledge.get_collection(application_id, environment_id, collection_id):
            raise NotFoundError("检索策略引用了不存在的知识集合")
    values = payload.model_dump()
    values["collection_ids"] = [str(value) for value in payload.collection_ids]
    row = await repository.create_retrieval_profile(
        application_id=application_id,
        environment_id=environment_id,
        status="active",
        **values,
    )
    await session.commit()
    await session.refresh(row)
    return success(RetrievalProfileView.model_validate(row).model_dump(by_alias=True))


@router.get("/answer-profiles", response_model=ApiSuccess[list[AnswerProfileView]])
async def list_answer_profiles(
    application_id: UUID,
    environment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _ensure_environment(session, application_id, environment_id)
    rows = await IntelligenceRepository(session).list_answer_profiles(
        application_id, environment_id
    )
    return success(
        [AnswerProfileView.model_validate(row).model_dump(by_alias=True) for row in rows]
    )


@router.post(
    "/answer-profiles",
    status_code=201,
    response_model=ApiSuccess[AnswerProfileView],
)
async def create_answer_profile(
    application_id: UUID,
    environment_id: UUID,
    payload: AnswerProfileCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _ensure_environment(session, application_id, environment_id)
    repository = IntelligenceRepository(session)
    if not await repository.get_retrieval_profile(
        application_id, environment_id, payload.retrieval_profile_id
    ):
        raise NotFoundError("回答策略引用的检索策略不存在")
    existing = await session.scalar(
        select(AnswerProfile).where(
            AnswerProfile.application_id == application_id,
            AnswerProfile.environment_id == environment_id,
            AnswerProfile.code == payload.code,
        )
    )
    if existing:
        raise ConflictError("回答策略编码已存在")
    row = await repository.create_answer_profile(
        application_id=application_id,
        environment_id=environment_id,
        status="active",
        **payload.model_dump(),
    )
    await session.commit()
    await session.refresh(row)
    return success(AnswerProfileView.model_validate(row).model_dump(by_alias=True))


@router.post(
    "/answer-profiles/{profile_id}/debug",
    response_model=ApiSuccess[AnswerResponse],
)
async def debug_answer_profile(
    application_id: UUID,
    environment_id: UUID,
    profile_id: UUID,
    payload: AnswerDebugRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """以管理员会话调试当前已发布回答策略，不要求或接收业务 API Key。"""
    application_repository = ApplicationRepository(session)
    application = await application_repository.get_application(application_id)
    environment = await application_repository.get_environment(application_id, environment_id)
    if application is None or environment is None:
        raise NotFoundError()
    profile = await IntelligenceRepository(session).get_answer_profile(
        application_id, environment_id, profile_id
    )
    if profile is None:
        raise NotFoundError("回答策略不存在")
    # 内部上下文仅复用同一领域服务和轨迹模型；不会具备或代替任何业务 API Key。
    context = ApplicationContext(
        application_id=application_id,
        environment_id=environment_id,
        application_code=application.code,
        environment_code=environment.code,
        api_key_id=UUID(int=0),
        scopes=frozenset({"answer:run", "knowledge:read"}),
    )
    result = await AnswerService(session).answer(
        context,
        AnswerRequest(
            profile=profile.code,
            query=payload.query,
            inputs=payload.inputs,
            # 调试必须返回证据和引用，避免管理端无法解释同一次真实回答。
            options=AnswerOptions(include_citations=True, include_evidence=True),
        ),
    )
    return success(result)


@router.get(
    "/retrieval-profiles/{profile_id}/revisions",
    response_model=ApiSuccess[list[ProfileRevisionView]],
)
async def list_retrieval_profile_revisions(
    application_id: UUID,
    environment_id: UUID,
    profile_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """在读取修订前验证策略归属，避免仅靠 UUID 枚举其他环境配置。"""
    await _ensure_environment(session, application_id, environment_id)
    repository = IntelligenceRepository(session)
    if not await repository.get_retrieval_profile(application_id, environment_id, profile_id):
        raise NotFoundError("检索策略不存在")
    rows = await repository.list_retrieval_revisions(application_id, environment_id, profile_id)
    return success(
        [ProfileRevisionView.model_validate(row).model_dump(by_alias=True) for row in rows]
    )


@router.get(
    "/answer-profiles/{profile_id}/revisions",
    response_model=ApiSuccess[list[ProfileRevisionView]],
)
async def list_answer_profile_revisions(
    application_id: UUID,
    environment_id: UUID,
    profile_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """回答修订同样先验证主策略归属，再返回管理员可见的配置快照。"""
    await _ensure_environment(session, application_id, environment_id)
    repository = IntelligenceRepository(session)
    if not await repository.get_answer_profile(application_id, environment_id, profile_id):
        raise NotFoundError("回答策略不存在")
    rows = await repository.list_answer_revisions(application_id, environment_id, profile_id)
    return success(
        [ProfileRevisionView.model_validate(row).model_dump(by_alias=True) for row in rows]
    )


@router.get(
    "/profile-releases/{profile_id}",
    response_model=ApiSuccess[list[ProfileReleaseView]],
)
async def list_profile_releases(
    application_id: UUID,
    environment_id: UUID,
    profile_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """发布事件支持跨策略类型查询，但只能访问当前应用环境的事件。"""
    await _ensure_environment(session, application_id, environment_id)
    rows = await IntelligenceRepository(session).list_profile_releases(
        application_id, environment_id, profile_id
    )
    return success(
        [ProfileReleaseView.model_validate(row).model_dump(by_alias=True) for row in rows]
    )


@router.post(
    "/retrieval-profiles/{profile_id}/revisions",
    status_code=201,
    response_model=ApiSuccess[ProfileRevisionView],
)
async def create_retrieval_profile_draft(
    application_id: UUID,
    environment_id: UUID,
    profile_id: UUID,
    payload: RetrievalProfileRevisionDraft,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """新检索配置只追加 draft 修订，主策略和历史发布版本均不改写。"""
    await _ensure_environment(session, application_id, environment_id)
    repository = IntelligenceRepository(session)
    profile = await repository.get_retrieval_profile(application_id, environment_id, profile_id)
    if profile is None:
        raise NotFoundError("检索策略不存在")
    knowledge = KnowledgeRepository(session)
    for collection_id in payload.collection_ids:
        if not await knowledge.get_collection(application_id, environment_id, collection_id):
            raise NotFoundError("检索策略草稿引用了不存在的知识集合")
    configuration = _retrieval_draft_configuration(profile, payload)
    row = await repository.create_retrieval_draft(profile, configuration)
    await session.commit()
    await session.refresh(row)
    return success(ProfileRevisionView.model_validate(row).model_dump(by_alias=True))


@router.post(
    "/answer-profiles/{profile_id}/revisions",
    status_code=201,
    response_model=ApiSuccess[ProfileRevisionView],
)
async def create_answer_profile_draft(
    application_id: UUID,
    environment_id: UUID,
    profile_id: UUID,
    payload: AnswerProfileRevisionDraft,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """回答草稿只能绑定当前环境的检索修订，防止跨环境组合策略。"""
    await _ensure_environment(session, application_id, environment_id)
    repository = IntelligenceRepository(session)
    profile = await repository.get_answer_profile(application_id, environment_id, profile_id)
    if profile is None:
        raise NotFoundError("回答策略不存在")
    retrieval_revision = await repository.get_retrieval_revision(
        application_id, environment_id, payload.retrieval_revision_id
    )
    if retrieval_revision is None:
        raise NotFoundError("回答策略草稿引用的检索修订不存在")
    configuration = payload.model_dump(exclude={"retrieval_revision_id"})
    configuration["code"] = profile.code
    configuration["name"] = profile.name
    configuration["retrieval_profile_id"] = str(retrieval_revision.profile_id)
    row = await repository.create_answer_draft(profile, retrieval_revision.id, configuration)
    await session.commit()
    await session.refresh(row)
    return success(ProfileRevisionView.model_validate(row).model_dump(by_alias=True))


@router.put(
    "/retrieval-profiles/{profile_id}/revisions/{revision_id}",
    response_model=ApiSuccess[ProfileRevisionView],
)
async def update_retrieval_profile_draft(
    application_id: UUID,
    environment_id: UUID,
    profile_id: UUID,
    revision_id: UUID,
    payload: RetrievalProfileRevisionDraft,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """字段级保存检索草稿；发布态或跨策略修订一律拒绝修改。"""
    await _ensure_environment(session, application_id, environment_id)
    repository = IntelligenceRepository(session)
    profile = await repository.get_retrieval_profile(application_id, environment_id, profile_id)
    revision = await repository.get_retrieval_revision(application_id, environment_id, revision_id)
    if profile is None or revision is None or revision.profile_id != profile_id:
        raise NotFoundError("检索策略草稿不存在")
    knowledge = KnowledgeRepository(session)
    for collection_id in payload.collection_ids:
        if not await knowledge.get_collection(application_id, environment_id, collection_id):
            raise NotFoundError("检索策略草稿引用了不存在的知识集合")
    configuration = _retrieval_draft_configuration(profile, payload)
    try:
        await repository.update_retrieval_draft(revision, configuration)
    except ValueError as exc:
        raise ConflictError(str(exc)) from exc
    await session.commit()
    await session.refresh(revision)
    return success(ProfileRevisionView.model_validate(revision).model_dump(by_alias=True))


@router.put(
    "/answer-profiles/{profile_id}/revisions/{revision_id}",
    response_model=ApiSuccess[ProfileRevisionView],
)
async def update_answer_profile_draft(
    application_id: UUID,
    environment_id: UUID,
    profile_id: UUID,
    revision_id: UUID,
    payload: AnswerProfileRevisionDraft,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """字段级保存回答草稿，并在保存时重新验证检索修订归属。"""
    await _ensure_environment(session, application_id, environment_id)
    repository = IntelligenceRepository(session)
    profile = await repository.get_answer_profile(application_id, environment_id, profile_id)
    revision = await repository.get_answer_revision(application_id, environment_id, revision_id)
    if profile is None or revision is None or revision.profile_id != profile_id:
        raise NotFoundError("回答策略草稿不存在")
    retrieval_revision = await repository.get_retrieval_revision(
        application_id, environment_id, payload.retrieval_revision_id
    )
    if retrieval_revision is None:
        raise NotFoundError("回答策略草稿引用的检索修订不存在")
    configuration = payload.model_dump(exclude={"retrieval_revision_id"})
    configuration["code"] = profile.code
    configuration["name"] = profile.name
    configuration["retrieval_profile_id"] = str(retrieval_revision.profile_id)
    try:
        await repository.update_answer_draft(revision, retrieval_revision.id, configuration)
    except ValueError as exc:
        raise ConflictError(str(exc)) from exc
    await session.commit()
    await session.refresh(revision)
    return success(ProfileRevisionView.model_validate(revision).model_dump(by_alias=True))


@router.post(
    "/retrieval-profiles/{profile_id}/revisions/{revision_id}/publish",
    response_model=ApiSuccess[ProfileReleaseView],
)
async def publish_retrieval_profile_revision(
    application_id: UUID,
    environment_id: UUID,
    profile_id: UUID,
    revision_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[object, Depends(require_permission(Permission.PROFILE_PUBLISH))],
) -> dict:
    """发布草稿并追加事件；运行时随后可按最近发布事件解析此修订。"""
    await _ensure_environment(session, application_id, environment_id)
    repository = IntelligenceRepository(session)
    revision = await repository.get_retrieval_revision(application_id, environment_id, revision_id)
    if revision is None or revision.profile_id != profile_id:
        raise NotFoundError("检索策略修订不存在")
    try:
        await repository.publish_retrieval_revision(revision)
    except ValueError as exc:
        raise ConflictError(str(exc)) from exc
    release = await repository.append_profile_release(
        application_id=application_id,
        environment_id=environment_id,
        profile_kind="retrieval",
        profile_id=profile_id,
        revision_id=revision.id,
        action="published",
    )
    await session.commit()
    await session.refresh(release)
    await _notify_profile_release(
        action="published",
        application_id=application_id,
        environment_id=environment_id,
        profile_kind="retrieval",
        profile_id=profile_id,
        revision_id=revision.id,
        revision=revision.revision,
    )
    return success(ProfileReleaseView.model_validate(release).model_dump(by_alias=True))


@router.post(
    "/answer-profiles/{profile_id}/revisions/{revision_id}/publish",
    response_model=ApiSuccess[ProfileReleaseView],
)
async def publish_answer_profile_revision(
    application_id: UUID,
    environment_id: UUID,
    profile_id: UUID,
    revision_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[object, Depends(require_permission(Permission.PROFILE_PUBLISH))],
) -> dict:
    """发布回答草稿；其已钉住的检索修订不在发布过程中被替换。"""
    await _ensure_environment(session, application_id, environment_id)
    repository = IntelligenceRepository(session)
    revision = await repository.get_answer_revision(application_id, environment_id, revision_id)
    if revision is None or revision.profile_id != profile_id:
        raise NotFoundError("回答策略修订不存在")
    if revision.retrieval_revision_id is None or not await EvaluationRepository(
        session
    ).has_passing_run(
        application_id,
        environment_id,
        revision.id,
        revision.retrieval_revision_id,
    ):
        raise ConflictError("回答策略发布需要同一修订组合的通过评测运行")
    try:
        await repository.publish_answer_revision(revision)
    except ValueError as exc:
        raise ConflictError(str(exc)) from exc
    release = await repository.append_profile_release(
        application_id=application_id,
        environment_id=environment_id,
        profile_kind="answer",
        profile_id=profile_id,
        revision_id=revision.id,
        action="published",
    )
    await session.commit()
    await session.refresh(release)
    await _notify_profile_release(
        action="published",
        application_id=application_id,
        environment_id=environment_id,
        profile_kind="answer",
        profile_id=profile_id,
        revision_id=revision.id,
        revision=revision.revision,
    )
    return success(ProfileReleaseView.model_validate(release).model_dump(by_alias=True))


@router.post(
    "/retrieval-profiles/{profile_id}/revisions/{revision_id}/rollback",
    response_model=ApiSuccess[ProfileReleaseView],
)
async def rollback_retrieval_profile_revision(
    application_id: UUID,
    environment_id: UUID,
    profile_id: UUID,
    revision_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[object, Depends(require_permission(Permission.PROFILE_ROLLBACK))],
) -> dict:
    """回滚只追加指向历史已发布修订的事件，不改写任何旧配置。"""
    await _ensure_environment(session, application_id, environment_id)
    repository = IntelligenceRepository(session)
    revision = await repository.get_retrieval_revision(application_id, environment_id, revision_id)
    if revision is None or revision.profile_id != profile_id or revision.state != "published":
        raise NotFoundError("可回滚的检索策略修订不存在")
    release = await repository.append_profile_release(
        application_id=application_id,
        environment_id=environment_id,
        profile_kind="retrieval",
        profile_id=profile_id,
        revision_id=revision.id,
        action="rolled_back",
    )
    await session.commit()
    await session.refresh(release)
    await _notify_profile_release(
        action="rolled_back",
        application_id=application_id,
        environment_id=environment_id,
        profile_kind="retrieval",
        profile_id=profile_id,
        revision_id=revision.id,
        revision=revision.revision,
    )
    return success(ProfileReleaseView.model_validate(release).model_dump(by_alias=True))


@router.post(
    "/answer-profiles/{profile_id}/revisions/{revision_id}/rollback",
    response_model=ApiSuccess[ProfileReleaseView],
)
async def rollback_answer_profile_revision(
    application_id: UUID,
    environment_id: UUID,
    profile_id: UUID,
    revision_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[object, Depends(require_permission(Permission.PROFILE_ROLLBACK))],
) -> dict:
    """回答回滚遵循同一事件追加规则，旧回答快照永远保持可审计。"""
    await _ensure_environment(session, application_id, environment_id)
    repository = IntelligenceRepository(session)
    revision = await repository.get_answer_revision(application_id, environment_id, revision_id)
    if revision is None or revision.profile_id != profile_id or revision.state != "published":
        raise NotFoundError("可回滚的回答策略修订不存在")
    release = await repository.append_profile_release(
        application_id=application_id,
        environment_id=environment_id,
        profile_kind="answer",
        profile_id=profile_id,
        revision_id=revision.id,
        action="rolled_back",
    )
    await session.commit()
    await session.refresh(release)
    await _notify_profile_release(
        action="rolled_back",
        application_id=application_id,
        environment_id=environment_id,
        profile_kind="answer",
        profile_id=profile_id,
        revision_id=revision.id,
        revision=revision.revision,
    )
    return success(ProfileReleaseView.model_validate(release).model_dump(by_alias=True))
