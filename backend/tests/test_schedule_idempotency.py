"""调度运行记录幂等性测试（SubTask 25.3）。

对应 Task 25：验证 ScheduleRunRepository 的幂等约束与并发领取语义。

测试设计要点
------------
1. **复合唯一约束验证**
   ``schedule_runs`` 表的复合唯一约束
   ``(project_id, schedule_id, planned_at)`` 保证同一任务同一计划时间只执行一次。
   并发创建相同 (schedule_id, planned_at) 的运行记录时，第二次应触发
   ``IntegrityError``，``ScheduleRunRepository.create`` 捕获后返回 None。

2. **FOR UPDATE SKIP LOCKED 语义**
   ``ScheduleRunRepository.claim_due_run`` 使用 PostgreSQL
   ``SELECT ... FOR UPDATE SKIP LOCKED`` 实现多实例并发领取时的安全加锁。
   本测试通过 mock 验证 SQL 含 ``SKIP LOCKED`` 关键字（真实并发场景需多进程，
   单元测试难以覆盖，此处验证 SQL 生成正确性）。

3. **数据库依赖**
   测试需真实数据库（PostgreSQL），通过 ``conftest.py`` 提供 ``db_session`` fixture。
   数据库未启动时跳过。

测试场景对照
------------
- test_schedule_run_unique_constraint：相同 (schedule_id, planned_at) 第二次返回 None
- test_schedule_run_different_planned_at_succeeds：不同 planned_at 可创建多条
- test_schedule_run_different_schedule_id_succeeds：不同 schedule_id 可创建多条
- test_claim_due_run_returns_existing_when_already_claimed：已存在时 claim 返回 None
- test_claim_due_run_sql_contains_skip_locked：claim SQL 含 FOR UPDATE SKIP LOCKED
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError

from app.core.project_context import ProjectContext
from app.db.repositories.project import ProjectRepository, ProjectSettingsRepository
from app.db.repositories.schedule import (
    ScheduleRepository,
    ScheduleRunRepository,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# ============================================================================
# 跳过开关：通过环境变量手动跳过调度幂等测试
# ============================================================================
_SKIP_FLAG = os.getenv("SKIP_SCHEDULE_TESTS", "0") == "1"

# 标记：所有测试均为异步，asyncio_mode=auto 已在 pyproject.toml 配置
pytestmark = pytest.mark.skipif(
    _SKIP_FLAG,
    reason="手动跳过调度幂等测试（SKIP_SCHEDULE_TESTS=1）",
)


async def _check_db_available(session: "AsyncSession") -> bool:
    """检测数据库是否可用。

    通过执行 ``SELECT 1`` 判断数据库连接是否正常。
    连接失败时返回 False，由调用方触发 skip。

    Args:
        session: 异步数据库会话。

    Returns:
        True 表示数据库可用；False 表示不可用。
    """
    from sqlalchemy import text

    try:
        await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest_asyncio.fixture
async def schedule_test_env(db_session: "AsyncSession"):
    """创建调度测试所需的项目与 Schedule 记录。

    创建一个测试项目与一个 Schedule，用于后续 ScheduleRun 幂等测试。
    数据库未启动时跳过整个 fixture。

    Yields:
        元组 ``(ctx, schedule)``，项目上下文与 Schedule ORM 实例。
    """
    # 检测数据库可用性，不可用则跳过
    if not await _check_db_available(db_session):
        pytest.skip("数据库未启动，跳过调度幂等测试")

    project_repo = ProjectRepository(db_session)
    settings_repo = ProjectSettingsRepository(db_session)
    schedule_repo = ScheduleRepository(db_session)

    # 创建测试项目
    project = await project_repo.create(
        code="ai-schedule-idempotency-test",
        name="调度幂等测试项目",
        description="调度运行记录幂等性测试",
    )
    ctx = ProjectContext(project_id=project.id, project_code=project.code)
    await settings_repo.upsert(ctx)

    # 创建一个 Schedule，用于后续 ScheduleRun 测试
    schedule = await schedule_repo.create(
        ctx,
        name="幂等测试定时任务",
        task_type="crawl_source",
        cron_expression="0 9 * * 1-5",
        timezone="Asia/Shanghai",
        config={"source_id": "fake-source-id"},
        concurrency_policy="skip",
        timeout_seconds=300,
        max_retries=2,
        enabled=True,
        next_run_at=datetime.now(timezone.utc),
    )
    await db_session.commit()

    yield ctx, schedule

    # 清理：回滚未提交事务
    await db_session.rollback()


# ============================================================================
# 测试 1：相同 (schedule_id, planned_at) 第二次创建返回 None（幂等）
# ============================================================================
async def test_schedule_run_unique_constraint(
    db_session: "AsyncSession",
    schedule_test_env,
):
    """相同 (schedule_id, planned_at) 的运行记录第二次创建返回 None。

    场景：
        - 第一次 create(schedule_id, planned_at) 成功，返回 ScheduleRun
        - 第二次 create(相同 schedule_id, 相同 planned_at) 触发唯一约束冲突
        - Repository.create 捕获 IntegrityError 后返回 None（幂等）

    验证点：
        - 第一次 create 返回非 None（成功）
        - 第二次 create 返回 None（冲突，幂等处理）
        - 数据库中仅存在一条该 (schedule_id, planned_at) 的记录
    """
    ctx, schedule = schedule_test_env
    run_repo = ScheduleRunRepository(db_session)

    # 固定一个计划时间，用于两次 create
    planned_at = datetime(2026, 7, 30, 9, 0, 0, tzinfo=timezone.utc)

    # 第一次创建：成功
    run1 = await run_repo.create(ctx, schedule.id, planned_at)
    await db_session.commit()
    assert run1 is not None, "首次创建应成功"
    assert run1.schedule_id == schedule.id
    assert run1.planned_at == planned_at
    assert run1.status == "pending"

    # 第二次创建：相同 (schedule_id, planned_at)，应触发唯一约束冲突
    # Repository.create 内部捕获 IntegrityError 后返回 None
    run2 = await run_repo.create(ctx, schedule.id, planned_at)
    assert run2 is None, "相同 (schedule_id, planned_at) 第二次创建应返回 None（幂等）"


# ============================================================================
# 测试 2：不同 planned_at 可创建多条运行记录
# ============================================================================
async def test_schedule_run_different_planned_at_succeeds(
    db_session: "AsyncSession",
    schedule_test_env,
):
    """同一 schedule_id 但不同 planned_at 可创建多条运行记录。

    场景：
        - 创建 (schedule_id, planned_at=T1) 成功
        - 创建 (schedule_id, planned_at=T2) 成功（T2 != T1）
        - 两条记录都存在，互不冲突

    验证点：
        - 两次 create 都返回非 None
        - 不同 planned_at 不触发唯一约束
    """
    ctx, schedule = schedule_test_env
    run_repo = ScheduleRunRepository(db_session)

    # 两个不同的计划时间
    planned_at_1 = datetime(2026, 7, 30, 9, 0, 0, tzinfo=timezone.utc)
    planned_at_2 = datetime(2026, 7, 30, 10, 0, 0, tzinfo=timezone.utc)

    # 创建第一条：T1
    run1 = await run_repo.create(ctx, schedule.id, planned_at_1)
    await db_session.commit()
    assert run1 is not None, "T1 创建应成功"

    # 创建第二条：T2（不同 planned_at）
    run2 = await run_repo.create(ctx, schedule.id, planned_at_2)
    await db_session.commit()
    assert run2 is not None, "T2 创建应成功（不同 planned_at 不冲突）"
    assert run2.id != run1.id, "两条记录应有不同 ID"


# ============================================================================
# 测试 3：不同 schedule_id 可创建多条运行记录（同 planned_at）
# ============================================================================
async def test_schedule_run_different_schedule_id_succeeds(
    db_session: "AsyncSession",
    schedule_test_env,
):
    """同一 planned_at 但不同 schedule_id 可创建多条运行记录。

    场景：
        - 创建 Schedule A 与 Schedule B
        - 创建 (schedule_a, planned_at=T) 成功
        - 创建 (schedule_b, planned_at=T) 成功（schedule_id 不同）
        - 两条记录都存在，互不冲突

    验证点：
        - 不同 schedule_id 即使 planned_at 相同也不冲突
        - 复合唯一约束允许跨 schedule 的相同时间点
    """
    ctx, schedule_a = schedule_test_env
    schedule_repo = ScheduleRepository(db_session)
    run_repo = ScheduleRunRepository(db_session)

    # 创建第二个 Schedule
    schedule_b = await schedule_repo.create(
        ctx,
        name="幂等测试定时任务 B",
        task_type="crawl_source",
        cron_expression="0 10 * * 1-5",
        timezone="Asia/Shanghai",
        config={"source_id": "fake-source-id-b"},
        concurrency_policy="skip",
        timeout_seconds=300,
        max_retries=2,
        enabled=True,
        next_run_at=datetime.now(timezone.utc),
    )
    await db_session.commit()

    # 同一 planned_at，不同 schedule_id
    planned_at = datetime(2026, 7, 30, 9, 0, 0, tzinfo=timezone.utc)

    # 创建 schedule_a 的运行记录
    run_a = await run_repo.create(ctx, schedule_a.id, planned_at)
    await db_session.commit()
    assert run_a is not None, "schedule_a 创建应成功"

    # 创建 schedule_b 的运行记录（同 planned_at，不同 schedule_id）
    run_b = await run_repo.create(ctx, schedule_b.id, planned_at)
    await db_session.commit()
    assert run_b is not None, "schedule_b 创建应成功（不同 schedule_id 不冲突）"
    assert run_b.id != run_a.id


# ============================================================================
# 测试 4：claim_due_run 已存在时返回 None（已被其他实例领取）
# ============================================================================
async def test_claim_due_run_returns_none_when_already_claimed(
    db_session: "AsyncSession",
    schedule_test_env,
):
    """claim_due_run 在运行记录已存在时返回 None。

    场景：
        - 先通过 create 创建一条 (schedule_id, planned_at) 运行记录
        - 调用 claim_due_run(相同 schedule_id, planned_at)
        - claim_due_run 内部 SELECT FOR UPDATE SKIP LOCKED 发现已存在
        - 返回 None（已被其他实例领取）

    验证点：
        - 已存在运行记录时，claim_due_run 返回 None
        - 不会创建重复记录
    """
    ctx, schedule = schedule_test_env
    run_repo = ScheduleRunRepository(db_session)

    planned_at = datetime(2026, 7, 30, 9, 0, 0, tzinfo=timezone.utc)

    # 先创建一条运行记录（模拟已被领取）
    existing_run = await run_repo.create(ctx, schedule.id, planned_at)
    await db_session.commit()
    assert existing_run is not None

    # 调用 claim_due_run：应发现已存在，返回 None
    claimed = await run_repo.claim_due_run(schedule.id, planned_at)
    assert claimed is None, "已存在的运行记录不应被重复领取"


# ============================================================================
# 测试 5：claim_due_run 不存在时创建新记录
# ============================================================================
async def test_claim_due_run_creates_new_when_not_exists(
    db_session: "AsyncSession",
    schedule_test_env,
):
    """claim_due_run 在运行记录不存在时创建新记录。

    场景：
        - 不预先创建运行记录
        - 调用 claim_due_run(schedule_id, planned_at)
        - claim_due_run 内部 SELECT FOR UPDATE SKIP LOCKED 未找到记录
        - 创建新的 pending 运行记录并返回

    验证点：
        - 返回的 ScheduleRun 非 None
        - status 为 pending
        - schedule_id 与 planned_at 与传入参数一致
        - project_id 从 schedule 表反查得到
    """
    ctx, schedule = schedule_test_env
    run_repo = ScheduleRunRepository(db_session)

    planned_at = datetime(2026, 7, 30, 11, 0, 0, tzinfo=timezone.utc)

    # 调用 claim_due_run：不存在，应创建新记录
    claimed = await run_repo.claim_due_run(schedule.id, planned_at)
    await db_session.commit()

    assert claimed is not None, "不存在的运行记录应被创建"
    assert claimed.schedule_id == schedule.id
    assert claimed.planned_at == planned_at
    assert claimed.status == "pending"
    # project_id 应从 schedule 表反查得到
    assert claimed.project_id == schedule.project_id


# ============================================================================
# 测试 6：claim_due_run 的 SQL 含 FOR UPDATE SKIP LOCKED（mock 验证）
# ============================================================================
async def test_claim_due_run_sql_contains_skip_locked(
    db_session: "AsyncSession",
    schedule_test_env,
    monkeypatch,
):
    """验证 claim_due_run 生成的 SQL 含 FOR UPDATE SKIP LOCKED。

    场景：
        - 拦截 session.scalar，记录实际执行的 SQL 文本
        - 调用 claim_due_run
        - 检查 SQL 文本含 ``FOR UPDATE`` 与 ``SKIP LOCKED`` 关键字

    验证点：
        - claim_due_run 使用 with_for_update(skip_locked=True)
        - 编译后的 SQL 含 SKIP LOCKED 语义

    Note:
        FOR UPDATE SKIP LOCKED 是 PostgreSQL 行级锁特性，
        确保 SQL 生成正确即可，真实并发场景需多进程集成测试。
    """
    ctx, schedule = schedule_test_env

    # 记录所有 scalar 调用的 SQL 文本
    executed_sqls: list[str] = []
    original_scalar = db_session.scalar

    async def _capture_scalar(stmt, *args, **kwargs):
        """拦截 session.scalar，记录 SQL 编译文本。"""
        try:
            # 编译 SQL 为字符串，便于断言检查
            compiled = stmt.compile()
            executed_sqls.append(str(compiled))
        except Exception:
            pass
        return await original_scalar(stmt, *args, **kwargs)

    monkeypatch.setattr(db_session, "scalar", _capture_scalar)

    run_repo = ScheduleRunRepository(db_session)
    planned_at = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)

    # 调用 claim_due_run（结果不重要，仅验证 SQL）
    await run_repo.claim_due_run(schedule.id, planned_at)

    # 至少应执行过 SELECT FOR UPDATE 查询
    assert len(executed_sqls) >= 1, "claim_due_run 应执行 SELECT FOR UPDATE SKIP LOCKED 查询"

    # 检查 SQL 含 FOR UPDATE 与 SKIP LOCKED 关键字
    # SQLAlchemy 编译 with_for_update(skip_locked=True) 会生成 "FOR UPDATE SKIP LOCKED"
    found_skip_locked = any(
        "for update" in sql.lower() and "skip locked" in sql.lower()
        for sql in executed_sqls
    )
    assert found_skip_locked, (
        "claim_due_run 的 SQL 必须含 FOR UPDATE SKIP LOCKED，"
        "确保多实例并发领取时的安全加锁。"
        f"实际执行的 SQL：{executed_sqls}"
    )


# ============================================================================
# 文档说明：FOR UPDATE SKIP LOCKED 真实并发测试
# ============================================================================
# 真实并发场景下 FOR UPDATE SKIP LOCKED 的语义验证需要：
# 1. 启动多个独立数据库连接（不同 session）
# 2. 在事务中并发调用 claim_due_run
# 3. 验证只有一个实例领取成功，其他实例跳过被锁的行
#
# 此类测试在单元测试中难以实现（需要多进程或多线程事务隔离），
# 推荐通过集成测试或压力测试覆盖：
#   - 使用 locust 或 asyncio.gather 模拟多实例并发
#   - 验证同一 (schedule_id, planned_at) 仅被领取一次
#
# 本测试套件通过 SQL 生成正确性验证（test_claim_due_run_sql_contains_skip_locked）
# 确保代码逻辑正确，真实并发行为依赖 PostgreSQL 的 SKIP LOCKED 特性保证。
