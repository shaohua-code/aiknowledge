"""Celery 应用实例与队列定义。

对应 SubTask 9.1：定义 4 个队列 online / ingestion / crawler / maintenance，
配置任务路由、超时、acks_late、prefetch 等关键参数，并自动发现任务模块。

队列优先级设计（重要）
----------------------
系统按业务重要性将任务分发到 4 条独立队列，部署时可给高优先级队列分配更多 Worker：
1. ``online`` 队列：在线研究、检索等用户同步等待的任务，延迟敏感，最高优先级。
   部署建议：独占 Worker 进程，prefetch=1，避免长任务阻塞。
2. ``ingestion`` 队列：文档解析/分块/向量化等后台任务，CPU 与 IO 密集，
   用户不直接等待，但影响知识库可见性，中等优先级。
3. ``crawler`` 队列：网页采集任务，长耗时、可容忍失败重试，低优先级。
4. ``maintenance`` 队列：定时任务派发、统计聚合等维护性任务，最低优先级。

为什么需要独立队列而非单队列 + 优先级字段？
    Redis broker 的优先级队列（Redis Queue 优先级）实现为多个 list 轮询，
    高优先级任务仍可能被低优先级长任务阻塞。独立队列 + 独立 Worker 进程
    可从物理上隔离资源，保证 online 队列永远有 Worker 处理，不被 ingestion
    的长文档解析拖累。运维侧启动 Worker 时通过 ``-Q`` 指定消费队列：
        ``celery -A app.workers.celery_app worker -Q online``
        ``celery -A app.workers.celery_app worker -Q ingestion,crawler``
"""
from __future__ import annotations

from kombu import Queue

from app.core.config import settings
from celery import Celery

# Celery 应用实例：broker 与 result_backend 从配置读取
# broker 用 Redis DB 1，result_backend 用 Redis DB 2（与 settings 默认值一致）
celery_app = Celery(
    "knowledge_hub",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    # 自动发现任务模块：Worker 启动时自动 import 以下模块，
    # 保证 @celery_app.task 装饰器注册到全局任务表，可被 .delay() / .apply_async() 调用
    include=[
        "app.workers.ingestion_tasks",
        "app.workers.crawl_tasks",
        "app.workers.schedule_tasks",
        "app.workers.research_tasks",
    ],
)

# ---------------------------------------------------------------------------
# 队列定义：4 条独立队列，无优先级字段，靠独立 Worker 进程物理隔离资源
# ---------------------------------------------------------------------------
celery_app.conf.task_queues = (
    # online：在线低延迟任务（研究、检索），最高优先级，独占 Worker
    Queue(
        name="online",
        routing_key="online",
    ),
    # ingestion：文档入库任务（解析/分块/向量化），中优先级
    Queue(
        name="ingestion",
        routing_key="ingestion",
    ),
    # crawler：网页采集任务，低优先级
    Queue(
        name="crawler",
        routing_key="crawler",
    ),
    # maintenance：维护性任务（定时派发、清理），低优先级
    Queue(
        name="maintenance",
        routing_key="maintenance",
    ),
)

# 默认队列：未显式指定队列的任务落入 ingestion
# 选 ingestion 而非 online 的原因：避免误投递任务抢占在线 Worker 资源
celery_app.conf.task_default_queue = "ingestion"
celery_app.conf.task_default_routing_key = "ingestion"

# ---------------------------------------------------------------------------
# 任务路由：按任务名前缀路由到对应队列
# ---------------------------------------------------------------------------
# 路由规则以任务名为 key（name 参数指定的字符串），匹配规则：
#   "app.workers.ingestion_tasks.*" 匹配该模块下所有任务
# 路由命中后，任务被投递到指定 queue，Worker 通过 -Q 参数消费对应队列
celery_app.conf.task_routes = {
    # 文档处理任务 → ingestion 队列（中优先级，CPU/IO 密集）
    "app.workers.ingestion_tasks.*": {"queue": "ingestion"},
    # 爬虫任务 → crawler 队列（低优先级，长耗时）
    "app.workers.crawl_tasks.*": {"queue": "crawler"},
    # 定时任务派发 → maintenance 队列（低优先级，维护性）
    "app.workers.schedule_tasks.*": {"queue": "maintenance"},
    # 研究任务 → online 队列（高优先级，用户同步等待）
    "app.workers.research_tasks.*": {"queue": "online"},
}

