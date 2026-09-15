"""验证 Outbox 与版本发布所需的 PostgreSQL 隔离 Schema、行锁和条件更新语义。"""

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from knowledge_core.config import settings
from knowledge_core.domains.knowledge.repository import KnowledgeRepository
from knowledge_core.domains.operations.repository import OperationRepository
from knowledge_core.shared.context import ApplicationContext
from knowledge_core.workers import tasks


@pytest.mark.asyncio
async def test_temporary_schema_can_be_created_and_removed() -> None:
    """并发集成测试需要隔离 Schema，验证结束后必须完全清理，不触碰业务表。"""
    # UUID 仅包含十六进制字符，拼入 SQL 标识符前缀后仍是受控的安全名称。
    schema = f"aik_outbox_test_{uuid4().hex}"
    # pytest 会为异步用例切换事件循环，临时连接池不能跨用例复用连接。
    test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with test_engine.begin() as connection:
            await connection.execute(text(f"CREATE SCHEMA {schema}"))
        async with test_engine.begin() as connection:
            await connection.execute(text(f"SET search_path TO {schema}"))
            # 测试表只保留租约领取查询所需字段，不会映射或访问任何业务表。
            await connection.execute(
                text(
                    "CREATE TABLE transactional_outbox ("
                    "id uuid PRIMARY KEY, "
                    "created_at timestamptz NOT NULL DEFAULT now(), "
                    "published_at timestamptz, "
                    "lease_expires_at timestamptz)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO transactional_outbox (id) "
                    "VALUES ('00000000-0000-0000-0000-000000000001')"
                )
            )

        # 第一事务持有行锁时，第二事务的 SKIP LOCKED 查询必须拿不到同一事件。
        async with test_engine.connect() as first_connection:
            first_transaction = await first_connection.begin()
            await first_connection.execute(text(f"SET search_path TO {schema}"))
            first_event = await first_connection.scalar(
                text(
                    "SELECT id FROM transactional_outbox "
                    "WHERE published_at IS NULL "
                    "AND (lease_expires_at IS NULL OR lease_expires_at < now()) "
                    "FOR UPDATE SKIP LOCKED"
                )
            )
            assert first_event is not None
            async with test_engine.connect() as second_connection:
                second_transaction = await second_connection.begin()
                await second_connection.execute(text(f"SET search_path TO {schema}"))
                second_event = await second_connection.scalar(
                    text(
                        "SELECT id FROM transactional_outbox "
                        "WHERE published_at IS NULL "
                        "AND (lease_expires_at IS NULL OR lease_expires_at < now()) "
                        "FOR UPDATE SKIP LOCKED"
                    )
                )
                assert second_event is None
                await second_transaction.rollback()
            await first_transaction.rollback()
    finally:
        # 无论断言或连接失败都回收随机测试 Schema，避免残留测试数据。
        async with test_engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await test_engine.dispose()


