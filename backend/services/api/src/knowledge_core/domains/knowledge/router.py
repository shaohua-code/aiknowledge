from __future__ import annotations

import hashlib
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.control.auth import require_admin, require_control_permission
from knowledge_core.control.permissions import Permission
from knowledge_core.domains.applications.repository import ApplicationRepository
from knowledge_core.domains.knowledge.repository import KnowledgeRepository
from knowledge_core.domains.knowledge.schemas import (
    ArchiveResponse,
    CollectionCreate,
    CollectionView,
    DocumentChunkView,
    DocumentIngestionQueuedResponse,
    DocumentRevisionView,
    DocumentView,
    IngestionRunView,
    RemoteDocumentCreate,
    RemotePreviewView,
    TextDocumentCreate,
    TextRevisionCreate,
)
from knowledge_core.domains.knowledge.service import MAX_UPLOAD_BYTES, KnowledgeService
from knowledge_core.infrastructure.database import get_session
from knowledge_core.shared.errors import NotFoundError
from knowledge_core.shared.idempotency import (
    begin_idempotency,
    complete_idempotency,
    fingerprint_payload,
)
from knowledge_core.shared.response import success
from knowledge_core.shared.schema import ApiSuccess

router = APIRouter(
    prefix="/control/v1/applications/{application_id}/environments/{environment_id}",
    tags=["知识中心"],
    # 删除端点当前是归档，因此使用归档权限，而不是物理删除权限。
    dependencies=[
        Depends(require_admin),
        Depends(
            require_control_permission(
                Permission.KNOWLEDGE_READ,
                Permission.KNOWLEDGE_WRITE,
                delete_permission=Permission.KNOWLEDGE_ARCHIVE,
            )
        ),
    ],
)


async def _ensure_environment(
    session: AsyncSession, application_id: UUID, environment_id: UUID
) -> None:
    if not await ApplicationRepository(session).get_environment(application_id, environment_id):
        raise NotFoundError()


async def _file_fingerprint(file: UploadFile, *, operation: str, scope: dict[str, str]) -> str:
    """只读取文件摘要用于幂等判断，随后恢复文件位置供正式入库再次读取。"""
    position = file.file.tell()
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.seek(position)
    return fingerprint_payload(
        operation,
        {
            **scope,
            "filename": file.filename or "未命名文件",
            "contentType": file.content_type or "application/octet-stream",
            "size": len(content),
            "contentHash": hashlib.sha256(content).hexdigest(),
        },
    )