# ---------------------------------------------------------------------------
# 关键运行参数
# ---------------------------------------------------------------------------

# worker_prefetch_multiplier=1：每个 Worker 进程一次只领取 1 个任务
# 为什么设为 1？
#   ingestion 任务长耗时且 CPU/IO 密集，若 Worker 一次预取多个任务，
#   会造成后续任务在 Worker 内存中排队等待，无法被其他空闲 Worker 抢走，
#   导致整体吞吐下降。prefetch=1 保证任务仅在"正在处理"时被领取，
#   未开始的任务留在队列中，可被任意空闲 Worker 立即消费。
#   配合 acks_late=True，Worker 崩溃时未完成的任务会被重新投递，不会丢失。
celery_app.conf.worker_prefetch_multiplier = 1

# task_acks_late=True：任务执行完成（或失败）后才向 broker 发送 ACK
# 为什么需要 acks_late？
#   默认行为（acks_early）下，Worker 领取任务即 ACK，任务从队列消失。
#   若 Worker 在执行中崩溃或被 OOM Kill，任务已 ACK 无法重投，造成数据丢失。
#   acks_late 让任务在执行完成后才 ACK，崩溃时 broker 因未收到 ACK 而重投任务
#   给其他 Worker。代价是任务可能被重复执行（需业务侧做幂等），但对入库流程
#   是可接受的：解析/切割/向量化本身是幂等的（重新执行只是覆盖结果）。
celery_app.conf.task_acks_late = True

# 全局任务硬超时：600 秒，超过将被 Worker 强制终止（SIGKILL）
# 用于防止任务卡死（如外部 URL 抓取挂死、LLM 接口无响应）拖垮 Worker。
# 配合 acks_late，被杀死的任务会被重投。
celery_app.conf.task_time_limit = 600

# 全局任务软超时：540 秒，超过将抛 SoftTimeLimitExceeded
# 业务代码可捕获此异常做清理（如标记任务为 failed、回滚事务），
# 在硬超时（600s）前优雅退出，避免被 SIGKILL 强杀导致状态不一致。
celery_app.conf.task_soft_time_limit = 540

# 拒绝接收未声明的任务：防止任务名拼写错误被静默忽略
# 仅允许 task_routes / 任务装饰器中声明的任务被执行
celery_app.conf.task_reject_on_worker_lost = True

# 结果后端仅保留 1 小时：避免 Redis 内存膨胀
celery_app.conf.result_expires = 3600


@celery_app.task(name="health_check")
def health_check() -> str:
    """健康检查任务：验证 Celery Worker 可用。

    用于运维探活与 K8s readiness probe，调用方式：
        ``celery -A app.workers.celery_app call health_check``

    Returns:
        固定返回 ``"ok"``，表示 Worker 进程正常工作。
    """
    return "ok"


# ---------------------------------------------------------------------------
# Celery Beat 配置：定时调度器（Task 18.4）
# ---------------------------------------------------------------------------
# Beat 是 Celery 的定时调度组件，按 ``beat_schedule`` 中的 crontab 规则
# 定期触发任务。本平台仅有一个 Beat 任务：每分钟扫描到期定时任务。
#
# ⚠️ 重要：Beat 只能有一个有效实例
# ---------------------------------
# Beat 进程负责按计划触发任务，若部署多个 Beat 实例：
#   - 每个 Beat 都会按相同规则触发任务，导致任务被重复投递。
#   - 虽然业务层有幂等键（schedule_id + planned_at）防护，但重复投递
#     仍会造成无意义的 claim 竞争与资源浪费。
# 部署时通过 K8s Deployment ``replicas=1`` 或分布式锁保证 Beat 单实例运行。
# 启动 Beat：
#   ``celery -A app.workers.celery_app beat --loglevel=info``
from celery.schedules import crontab  # noqa: E402  局部导入避免顶部样式干扰

celery_app.conf.beat_schedule = {
    # 每分钟扫描到期定时任务并投递执行
    # crontab(minute="*") 表示每分钟触发一次（每小时的每一分钟）
    # dispatch_due_schedules 内部通过 list_due 查询 next_run_at <= now 的任务，
    # 即使 Beat 每分钟触发，任务的实际执行时间由 next_run_at 决定，不会误触发未到期任务
    "dispatch-due-schedules": {
        "task": "app.workers.schedule_tasks.dispatch_due_schedules",
        "schedule": crontab(minute="*"),  # 每分钟
    },
}
