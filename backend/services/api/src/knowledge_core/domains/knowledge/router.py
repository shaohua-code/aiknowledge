from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.control.auth import require_admin
from knowledge_core.domains.applications.repository import ApplicationRepository
from knowledge_core.domains.knowledge.repository import KnowledgeRepository
from knowledge_core.domains.knowledge.schemas import (
    CollectionCreate,
    CollectionView,
    DocumentView,
    IngestionRunView,
    RemoteDocumentCreate,
    RemotePreviewView,
    TextDocumentCreate,
    TextRevisionCreate,
)
from knowledge_core.domains.knowledge.service import KnowledgeService
from knowledge_core.infrastructure.database import get_session
from knowledge_core.shared.errors import NotFoundError
from knowledge_core.shared.response import success

router = APIRouter(
    prefix="/control/v1/applications/{application_id}/environments/{environment_id}",
    tags=["知识中心"],
    dependencies=[Depends(require_admin)],
)


async def _ensure_environment(
    session: AsyncSession, application_id: UUID, environment_id: UUID
) -> None:
    if not await ApplicationRepository(session).get_environment(application_id, environment_id):
        raise NotFoundError()


@router.get("/collections")
async def list_collections(
    application_id: UUID,
    environment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _ensure_environment(session, application_id, environment_id)
    rows = await KnowledgeRepository(session).list_collections(application_id, environment_id)
    return success([CollectionView.model_validate(row).model_dump(by_alias=True) for row in rows])


@router.post("/collections", status_code=201)
async def create_collection(
    application_id: UUID,
    environment_id: UUID,
    payload: CollectionCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _ensure_environment(session, application_id, environment_id)
    row = await KnowledgeService(session).create_collection(application_id, environment_id, payload)
    return success(CollectionView.model_validate(row).model_dump(by_alias=True))


@router.get("/collections/{collection_id}/documents")
async def list_documents(
    application_id: UUID,
    environment_id: UUID,
    collection_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    repository = KnowledgeRepository(session)
    if not await repository.get_collection(application_id, environment_id, collection_id):
        raise NotFoundError()
    rows = await repository.list_documents(application_id, environment_id, collection_id)
    return success([DocumentView.model_validate(row).model_dump(by_alias=True) for row in rows])


@router.post("/collections/{collection_id}/documents/upload", status_code=202)
async def upload_document(
    application_id: UUID,
    environment_id: UUID,
    collection_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    file: Annotated[UploadFile, File()],
) -> dict:
    document, run = await KnowledgeService(session).create_file_document(
        application_id, environment_id, collection_id, file
    )
    return success(
        {
            "document": DocumentView.model_validate(document).model_dump(by_alias=True),
            "ingestionRun": IngestionRunView.model_validate(run).model_dump(by_alias=True),
        }
    )


@router.post("/collections/{collection_id}/documents/text", status_code=202)
async def create_text_document(
    application_id: UUID,
    environment_id: UUID,
    collection_id: UUID,
    payload: TextDocumentCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    document, run = await KnowledgeService(session).create_text_document(
        application_id,
        environment_id,
        collection_id,
        title=payload.title,
        content=payload.content,
    )
    return success(
        {
            "document": DocumentView.model_validate(document).model_dump(by_alias=True),
            "ingestionRun": IngestionRunView.model_validate(run).model_dump(by_alias=True),
        }
    )


@router.post("/collections/{collection_id}/documents/remote", status_code=202)
async def create_remote_document(
    application_id: UUID,
    environment_id: UUID,
    collection_id: UUID,
    payload: RemoteDocumentCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    document, run = await KnowledgeService(session).create_remote_document(
        application_id,
        environment_id,
        collection_id,
        payload=payload,
    )
    return success(
        {
            "document": DocumentView.model_validate(document).model_dump(by_alias=True),
            "ingestionRun": IngestionRunView.model_validate(run).model_dump(by_alias=True),
        }
    )


@router.post("/collections/{collection_id}/documents/remote-preview")
async def preview_remote_document(
    application_id: UUID,
    environment_id: UUID,
    collection_id: UUID,
    payload: RemoteDocumentCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    repository = KnowledgeRepository(session)
    if not await repository.get_collection(application_id, environment_id, collection_id):
        raise NotFoundError("知识集合不存在")
    preview = await KnowledgeService(session).preview_remote_document(payload)
    return success(RemotePreviewView.model_validate(preview).model_dump(by_alias=True))


@router.get("/ingestion-runs")
async def list_ingestion_runs(
    application_id: UUID,
    environment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _ensure_environment(session, application_id, environment_id)
    rows = await KnowledgeRepository(session).list_runs(application_id, environment_id)
    return success([IngestionRunView.model_validate(row).model_dump(by_alias=True) for row in rows])


@router.post("/ingestion-runs/{run_id}/retry", status_code=202)
async def retry_ingestion_run(
    application_id: UUID,
    environment_id: UUID,
    run_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    run = await KnowledgeService(session).retry_run(application_id, environment_id, run_id)
    return success(IngestionRunView.model_validate(run).model_dump(by_alias=True))


@router.post("/documents/{document_id}/versions/text", status_code=202)
async def create_text_revision(
    application_id: UUID,
    environment_id: UUID,
    document_id: UUID,
    payload: TextRevisionCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    document, run = await KnowledgeService(session).create_text_revision(
        application_id, environment_id, document_id, payload.content
    )
    return success(
        {
            "document": DocumentView.model_validate(document).model_dump(by_alias=True),
            "ingestionRun": IngestionRunView.model_validate(run).model_dump(by_alias=True),
        }
    )


@router.post("/documents/{document_id}/versions/upload", status_code=202)
async def create_file_revision(
    application_id: UUID,
    environment_id: UUID,
    document_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    file: Annotated[UploadFile, File()],
) -> dict:
    document, run = await KnowledgeService(session).create_file_revision(
        application_id, environment_id, document_id, file
    )
    return success(
        {
            "document": DocumentView.model_validate(document).model_dump(by_alias=True),
            "ingestionRun": IngestionRunView.model_validate(run).model_dump(by_alias=True),
        }
    )


@router.post("/documents/{document_id}/refresh", status_code=202)
async def refresh_remote_document(
    application_id: UUID,
    environment_id: UUID,
    document_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    document, run = await KnowledgeService(session).refresh_remote_document(
        application_id, environment_id, document_id
    )
    return success(
        {
            "document": DocumentView.model_validate(document).model_dump(by_alias=True),
            "ingestionRun": IngestionRunView.model_validate(run).model_dump(by_alias=True),
        }
    )


@router.delete("/documents/{document_id}")
async def archive_document(
    application_id: UUID,
    environment_id: UUID,
    document_id: UUID,
    actor: Annotated[str, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await KnowledgeService(session).archive_document(
        application_id, environment_id, document_id, actor=actor
    )
    return success({"archived": True, "id": str(document_id)})


@router.delete("/collections/{collection_id}")
async def archive_collection(
    application_id: UUID,
    environment_id: UUID,
    collection_id: UUID,
    actor: Annotated[str, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await KnowledgeService(session).archive_collection(
        application_id, environment_id, collection_id, actor=actor
    )
    return success({"archived": True, "id": str(collection_id)})
