"""运行中心概览的时间窗口、完成态分母与环境隔离集成测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from knowledge_core.config import settings
from knowledge_core.domains.operations.repository import OperationRepository


@pytest.mark.asyncio
async def test_summary_excludes_running_requests_from_success_rate_and_honors_window() -> None:
    """成功率只按窗口内完成请求计算，并绝不读取其他环境或历史记录。"""
    schema = f"aik_operation_summary_{uuid4().hex}"
    application_id = UUID("00000000-0000-0000-0000-000000000101")
    environment_id = UUID("00000000-0000-0000-0000-000000000102")
    other_environment_id = UUID("00000000-0000-0000-0000-000000000103")
    now = datetime.now(UTC)
    test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    try:
        async with test_engine.begin() as connection:
            await connection.execute(text(f"CREATE SCHEMA {schema}"))
            await connection.execute(text(f"SET search_path TO {schema}"))
            # 表只保留聚合 SQL 所需列，随机 Schema 不映射、修改或删除业务表。
            await connection.execute(
                text(
                    "CREATE TABLE request_traces ("
                    "id uuid PRIMARY KEY, application_id uuid NOT NULL, "
                    "environment_id uuid NOT NULL, "
                    "status varchar(24) NOT NULL, answer_mode varchar(40), total_ms integer, "
                    "created_at timestamptz NOT NULL)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO request_traces "
                    "(id, application_id, environment_id, status, answer_mode, total_ms, "
                    "created_at) VALUES (:id, :application_id, :environment_id, :status, "
                    ":answer_mode, :total_ms, :created_at)"
                ),
                [
                    # 当前窗口的成功、失败和运行中请求构成目标统计集。
                    {
                        "id": UUID("00000000-0000-0000-0000-000000000201"),
                        "application_id": application_id,
                        "environment_id": environment_id,
                        "status": "succeeded",
                        "answer_mode": "MODEL_ONLY",
                        "total_ms": 120,
                        "created_at": now - timedelta(minutes=5),
                    },
                    {
                        "id": UUID("00000000-0000-0000-0000-000000000202"),
                        "application_id": application_id,
                        "environment_id": environment_id,
                        "status": "failed",
                        "answer_mode": None,
                        "total_ms": 300,
                        "created_at": now - timedelta(minutes=4),
                    },
                    {
                        "id": UUID("00000000-0000-0000-0000-000000000203"),
                        "application_id": application_id,
                        "environment_id": environment_id,
                        "status": "running",
                        "answer_mode": None,
                        "total_ms": None,
                        "created_at": now - timedelta(minutes=3),
                    },
                    # 未知终态仍计入总请求，但不能被推导为运行中请求。
                    {
                        "id": UUID("00000000-0000-0000-0000-000000000206"),
                        "application_id": application_id,
                        "environment_id": environment_id,
                        "status": "cancelled",
                        "answer_mode": None,
                        "total_ms": None,
                        "created_at": now - timedelta(minutes=2),
                    },
                    # 历史请求和其他环境请求不能进入当前窗口的任何分子或分母。
                    {
                        "id": UUID("00000000-0000-0000-0000-000000000204"),
                        "application_id": application_id,
                        "environment_id": environment_id,
                        "status": "succeeded",
                        "answer_mode": "MODEL_ONLY",
                        "total_ms": 900,
                        "created_at": now - timedelta(days=2),
                    },
                    {
                        "id": UUID("00000000-0000-0000-0000-000000000205"),
                        "application_id": application_id,
                        "environment_id": other_environment_id,
                        "status": "succeeded",
                        "answer_mode": "MODEL_ONLY",
                        "total_ms": 10,
                        "created_at": now - timedelta(minutes=2),
                    },
                ],
            )

        async with session_factory() as session:
            await session.execute(text(f"SET search_path TO {schema}"))
            result = await OperationRepository(session).summary(
                application_id,
                environment_id,
                window_started_at=now - timedelta(hours=1),
                window_ended_at=now,
            )

        assert result["totalRequests"] == 4
        assert result["completedRequests"] == 2
        assert result["runningRequests"] == 1
        assert result["failedRequests"] == 1
        assert result["successRate"] == 0.5
        assert result["averageDurationMs"] == 120
        assert result["modelFallbackRate"] == 0.5
    finally:
        # Schema 名只来自 uuid4 的十六进制值，清理范围严格限制为本次临时资源。
        async with test_engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await test_engine.dispose()


@pytest.mark.asyncio
async def test_ingestion_attempts_are_isolated_and_filterable() -> None:
    """运行中心读取入库尝试时只返回当前环境，并支持按任务和状态定位。"""
    schema = f"aik_ingestion_attempts_{uuid4().hex}"
    application_id = UUID("00000000-0000-0000-0000-000000000301")
    environment_id = UUID("00000000-0000-0000-0000-000000000302")
    other_environment_id = UUID("00000000-0000-0000-0000-000000000303")
    run_id = UUID("00000000-0000-0000-0000-000000000311")
    now = datetime.now(UTC)
    test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with test_engine.begin() as connection:
            await connection.execute(text(f"CREATE SCHEMA {schema}"))
            await connection.execute(text(f"SET search_path TO {schema}"))
            # 只建被测查询需要的字段，随机 Schema 不映射业务数据库表。
            await connection.execute(
                text(
                    "CREATE TABLE ingestion_runs ("
                    "id uuid PRIMARY KEY, application_id uuid NOT NULL, "
                    "environment_id uuid NOT NULL, request_id varchar(80) NOT NULL)"
                )
            )
            await connection.execute(
                text(
                    "CREATE TABLE ingestion_attempts ("
                    "id uuid PRIMARY KEY, created_at timestamptz NOT NULL, "
                    "updated_at timestamptz NOT NULL, application_id uuid NOT NULL, "
                    "environment_id uuid NOT NULL, run_id uuid NOT NULL, "
                    "attempt_number integer NOT NULL, status varchar(24) NOT NULL, "
                    "stage varchar(24) NOT NULL, error_code varchar(80), "
                    "error_message text, started_at timestamptz NOT NULL, "
                    "completed_at timestamptz)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO ingestion_runs (id, application_id, environment_id, request_id) "
                    "VALUES (:run_id, :application_id, :environment_id, 'ingest-current')"
                ),
                {
                    "run_id": run_id,
                    "application_id": application_id,
                    "environment_id": environment_id,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO ingestion_attempts ("
                    "id, created_at, updated_at, application_id, environment_id, run_id, "
                    "attempt_number, status, stage, error_code, error_message, started_at, "
                    "completed_at) "
                    "VALUES (:id, :created_at, :updated_at, :application_id, :environment_id, "
                    ":run_id, :attempt_number, :status, :stage, :error_code, :error_message, "
                    ":started_at, :completed_at)"
                ),
                {
                    "id": uuid4(),
                    "created_at": now,
                    "updated_at": now,
                    "application_id": application_id,
                    "environment_id": environment_id,
                    "run_id": run_id,
                    "attempt_number": 1,
                    "status": "failed",
                    "stage": "embedding",
                    "error_code": "PROVIDER_UNAVAILABLE",
                    "error_message": "已脱敏错误摘要",
                    "started_at": now,
                    "completed_at": now,
                },
            )
            # 同一任务的成功重试和另一环境记录用于检验筛选边界。
            await connection.execute(
                text(
                    "INSERT INTO ingestion_attempts ("
                    "id, created_at, updated_at, application_id, environment_id, run_id, "
                    "attempt_number, status, stage, started_at) VALUES "
                    "(:id, :created_at, :updated_at, :application_id, :environment_id, :run_id, "
                    "2, 'succeeded', 'published', :started_at)"
                ),
                {
                    "id": uuid4(),
                    "created_at": now + timedelta(seconds=1),
                    "updated_at": now + timedelta(seconds=1),
                    "application_id": application_id,
                    "environment_id": environment_id,
                    "run_id": run_id,
                    "started_at": now + timedelta(seconds=1),
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO ingestion_runs (id, application_id, environment_id, request_id) "
                    "VALUES ('00000000-0000-0000-0000-000000000312', :application_id, "
                    ":environment_id, 'ingest-other')"
                ),
                {"application_id": application_id, "environment_id": other_environment_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO ingestion_attempts ("
                    "id, created_at, updated_at, application_id, environment_id, run_id, "
                    "attempt_number, status, stage, started_at) VALUES "
                    "('00000000-0000-0000-0000-000000000313', :created_at, :updated_at, "
                    ":application_id, :environment_id, '00000000-0000-0000-0000-000000000312', "
                    "1, 'failed', 'parsing', :started_at)"
                ),
                {
                    "created_at": now,
                    "updated_at": now,
                    "application_id": application_id,
                    "environment_id": other_environment_id,
                    "started_at": now,
                },
            )

        async with test_engine.connect() as connection:
            await connection.execute(text(f"SET search_path TO {schema}"))
            session_factory = async_sessionmaker(
                connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            async with session_factory() as session:
                repository = OperationRepository(session)
                rows = await repository.list_ingestion_attempts(application_id, environment_id)
                assert len(rows) == 2
                failed = await repository.list_ingestion_attempts(
                    application_id, environment_id, run_id=run_id, status="failed"
                )
                assert len(failed) == 1
                assert failed[0][1] == "ingest-current"
                assert failed[0][0].error_code == "PROVIDER_UNAVAILABLE"
    finally:
        async with test_engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await test_engine.dispose()
