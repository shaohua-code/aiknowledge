"""管理员维护固定评测集和样本修订的控制面路由。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.control.auth import require_admin
from knowledge_core.domains.applications.repository import ApplicationRepository
from knowledge_core.domains.evaluations.repository import EvaluationRepository
from knowledge_core.domains.evaluations.schemas import (
    BulkCaseImportRequest,
    BulkCaseImportResponse,
    EvaluationCaseCreate,
    EvaluationCaseResultView,
    EvaluationCaseRevisionView,
    EvaluationRunCreate,
    EvaluationRunView,
    EvaluationSetCreate,
    EvaluationSetView,
)
from knowledge_core.domains.evaluations.service import EvaluationService
from knowledge_core.infrastructure.database import get_session
from knowledge_core.infrastructure.models import EvaluationCaseRevision, EvaluationSet
from knowledge_core.shared.errors import ConflictError, NotFoundError
from knowledge_core.shared.response import success
from knowledge_core.shared.schema import ApiSuccess

router = APIRouter(
    prefix="/control/v1/applications/{application_id}/environments/{environment_id}/evaluations",
    tags=["质量评测"],
    dependencies=[Depends(require_admin)],
)


async def _ensure_environment(
    session: AsyncSession, application_id: UUID, environment_id: UUID
) -> None:
    """评测数据和运行策略必须共享同一应用环境。"""
    if not await ApplicationRepository(session).get_environment(application_id, environment_id):
        raise NotFoundError()


@router.get("/sets", response_model=ApiSuccess[list[EvaluationSetView]])
async def list_evaluation_sets(
    application_id: UUID,
    environment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _ensure_environment(session, application_id, environment_id)
    rows = await EvaluationRepository(session).list_sets(application_id, environment_id)
    return success(
        [EvaluationSetView.model_validate(row).model_dump(by_alias=True) for row in rows]
    )


@router.post("/sets", status_code=201, response_model=ApiSuccess[EvaluationSetView])
async def create_evaluation_set(
    application_id: UUID,
    environment_id: UUID,
    payload: EvaluationSetCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _ensure_environment(session, application_id, environment_id)
    existing = await session.scalar(
        select(EvaluationSet).where(
            EvaluationSet.application_id == application_id,
            EvaluationSet.environment_id == environment_id,
            EvaluationSet.code == payload.code,
        )
    )
    if existing:
        raise ConflictError("评测集编码已存在")
    row = EvaluationSet(
        application_id=application_id,
        environment_id=environment_id,
        status="active",
        **payload.model_dump(),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return success(EvaluationSetView.model_validate(row).model_dump(by_alias=True))


@router.get("/sets/{set_id}/cases", response_model=ApiSuccess[list[EvaluationCaseRevisionView]])
async def list_evaluation_cases(
    application_id: UUID,
    environment_id: UUID,
    set_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _ensure_environment(session, application_id, environment_id)
    if not await EvaluationRepository(session).get_set(application_id, environment_id, set_id):
        raise NotFoundError("评测集不存在")
    rows = await EvaluationRepository(session).list_case_revisions(
        application_id, environment_id, set_id
    )
    return success(
        [EvaluationCaseRevisionView.model_validate(row).model_dump(by_alias=True) for row in rows]
    )


@router.post(
    "/sets/{set_id}/cases", status_code=201, response_model=ApiSuccess[EvaluationCaseRevisionView]
)
async def create_evaluation_case(
    application_id: UUID,
    environment_id: UUID,
    set_id: UUID,
    payload: EvaluationCaseCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _ensure_environment(session, application_id, environment_id)
    if not await EvaluationRepository(session).get_set(application_id, environment_id, set_id):
        raise NotFoundError("评测集不存在")
    existing = await session.scalar(
        select(EvaluationCaseRevision).where(
            EvaluationCaseRevision.application_id == application_id,
            EvaluationCaseRevision.environment_id == environment_id,
            EvaluationCaseRevision.evaluation_set_id == set_id,
            EvaluationCaseRevision.case_code == payload.case_code,
        )
    )
    if existing:
        raise ConflictError("样本编码已存在；请通过后续修订接口更新")
    values = payload.model_dump()
    values["expected_document_ids"] = [str(item) for item in payload.expected_document_ids]
    row = EvaluationCaseRevision(
        application_id=application_id,
        environment_id=environment_id,
        evaluation_set_id=set_id,
        revision=1,
        **values,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return success(EvaluationCaseRevisionView.model_validate(row).model_dump(by_alias=True))


@router.post(
    "/sets/{set_id}/cases/{case_code}/revisions",
    status_code=201,
    response_model=ApiSuccess[EvaluationCaseRevisionView],
)
async def create_evaluation_case_revision(
    application_id: UUID,
    environment_id: UUID,
    set_id: UUID,
    case_code: str,
    payload: EvaluationCaseCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """修改样本时追加修订，路径编码必须与正文一致以避免误建新 case。"""
    await _ensure_environment(session, application_id, environment_id)
    if payload.case_code != case_code:
        raise ConflictError("路径 caseCode 与请求体 caseCode 不一致")
    repository = EvaluationRepository(session)
    if not await repository.get_set(application_id, environment_id, set_id):
        raise NotFoundError("评测集不存在")
    previous = await repository.get_latest_case_revision(
        application_id, environment_id, set_id, case_code
    )
    if previous is None:
        raise NotFoundError("评测样本不存在")
    values = payload.model_dump()
    values["expected_document_ids"] = [str(item) for item in payload.expected_document_ids]
    row = EvaluationCaseRevision(
        application_id=application_id,
        environment_id=environment_id,
        evaluation_set_id=set_id,
        revision=previous.revision + 1,
        **values,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return success(EvaluationCaseRevisionView.model_validate(row).model_dump(by_alias=True))


@router.get("/sets/{set_id}/runs", response_model=ApiSuccess[list[EvaluationRunView]])
async def list_evaluation_runs(
    application_id: UUID,
    environment_id: UUID,
    set_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """查看一个评测集的完整运行历史，而不是只显示最近一次请求的内存结果。"""
    await _ensure_environment(session, application_id, environment_id)
    repository = EvaluationRepository(session)
    if not await repository.get_set(application_id, environment_id, set_id):
        raise NotFoundError("评测集不存在")
    rows = await repository.list_runs(application_id, environment_id, set_id)
    return success(
        [EvaluationRunView.model_validate(row).model_dump(by_alias=True) for row in rows]
    )


@router.get("/runs/{run_id}/results", response_model=ApiSuccess[list[EvaluationCaseResultView]])
async def list_evaluation_case_results(
    application_id: UUID,
    environment_id: UUID,
    run_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """返回运行的逐样本结论；不存在或跨环境的运行统一视为不存在。"""
    await _ensure_environment(session, application_id, environment_id)
    repository = EvaluationRepository(session)
    if not await repository.get_run(application_id, environment_id, run_id):
        raise NotFoundError("评测运行不存在")
    rows = await repository.list_case_results(application_id, environment_id, run_id)
    return success(
        [EvaluationCaseResultView.model_validate(row).model_dump(by_alias=True) for row in rows]
    )


@router.post(
    "/sets/{set_id}/cases/bulk",
    status_code=201,
    response_model=ApiSuccess[BulkCaseImportResponse],
)
async def bulk_import_cases(
    application_id: UUID,
    environment_id: UUID,
    set_id: UUID,
    payload: BulkCaseImportRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """批量导入评测样本，支持从 JSON 数组快速创建示例数据。

    适用于：
    - 导入示例评测集
    - 从旧系统迁移样本
    - 批量添加测试用例

    默认跳过已存在的样本编码，避免重复创建冲突。
    """
    await _ensure_environment(session, application_id, environment_id)
    if not await EvaluationRepository(session).get_set(application_id, environment_id, set_id):
        raise NotFoundError("评测集不存在")

    imported_codes: list[str] = []
    skipped_count = 0

    for case_data in payload.cases:
        existing = await session.scalar(
            select(EvaluationCaseRevision).where(
                EvaluationCaseRevision.application_id == application_id,
                EvaluationCaseRevision.environment_id == environment_id,
                EvaluationCaseRevision.evaluation_set_id == set_id,
                EvaluationCaseRevision.case_code == case_data.case_code,
            )
        )
        if existing:
            if payload.skip_existing:
                skipped_count += 1
                continue
            raise ConflictError(f"样本编码已存在: {case_data.case_code}")

        values = case_data.model_dump()
        values["expected_document_ids"] = [str(item) for item in case_data.expected_document_ids]
        row = EvaluationCaseRevision(
            application_id=application_id,
            environment_id=environment_id,
            evaluation_set_id=set_id,
            revision=1,
            **values,
        )
        session.add(row)
        imported_codes.append(case_data.case_code)

    await session.commit()
    return success({
        "imported": len(imported_codes),
        "skipped": skipped_count,
        "caseCodes": imported_codes,
    })


@router.post("/sets/{set_id}/runs", status_code=201, response_model=ApiSuccess[EvaluationRunView])
async def run_evaluation_set(
    application_id: UUID,
    environment_id: UUID,
    set_id: UUID,
    payload: EvaluationRunCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """同步执行固定样本；每个结果会持久化，Provider 故障只会形成失败门禁。"""
    application_repository = ApplicationRepository(session)
    environment = await application_repository.get_environment(application_id, environment_id)
    application = await application_repository.get_application(application_id)
    if environment is None or application is None:
        raise NotFoundError()
    if not await EvaluationRepository(session).get_set(application_id, environment_id, set_id):
        raise NotFoundError("评测集不存在")
    from knowledge_core.shared.context import ApplicationContext

    # 评测不携带业务 API Key；仅为复用同一服务创建不可外泄的内部上下文。
    context = ApplicationContext(
        application_id=application_id,
        environment_id=environment_id,
        application_code=application.code,
        environment_code=environment.code,
        api_key_id=UUID(int=0),
        scopes=frozenset({"answer:run", "knowledge:read"}),
    )
    try:
        row = await EvaluationService(session).run(
            context, set_id, payload.answer_profile_revision_id
        )
    except ValueError as exc:
        raise ConflictError(str(exc)) from exc
    return success(EvaluationRunView.model_validate(row).model_dump(by_alias=True))
