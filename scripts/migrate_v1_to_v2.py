from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine


def _database_url(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"缺少环境变量 {name}")
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    return value


@dataclass(slots=True)
class Counts:
    projects: int
    collections: int
    documents: int
    chunks: int


async def _counts(connection: AsyncConnection) -> Counts:
    row = (
        await connection.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM projects) AS projects,
                  (SELECT count(*) FROM knowledge_bases) AS collections,
                  (SELECT count(*) FROM documents) AS documents,
                  (SELECT count(*) FROM document_chunks WHERE enabled = true) AS chunks
                """
            )
        )
    ).one()
    return Counts(*(int(value) for value in row))


def _environment_id(project_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"aiknowledge-v2:{project_id}:development")


def _source_id(document_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"aiknowledge-v2:{document_id}:source")


def _revision_id(document_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"aiknowledge-v2:{document_id}:revision:1")


def _json(value: Any, fallback: Any) -> str:
    return json.dumps(value if value is not None else fallback, ensure_ascii=False)


async def _insert_application(target: AsyncConnection, project: Any) -> UUID:
    project_id = UUID(str(project.id))
    environment_id = _environment_id(project_id)
    await target.execute(
        text(
            """
            INSERT INTO applications
              (id, code, name, description, application_type, status, created_at, updated_at)
            VALUES
              (:id, :code, :name, :description, 'general', :status, :created_at, :updated_at)
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "id": project_id,
            "code": str(project.code).lower(),
            "name": project.name,
            "description": project.description,
            "status": project.status
            if project.status in {"active", "disabled"}
            else "disabled",
            "created_at": project.created_at,
            "updated_at": project.updated_at,
        },
    )
    await target.execute(
        text(
            """
            INSERT INTO application_environments
              (id, application_id, code, name, status, created_at, updated_at)
            VALUES
              (:id, :application_id, 'development', '开发环境', 'active', now(), now())
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"id": environment_id, "application_id": project_id},
    )
    return environment_id


async def _migrate_project(
    source: AsyncConnection,
    target: AsyncConnection,
    project: Any,
) -> tuple[int, int, int]:
    project_id = UUID(str(project.id))
    environment_id = await _insert_application(target, project)
    collections = (
        await source.execute(
            text(
                "SELECT * FROM knowledge_bases WHERE project_id = :project_id ORDER BY created_at"
            ),
            {"project_id": project_id},
        )
    ).all()
    document_total = 0
    chunk_total = 0

    for collection in collections:
        collection_id = UUID(str(collection.id))
        documents = (
            await source.execute(
                text(
                    "SELECT * FROM documents "
                    "WHERE project_id = :project_id AND knowledge_base_id = :collection_id "
                    "ORDER BY created_at"
                ),
                {"project_id": project_id, "collection_id": collection_id},
            )
        ).all()
        active_documents = [item for item in documents if item.enabled]
        collection_chunks = 0
        ready_document_count = 0
        for document in active_documents:
            document_chunk_count = int(
                await source.scalar(
                    text(
                        "SELECT count(*) FROM document_chunks "
                        "WHERE project_id = :project_id AND document_id = :document_id "
                        "AND enabled = true"
                    ),
                    {"project_id": project_id, "document_id": document.id},
                )
                or 0
            )
            collection_chunks += document_chunk_count
            ready_document_count += int(document_chunk_count > 0)
        await target.execute(
            text(
                """
                INSERT INTO knowledge_collections
                  (id, application_id, environment_id, code, name, description, status,
                   document_count, chunk_count, last_published_at, created_at, updated_at)
                VALUES
                  (:id, :application_id, :environment_id, :code, :name, :description, :status,
                   :document_count, :chunk_count, :published_at, :created_at, :updated_at)
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": collection_id,
                "application_id": project_id,
                "environment_id": environment_id,
                "code": str(collection.code).lower(),
                "name": collection.name,
                "description": collection.description,
                "status": "active" if collection.status == "active" else "archived",
                "document_count": ready_document_count,
                "chunk_count": collection_chunks,
                "published_at": collection.updated_at,
                "created_at": collection.created_at,
                "updated_at": collection.updated_at,
            },
        )

        for document in documents:
            document_id = UUID(str(document.id))
            source_id = _source_id(document_id)
            revision_id = _revision_id(document_id)
            chunks = (
                await source.execute(
                    text(
                        "SELECT * FROM document_chunks "
                        "WHERE project_id = :project_id AND document_id = :document_id "
                        "AND enabled = true ORDER BY chunk_index"
                    ),
                    {"project_id": project_id, "document_id": document_id},
                )
            ).all()
            ready = bool(document.enabled and chunks)
            source_type = (
                document.source_type
                if document.source_type in {"file", "manual", "web", "api"}
                else "file"
            )
            await target.execute(
                text(
                    """
                    INSERT INTO sources
                      (id, application_id, environment_id, collection_id, source_type, name,
                       configuration, status, last_synced_at, created_at, updated_at)
                    VALUES
                      (:id, :application_id, :environment_id, :collection_id, :source_type, :name,
                       CAST(:configuration AS json), :status, :last_synced_at, :created_at, :updated_at)
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": source_id,
                    "application_id": project_id,
                    "environment_id": environment_id,
                    "collection_id": collection_id,
                    "source_type": source_type,
                    "name": document.title,
                    "configuration": _json({"url": document.source_url}, {}),
                    "status": "active" if document.enabled else "archived",
                    "last_synced_at": document.updated_at if ready else None,
                    "created_at": document.created_at,
                    "updated_at": document.updated_at,
                },
            )
            await target.execute(
                text(
                    """
                    INSERT INTO documents
                      (id, application_id, environment_id, collection_id, source_id, title,
                       mime_type, storage_key, source_url, status, current_version, archived_at,
                       created_at, updated_at)
                    VALUES
                      (:id, :application_id, :environment_id, :collection_id, :source_id, :title,
                       :mime_type, :storage_key, :source_url, :status, :current_version, :archived_at,
                       :created_at, :updated_at)
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": document_id,
                    "application_id": project_id,
                    "environment_id": environment_id,
                    "collection_id": collection_id,
                    "source_id": source_id,
                    "title": document.title,
                    "mime_type": document.mime_type,
                    "storage_key": document.storage_key,
                    "source_url": document.source_url,
                    "status": "ready"
                    if ready
                    else ("archived" if not document.enabled else "failed"),
                    "current_version": 1 if ready else None,
                    "archived_at": document.updated_at
                    if not document.enabled
                    else None,
                    "created_at": document.created_at,
                    "updated_at": document.updated_at,
                },
            )
            content_hash = (
                document.content_hash
                or hashlib.sha256(str(document_id).encode()).hexdigest()
            )
            await target.execute(
                text(
                    """
                    INSERT INTO document_revisions
                      (id, application_id, environment_id, document_id, version, content_hash,
                       storage_key, status, char_count, metadata, published_at, created_at, updated_at)
                    VALUES
                      (:id, :application_id, :environment_id, :document_id, 1, :content_hash,
                       :storage_key, :status, :char_count, CAST(:metadata AS json), :published_at,
                       :created_at, :updated_at)
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": revision_id,
                    "application_id": project_id,
                    "environment_id": environment_id,
                    "document_id": document_id,
                    "content_hash": content_hash,
                    "storage_key": document.storage_key,
                    "status": "ready" if ready else "failed",
                    "char_count": sum(len(item.content) for item in chunks),
                    "metadata": _json(document.metadata, {}),
                    "published_at": document.updated_at if ready else None,
                    "created_at": document.created_at,
                    "updated_at": document.updated_at,
                },
            )
            for chunk in chunks:
                await target.execute(
                    text(
                        """
                        INSERT INTO document_chunks
                          (id, application_id, environment_id, revision_id, chunk_index, content,
                           page_number, section, token_count, metadata, embedding,
                           created_at, updated_at)
                        VALUES
                          (:id, :application_id, :environment_id, :revision_id, :chunk_index, :content,
                           :page_number, NULL, :token_count, CAST(:metadata AS json), NULL,
                           :created_at, :updated_at)
                        ON CONFLICT (id) DO NOTHING
                        """
                    ),
                    {
                        "id": UUID(str(chunk.id)),
                        "application_id": project_id,
                        "environment_id": environment_id,
                        "revision_id": revision_id,
                        "chunk_index": chunk.chunk_index,
                        "content": chunk.content,
                        "page_number": chunk.page_number,
                        "token_count": chunk.token_count
                        or max(1, len(chunk.content) // 3),
                        "metadata": _json(chunk.metadata, {}),
                        "created_at": chunk.created_at,
                        "updated_at": chunk.updated_at,
                    },
                )
            document_total += 1
            chunk_total += len(chunks)
    return len(collections), document_total, chunk_total


async def run(execute: bool) -> None:
    source_engine = create_async_engine(
        _database_url("V1_DATABASE_URL"), pool_pre_ping=True
    )
    try:
        async with source_engine.connect() as source:
            counts = await _counts(source)
            print(
                "V1 audit: "
                f"projects={counts.projects}, collections={counts.collections}, "
                f"documents={counts.documents}, active_chunks={counts.chunks}"
            )
            if not execute:
                print(
                    "Dry-run complete. No target data was written. Add --execute after backup review."
                )
                return

            target_engine = create_async_engine(
                _database_url("DATABASE_URL"), pool_pre_ping=True
            )
            try:
                projects = (
                    await source.execute(
                        text("SELECT * FROM projects ORDER BY created_at")
                    )
                ).all()
                migrated = [0, 0, 0]
                async with target_engine.begin() as target:
                    for project in projects:
                        result = await _migrate_project(source, target, project)
                        migrated = [
                            left + right
                            for left, right in zip(migrated, result, strict=True)
                        ]
                print(
                    "V2 migration complete: "
                    f"applications={len(projects)}, collections={migrated[0]}, "
                    f"documents={migrated[1]}, chunks={migrated[2]}"
                )
                print(
                    "Embeddings were intentionally not copied; rebuild vectors before production cutover."
                )
            finally:
                await target_engine.dispose()
    finally:
        await source_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit or migrate AI Knowledge V1 core data"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="write mapped core knowledge into an already migrated V2 database",
    )
    args = parser.parse_args()
    asyncio.run(run(args.execute))


if __name__ == "__main__":
    main()
