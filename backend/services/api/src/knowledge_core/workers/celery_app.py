from __future__ import annotations

from celery import Celery

from knowledge_core.config import settings

celery_app = Celery(
    "aiknowledge-v2",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["knowledge_core.workers.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "knowledge.ingest_document": {"queue": "ingestion"},
    },
)