@pytest.mark.asyncio
async def test_only_one_transaction_can_claim_a_queued_run() -> None:
    """重复投递时，条件更新只能让一个 Worker 把 queued 任务切换为 running。"""
    schema = f"aik_run_test_{uuid4().hex}"
    run_id = UUID("00000000-0000-0000-0000-000000000002")
    application_id = UUID("00000000-0000-0000-0000-000000000012")
    environment_id = UUID("00000000-0000-0000-0000-000000000022")
    context = ApplicationContext(
        application_id=application_id,
        environment_id=environment_id,
        application_code="schema-test",
        environment_code="test",
        api_key_id=UUID(int=0),
        scopes=frozenset({"knowledge:write"}),
    )
    # 与前一用例隔离连接池，避免 asyncpg 连接绑定到已经关闭的事件循环。
    test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with test_engine.begin() as connection:
            await connection.execute(text(f"CREATE SCHEMA {schema}"))
        async with test_engine.begin() as connection:
            await connection.execute(text(f"SET search_path TO {schema}"))
            await connection.execute(
                text(
                    "CREATE TABLE ingestion_runs ("
                    "id uuid PRIMARY KEY, "
                    "application_id uuid NOT NULL, "
                    "environment_id uuid NOT NULL, "
                    "status text NOT NULL, "
                    "started_at timestamptz, "
                    "updated_at timestamptz NOT NULL DEFAULT now())"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO ingestion_runs "
                    "(id, application_id, environment_id, status) "
                    "VALUES (:run_id, :application_id, :environment_id, 'queued')"
                ),
                {
                    "run_id": run_id,
                    "application_id": application_id,
                    "environment_id": environment_id,
                },
            )

        session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

        async def claim_from_duplicate_message() -> bool:
            """模拟收到同一运行 ID 的独立 Celery 消息，调用真实仓库领取方法。"""
            async with session_factory() as session:
                # NullPool 确保每个协程使用独立数据库连接，避免共享事务伪造并发结果。
                await session.execute(text(f"SET search_path TO {schema}"))
                claimed = await KnowledgeRepository(session).claim_queued_run(context, run_id)
                await session.commit()
                return claimed

        # 两条重复消息并发到达时，生产条件更新只允许其中一个消费者开始处理。
        claims = await asyncio.gather(
            claim_from_duplicate_message(), claim_from_duplicate_message()
        )
        assert sorted(claims) == [False, True]
        async with test_engine.begin() as connection:
            await connection.execute(text(f"SET search_path TO {schema}"))
            assert (
                await connection.scalar(
                    text("SELECT status FROM ingestion_runs WHERE id = :run_id"), {"run_id": run_id}
                )
                == "running"
            )
    finally:
        # 该 Schema 只存在于本测试，结束后级联删除不会影响业务数据。
        async with test_engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await test_engine.dispose()


