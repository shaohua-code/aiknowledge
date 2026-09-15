"""验证 Outbox 恢复调度已注册到 Celery Beat。"""

from knowledge_core.workers.celery_app import celery_app


def test_outbox_dispatcher_is_scheduled_for_recovery() -> None:
    """已过期租约不能依赖下一次用户操作，Beat 必须周期触发派发器扫描。"""
    schedule = celery_app.conf.beat_schedule["dispatch-pending-knowledge-outbox"]

    assert schedule["task"] == "knowledge.dispatch_outbox"
    assert schedule["schedule"] == 60.0


def test_orphan_object_cleanup_is_scheduled_after_retention_window() -> None:
    """孤儿对象清理必须由独立 Beat 周期触发，不能依赖人工请求才执行。"""
    schedule = celery_app.conf.beat_schedule["cleanup-orphan-knowledge-objects"]

    assert schedule["task"] == "knowledge.cleanup_orphan_objects"
    assert schedule["schedule"] == 24 * 60 * 60.0


def test_orphan_object_cleanup_is_routed_to_maintenance_worker() -> None:
    """Beat 发布的清理任务必须进入 Compose Worker 实际监听的 maintenance 队列。"""
    routes = celery_app.conf.task_routes

    assert routes["knowledge.cleanup_orphan_objects"]["queue"] == "maintenance"
