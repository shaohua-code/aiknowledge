from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.control.auth import require_admin
from knowledge_core.domains.applications.repository import ApplicationRepository
from knowledge_core.domains.intelligence.repository import IntelligenceRepository
from knowledge_core.domains.intelligence.schemas import (
    AnswerProfileCreate,
    AnswerProfileView,
    RetrievalProfileCreate,
    RetrievalProfileView,
)
from knowledge_core.domains.knowledge.repository import KnowledgeRepository
from knowledge_core.infrastructure.database import get_session
from knowledge_core.infrastructure.models import AnswerProfile, RetrievalProfile
from knowledge_core.shared.errors import ConflictError, NotFoundError
from knowledge_core.shared.response import success

router = APIRouter(
    prefix="/control/v1/applications/{application_id}/environments/{environment_id}",
    tags=["AI 能力"],
    dependencies=[Depends(require_admin)],
)


async def _ensure_environment(
    session: AsyncSession, application_id: UUID, environment_id: UUID
) -> None:
    if not await ApplicationRepository(session).get_environment(application_id, environment_id):
        raise NotFoundError()


@router.get("/retrieval-profiles")
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


@router.post("/retrieval-profiles", status_code=201)
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


@router.get("/answer-profiles")
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


@router.post("/answer-profiles", status_code=201)
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