@pytest.mark.asyncio
async def test_late_document_version_cannot_overwrite_concurrently_published_newer_version() -> (
    None
):
    """旧 Worker 与新 Worker 并发发布时，条件更新必须阻止版本指针回退。"""
    schema = f"aik_publish_test_{uuid4().hex}"
    document_id = "00000000-0000-0000-0000-000000000003"
    # 使用独立连接模拟两个 Celery Worker；测试表只保留发布条件所需字段。
    test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with test_engine.begin() as connection:
            await connection.execute(text(f"CREATE SCHEMA {schema}"))
        async with test_engine.begin() as connection:
            await connection.execute(text(f"SET search_path TO {schema}"))
            await connection.execute(
                text("CREATE TABLE documents (id uuid PRIMARY KEY, current_version integer)")
            )
            await connection.execute(
                text(f"INSERT INTO documents (id, current_version) VALUES ('{document_id}', NULL)")
            )

        async with test_engine.connect() as newer_connection:
            newer_transaction = await newer_connection.begin()
            stale_task: asyncio.Task[int] | None = None
            try:
                await newer_connection.execute(text(f"SET search_path TO {schema}"))
                # 新版本先获得文档行锁，但故意不提交，令旧版本发布真正与之并发。
                newer_publish = await newer_connection.execute(
                    text(
                        "UPDATE documents SET current_version = 2 "
                        f"WHERE id = '{document_id}' "
                        "AND (current_version IS NULL OR current_version < 2)"
                    )
                )
                assert newer_publish.rowcount == 1

                stale_ready = asyncio.Event()

                async def publish_stale_version() -> int:
                    """模拟晚完成旧 Worker：事务会先等待新版本释放同一文档行锁。"""
                    async with test_engine.connect() as stale_connection:
                        stale_transaction = await stale_connection.begin()
                        try:
                            await stale_connection.execute(text(f"SET search_path TO {schema}"))
                            stale_ready.set()
                            stale_publish = await stale_connection.execute(
                                text(
                                    "UPDATE documents SET current_version = 1 "
                                    f"WHERE id = '{document_id}' "
                                    "AND (current_version IS NULL OR current_version < 1)"
                                )
                            )
                            await stale_transaction.commit()
                            return int(stale_publish.rowcount or 0)
                        except Exception:
                            await stale_transaction.rollback()
                            raise

                stale_task = asyncio.create_task(publish_stale_version())
                await stale_ready.wait()
                # 旧事务已发起更新但必须等待新事务提交，证明不是顺序伪造的断言。
                await asyncio.sleep(0.05)
                assert not stale_task.done()
                await newer_transaction.commit()

                assert await stale_task == 0
            finally:
                if newer_transaction.is_active:
                    await newer_transaction.rollback()
                if stale_task and not stale_task.done():
                    stale_task.cancel()
                    await asyncio.gather(stale_task, return_exceptions=True)

        async with test_engine.begin() as connection:
            await connection.execute(text(f"SET search_path TO {schema}"))
            published_version = await connection.scalar(
                text(f"SELECT current_version FROM documents WHERE id = '{document_id}'")
            )
            assert published_version == 2
    finally:
        # 随机 Schema 只包含测试表，收尾级联删除不会触碰任何业务数据。
        async with test_engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await test_engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_outbox_dispatchers_publish_one_event_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两个派发 Worker 竞争同一事件时，只能向 Broker 请求一次入库任务。"""
    schema = f"aik_dispatch_test_{uuid4().hex}"
    event_id = uuid4()
    application_id = uuid4()
    environment_id = uuid4()
    run_id = uuid4()
    # 建表和收尾使用未打补丁的 engine；被测派发器会得到固定到临时 Schema 的连接。
    test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
    queued_messages: list[tuple[str, str, str]] = []

    def create_schema_scoped_engine(*args: object, **kwargs: object):
        """让生产派发器的每一条临时连接只搜索本用例的随机 Schema。"""
        connect_args = dict(kwargs.pop("connect_args", {}))
        connect_args["server_settings"] = {"search_path": schema}
        return create_async_engine(*args, connect_args=connect_args, **kwargs)

    def record_broker_message(
        actual_run_id: str, actual_application_id: str, actual_environment_id: str
    ) -> None:
        """记录 Broker 接收意图，避免集成测试连接真实 Celery Broker。"""
        queued_messages.append((actual_run_id, actual_application_id, actual_environment_id))

    try:
        async with test_engine.begin() as connection:
            await connection.execute(text(f"CREATE SCHEMA {schema}"))
        async with test_engine.begin() as connection:
            await connection.execute(text(f"SET search_path TO {schema}"))
            # 字段与实际 Outbox ORM 映射一致，使测试直接执行生产派发代码。
            await connection.execute(
                text(
                    "CREATE TABLE transactional_outbox ("
                    "id uuid PRIMARY KEY, "
                    "created_at timestamptz NOT NULL DEFAULT now(), "
                    "updated_at timestamptz NOT NULL DEFAULT now(), "
                    "application_id uuid NOT NULL, "
                    "environment_id uuid NOT NULL, "
                    "topic varchar(120) NOT NULL, "
                    "payload json NOT NULL, "
                    "published_at timestamptz, "
                    "lease_token varchar(64), "
                    "lease_expires_at timestamptz, "
                    "attempt_count integer NOT NULL DEFAULT 0)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO transactional_outbox "
                    "(id, application_id, environment_id, topic, payload) "
                    "VALUES (:event_id, :application_id, :environment_id, "
                    "'knowledge.ingest_document', CAST(:payload AS json))"
                ),
                {
                    "event_id": event_id,
                    "application_id": application_id,
                    "environment_id": environment_id,
                    "payload": (
                        f'{{"runId":"{run_id}","applicationId":"{application_id}",'
                        f'"environmentId":"{environment_id}"}}'
                    ),
                },
            )

        monkeypatch.setattr(tasks, "create_async_engine", create_schema_scoped_engine)
        monkeypatch.setattr(tasks.ingest_document, "delay", record_broker_message)
        # 两个独立派发器并发执行，分别建立独立连接和事务以覆盖真实锁领取路径。
        first_result, second_result = await asyncio.gather(
            tasks._dispatch_outbox(), tasks._dispatch_outbox()
        )
        assert first_result["published"] + second_result["published"] == 1
        assert queued_messages == [(str(run_id), str(application_id), str(environment_id))]

        # Broker 确认后的标记会让后续扫描幂等，不会再次投递已发布事件。
        assert await tasks._dispatch_outbox() == {"published": 0}
        assert len(queued_messages) == 1
        async with test_engine.begin() as connection:
            await connection.execute(text(f"SET search_path TO {schema}"))
            persisted = await connection.execute(
                text(
                    "SELECT published_at IS NOT NULL AS published, attempt_count, lease_token "
                    "FROM transactional_outbox WHERE id = :event_id"
                ),
                {"event_id": event_id},
            )
            row = persisted.mappings().one()
            assert row["published"] is True
            assert row["attempt_count"] == 1
            assert row["lease_token"] is None
    finally:
        # 仅删除本测试创建的随机 Schema；即使派发断言失败也不保留临时事件。
        async with test_engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await test_engine.dispose()


@pytest.mark.asyncio
async def test_feedback_listing_cannot_cross_application_environment_boundary() -> None:
    """管理员反馈列表只能返回本环境请求，避免通过反馈文本或 requestId 横向窥探。"""
    schema = f"aik_feedback_test_{uuid4().hex}"
    application_id = uuid4()
    environment_id = uuid4()
    other_environment_id = uuid4()
    visible_trace_id = uuid4()
    hidden_trace_id = uuid4()
    test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    try:
        async with test_engine.begin() as connection:
            await connection.execute(text(f"CREATE SCHEMA {schema}"))
        async with test_engine.begin() as connection:
            await connection.execute(text(f"SET search_path TO {schema}"))
            # 最小表结构覆盖 ORM 查询的全部反馈列，并故意插入另一环境的数据。
            await connection.execute(
                text(
                    "CREATE TABLE request_traces ("
                    "id uuid PRIMARY KEY, application_id uuid NOT NULL, "
                    "environment_id uuid NOT NULL, "
                    "request_id varchar(80) NOT NULL)"
                )
            )
            await connection.execute(
                text(
                    "CREATE TABLE user_feedback ("
                    "id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(), "
                    "updated_at timestamptz NOT NULL DEFAULT now(), application_id uuid NOT NULL, "
                    "environment_id uuid NOT NULL, trace_id uuid NOT NULL, "
                    "rating integer NOT NULL, "
                    "reason_code varchar(80), comment text)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO request_traces (id, application_id, environment_id, request_id) "
                    "VALUES (:visible_trace_id, :application_id, :environment_id, 'req_visible'), "
                    "(:hidden_trace_id, :application_id, :other_environment_id, 'req_hidden')"
                ),
                {
                    "visible_trace_id": visible_trace_id,
                    "hidden_trace_id": hidden_trace_id,
                    "application_id": application_id,
                    "environment_id": environment_id,
                    "other_environment_id": other_environment_id,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO user_feedback "
                    "(id, application_id, environment_id, trace_id, rating, reason_code, comment) "
                    "VALUES (:visible_feedback_id, :application_id, :environment_id, "
                    ":visible_trace_id, "
                    "1, 'USEFUL', '可见反馈'), (:hidden_feedback_id, :application_id, "
                    ":other_environment_id, :hidden_trace_id, -1, 'PRIVATE', '不可见反馈')"
                ),
                {
                    "visible_feedback_id": uuid4(),
                    "hidden_feedback_id": uuid4(),
                    "application_id": application_id,
                    "environment_id": environment_id,
                    "other_environment_id": other_environment_id,
                    "visible_trace_id": visible_trace_id,
                    "hidden_trace_id": hidden_trace_id,
                },
            )

        async with session_factory() as session:
            await session.execute(text(f"SET search_path TO {schema}"))
            rows = await OperationRepository(session).list_feedback(application_id, environment_id)

        assert [(feedback.comment, request_id) for feedback, request_id in rows] == [
            ("可见反馈", "req_visible")
        ]
    finally:
        # 只销毁随机 Schema 中的两张最小测试表，不修改开发库业务数据。
        async with test_engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await test_engine.dispose()