@router.get("/collections", response_model=ApiSuccess[list[CollectionView]])
async def list_collections(
    application_id: UUID,
    environment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _ensure_environment(session, application_id, environment_id)
    rows = await KnowledgeRepository(session).list_collections(application_id, environment_id)
    return success([CollectionView.model_validate(row).model_dump(by_alias=True) for row in rows])


@router.post("/collections", status_code=201, response_model=ApiSuccess[CollectionView])
async def create_collection(
    application_id: UUID,
    environment_id: UUID,
    payload: CollectionCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _ensure_environment(session, application_id, environment_id)
    row = await KnowledgeService(session).create_collection(application_id, environment_id, payload)
    return success(CollectionView.model_validate(row).model_dump(by_alias=True))


@router.get(
    "/collections/{collection_id}/documents",
    response_model=ApiSuccess[list[DocumentView]],
)
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


@router.post(
    "/collections/{collection_id}/documents/upload",
    status_code=202,
    response_model=ApiSuccess[DocumentIngestionQueuedResponse],
)
async def upload_document(
    application_id: UUID,
    environment_id: UUID,
    collection_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    operation = "knowledge.document.upload"
    replay = await begin_idempotency(
        session,
        application_id=application_id,
        environment_id=environment_id,
        operation=operation,
        key=idempotency_key,
        request_fingerprint=await _file_fingerprint(
            file,
            operation=operation,
            scope={"collectionId": str(collection_id)},
        ),
    )
    if replay:
        return replay.body
    document, run = await KnowledgeService(session).create_file_document(
        application_id, environment_id, collection_id, file
    )
    body = success(
        {
            "document": DocumentView.model_validate(document).model_dump(by_alias=True),
            "ingestionRun": IngestionRunView.model_validate(run).model_dump(by_alias=True),
        }
    )
    await complete_idempotency(
        session,
        application_id=application_id,
        environment_id=environment_id,
        operation=operation,
        key=idempotency_key,
        status_code=202,
        body=body,
    )
    await session.commit()
    return body


@router.post(
    "/collections/{collection_id}/documents/text",
    status_code=202,
    response_model=ApiSuccess[DocumentIngestionQueuedResponse],
)
async def create_text_document(
    application_id: UUID,
    environment_id: UUID,
    collection_id: UUID,
    payload: TextDocumentCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    operation = "knowledge.document.text"
    replay = await begin_idempotency(
        session,
        application_id=application_id,
        environment_id=environment_id,
        operation=operation,
        key=idempotency_key,
        request_fingerprint=fingerprint_payload(
            operation,
            {"collectionId": str(collection_id), **payload.model_dump(mode="json")},
        ),
    )
    if replay:
        return replay.body
    document, run = await KnowledgeService(session).create_text_document(
        application_id,
        environment_id,
        collection_id,
        title=payload.title,
        content=payload.content,
    )
    body = success(
        {
            "document": DocumentView.model_validate(document).model_dump(by_alias=True),
            "ingestionRun": IngestionRunView.model_validate(run).model_dump(by_alias=True),
        }
    )
    await complete_idempotency(
        session,
        application_id=application_id,
        environment_id=environment_id,
        operation=operation,
        key=idempotency_key,
        status_code=202,
        body=body,
    )
    await session.commit()
    return body


@router.post(
    "/collections/{collection_id}/documents/remote",
    status_code=202,
    response_model=ApiSuccess[DocumentIngestionQueuedResponse],
)
async def create_remote_document(
    application_id: UUID,
    environment_id: UUID,
    collection_id: UUID,
    payload: RemoteDocumentCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    operation = "knowledge.document.remote"
    replay = await begin_idempotency(
        session,
        application_id=application_id,
        environment_id=environment_id,
        operation=operation,
        key=idempotency_key,
        request_fingerprint=fingerprint_payload(
            operation,
            {"collectionId": str(collection_id), **payload.model_dump(mode="json")},
        ),
    )
    if replay:
        return replay.body
    document, run = await KnowledgeService(session).create_remote_document(
        application_id,
        environment_id,
        collection_id,
        payload=payload,
    )
    body = success(
        {
            "document": DocumentView.model_validate(document).model_dump(by_alias=True),
            "ingestionRun": IngestionRunView.model_validate(run).model_dump(by_alias=True),
        }
    )
    await complete_idempotency(
        session,
        application_id=application_id,
        environment_id=environment_id,
        operation=operation,
        key=idempotency_key,
        status_code=202,
        body=body,
    )
    await session.commit()
    return body


@router.post(
    "/collections/{collection_id}/documents/remote-preview",
    response_model=ApiSuccess[RemotePreviewView],
)
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


@router.get("/ingestion-runs", response_model=ApiSuccess[list[IngestionRunView]])
async def list_ingestion_runs(
    application_id: UUID,
    environment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _ensure_environment(session, application_id, environment_id)
    rows = await KnowledgeRepository(session).list_runs(application_id, environment_id)
    return success([IngestionRunView.model_validate(row).model_dump(by_alias=True) for row in rows])


@router.post(
    "/ingestion-runs/{run_id}/retry",
    status_code=202,
    response_model=ApiSuccess[IngestionRunView],
)
async def retry_ingestion_run(
    application_id: UUID,
    environment_id: UUID,
    run_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    operation = "knowledge.ingestion.retry"
    replay = await begin_idempotency(
        session,
        application_id=application_id,
        environment_id=environment_id,
        operation=operation,
        key=idempotency_key,
        request_fingerprint=fingerprint_payload(operation, {"runId": str(run_id)}),
    )
    if replay:
        return replay.body
    run = await KnowledgeService(session).retry_run(application_id, environment_id, run_id)
    body = success(IngestionRunView.model_validate(run).model_dump(by_alias=True))
    await complete_idempotency(
        session,
        application_id=application_id,
        environment_id=environment_id,
        operation=operation,
        key=idempotency_key,
        status_code=202,
        body=body,
    )
    await session.commit()
    return body


@router.get(
    "/documents/{document_id}/versions",
    response_model=ApiSuccess[list[DocumentRevisionView]],
)
async def list_document_revisions(
    application_id: UUID,
    environment_id: UUID,
    document_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """读取文档版本时间线，供控制台辨识当前版本和失败版本。"""
    repository = KnowledgeRepository(session)
    if not await repository.get_document_in_context(application_id, environment_id, document_id):
        raise NotFoundError("文档不存在")
    rows = await repository.list_document_revisions(application_id, environment_id, document_id)
    views = [DocumentRevisionView.model_validate(row).model_dump(by_alias=True) for row in rows]
    return success(views)


@router.get(
    "/documents/{document_id}/versions/{revision_id}/chunks",
    response_model=ApiSuccess[list[DocumentChunkView]],
)
async def list_document_revision_chunks(
    application_id: UUID,
    environment_id: UUID,
    document_id: UUID,
    revision_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict:
    """按版本预览有限片段；先验证文档和版本归属，避免跨上下文读取正文。"""
    repository = KnowledgeRepository(session)
    if not await repository.get_document_in_context(application_id, environment_id, document_id):
        raise NotFoundError("文档不存在")
    if not await repository.get_document_revision(
        application_id, environment_id, document_id, revision_id
    ):
        raise NotFoundError("文档版本不存在")
    rows = await repository.list_revision_chunks(
        application_id, environment_id, revision_id, limit=limit
    )
    views = [DocumentChunkView.model_validate(row).model_dump(by_alias=True) for row in rows]
    return success(views)


@router.post(
    "/documents/{document_id}/versions/text",
    status_code=202,
    response_model=ApiSuccess[DocumentIngestionQueuedResponse],
)
async def create_text_revision(
    application_id: UUID,
    environment_id: UUID,
    document_id: UUID,
    payload: TextRevisionCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    operation = "knowledge.document.revision.text"
    replay = await begin_idempotency(
        session,
        application_id=application_id,
        environment_id=environment_id,
        operation=operation,
        key=idempotency_key,
        request_fingerprint=fingerprint_payload(
            operation,
            {"documentId": str(document_id), **payload.model_dump(mode="json")},
        ),
    )
    if replay:
        return replay.body
    document, run = await KnowledgeService(session).create_text_revision(
        application_id, environment_id, document_id, payload.content
    )
    body = success(
        {
            "document": DocumentView.model_validate(document).model_dump(by_alias=True),
            "ingestionRun": IngestionRunView.model_validate(run).model_dump(by_alias=True),
        }
    )
    await complete_idempotency(
        session,
        application_id=application_id,
        environment_id=environment_id,
        operation=operation,
        key=idempotency_key,
        status_code=202,
        body=body,
    )
    await session.commit()
    return body


@router.post(
    "/documents/{document_id}/versions/upload",
    status_code=202,
    response_model=ApiSuccess[DocumentIngestionQueuedResponse],
)
async def create_file_revision(
    application_id: UUID,
    environment_id: UUID,
    document_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    operation = "knowledge.document.revision.upload"
    replay = await begin_idempotency(
        session,
        application_id=application_id,
        environment_id=environment_id,
        operation=operation,
        key=idempotency_key,
        request_fingerprint=await _file_fingerprint(
            file,
            operation=operation,
            scope={"documentId": str(document_id)},
        ),
    )
    if replay:
        return replay.body
    document, run = await KnowledgeService(session).create_file_revision(
        application_id, environment_id, document_id, file
    )
    body = success(
        {
            "document": DocumentView.model_validate(document).model_dump(by_alias=True),
            "ingestionRun": IngestionRunView.model_validate(run).model_dump(by_alias=True),
        }
    )
    await complete_idempotency(
        session,
        application_id=application_id,
        environment_id=environment_id,
        operation=operation,
        key=idempotency_key,
        status_code=202,
        body=body,
    )
    await session.commit()
    return body


@router.post(
    "/documents/{document_id}/refresh",
    status_code=202,
    response_model=ApiSuccess[DocumentIngestionQueuedResponse],
)
async def refresh_remote_document(
    application_id: UUID,
    environment_id: UUID,
    document_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    operation = "knowledge.document.refresh"
    replay = await begin_idempotency(
        session,
        application_id=application_id,
        environment_id=environment_id,
        operation=operation,
        key=idempotency_key,
        request_fingerprint=fingerprint_payload(operation, {"documentId": str(document_id)}),
    )
    if replay:
        return replay.body
    document, run = await KnowledgeService(session).refresh_remote_document(
        application_id, environment_id, document_id
    )
    body = success(
        {
            "document": DocumentView.model_validate(document).model_dump(by_alias=True),
            "ingestionRun": IngestionRunView.model_validate(run).model_dump(by_alias=True),
        }
    )
    await complete_idempotency(
        session,
        application_id=application_id,
        environment_id=environment_id,
        operation=operation,
        key=idempotency_key,
        status_code=202,
        body=body,
    )
    await session.commit()
    return body


@router.post(
    "/documents/{document_id}/versions/{revision_id}/rollback",
    response_model=ApiSuccess[DocumentView],
)
async def rollback_document_revision(
    application_id: UUID,
    environment_id: UUID,
    document_id: UUID,
    revision_id: UUID,
    actor: Annotated[str, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """切换文档活动版本；仅允许已完成且有向量片段的版本。"""
    document = await KnowledgeService(session).rollback_document_revision(
        application_id,
        environment_id,
        document_id,
        revision_id,
        actor=actor,
    )
    return success(DocumentView.model_validate(document).model_dump(by_alias=True))


@router.delete("/documents/{document_id}", response_model=ApiSuccess[ArchiveResponse])
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


@router.delete("/collections/{collection_id}", response_model=ApiSuccess[ArchiveResponse])
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
