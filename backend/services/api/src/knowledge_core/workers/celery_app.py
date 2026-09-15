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
        "knowledge.dispatch_outbox": {"queue": "maintenance"},
        # 清理任务与 Outbox 派发共用维护队列，确保当前 Worker 会实际消费 Beat 消息。
        "knowledge.cleanup_orphan_objects": {"queue": "maintenance"},
    },
    # 独立 Beat 会周期唤醒派发器，恢复进程崩溃后已过期租约的待投递事件。
    beat_schedule={
        "dispatch-pending-knowledge-outbox": {
            "task": "knowledge.dispatch_outbox",
            "schedule": 60.0,
        },
        # 每日扫描已超过保留窗口的无引用对象，避免归档与失败任务积累永久孤儿文件。
        "cleanup-orphan-knowledge-objects": {
            "task": "knowledge.cleanup_orphan_objects",
            "schedule": 24 * 60 * 60.0,
        },
    },
)
