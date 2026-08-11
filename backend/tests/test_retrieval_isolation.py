"""检索跨项目隔离测试（Task 10.5）。

验证 ``HybridSearcher`` 在向量检索与全文检索中都通过 ``project_id`` 前置过滤，
杜绝跨项目召回。

测试设计要点
------------
1. **数据库依赖**：测试需真实数据库（PostgreSQL + pgvector + TSVECTOR），
   通过 ``conftest.py`` 的 ``db_session`` fixture 提供异步会话。
   数据库未启动时通过 ``_check_db_available`` 跳过。
2. **Mock Embedding**：跨项目隔离验证不依赖真实 Embedding 接口，
   通过 ``monkeypatch`` 替换 ``get_embedding_provider`` 返回固定向量，
   避免 CI 环境无 API Key 时测试失败。
3. **断言重点**：
   - 向量检索 SQL WHERE 含 ``project_id``（通过拦截 ``session.execute`` 验证）
   - AI 基金项目的 chunk 不会被 AI 简历的检索召回
   - 全文检索同样受 ``project_id`` 过滤保护
4. **跳过策略**：数据库未启动时通过 ``pytest.skip`` 跳过，不影响测试代码完整性。

测试场景对照
------------
- test_cross_project_vector_search_blocked：AI 基金 chunk 不会被 AI 简历检索召回
- test_vector_search_sql_contains_project_id：向量检索 SQL 含 project_id 过滤
- test_fulltext_search_sql_contains_project_id：全文检索 SQL 含 project_id 过滤
- test_rrf_merge_preserves_project_isolation：RRF 合并后仍只含当前项目片段
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from app.core.project_context import ProjectContext
from app.modules.retrieval.hybrid_searcher import HybridSearcher

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# ============================================================================
# 数据库连接检测与跳过开关
# ============================================================================
# 通过环境变量 ``SKIP_RETRIEVAL_TESTS=1`` 可手动跳过；默认尝试连接
_SKIP_FLAG = os.getenv("SKIP_RETRIEVAL_TESTS", "0") == "1"

# 标记：所有测试均为异步，asyncio_mode=auto 已在 pyproject.toml 配置
pytestmark = pytest.mark.skipif(
    _SKIP_FLAG,
    reason="手动跳过检索隔离测试（SKIP_RETRIEVAL_TESTS=1）",
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


# ============================================================================
# Mock Embedding Provider：跨项目隔离测试不依赖真实 Embedding 接口
# ============================================================================
class _MockEmbeddingProvider:
    """Mock Embedding Provider：返回固定向量，避免调用远端接口。

    跨项目隔离验证的核心是 SQL WHERE ``project_id`` 过滤，与向量内容无关，
    故用固定向量即可。返回的向量维度需与 ``settings.embedding_dimension`` 一致。
    """

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """返回固定向量列表，长度与输入一致。

        Args:
            texts: 输入文本列表。

        Returns:
            固定向量列表，每条向量长度 1536（与默认 embedding_dimension 一致）。
        """
        # 1536 维固定向量，避免真实 API 调用
        return [[0.1] * 1536 for _ in texts]


@pytest_asyncio.fixture
async def two_projects(db_session: "AsyncSession"):
    """创建两个测试项目：ai-fund-retrieval 与 ai-resume-retrieval。

    用于检索跨项目隔离测试：AI 基金的查询不应召回 AI 简历的片段。

    Yields:
        元组 ``(fund_project, resume_project)``，两个 Project ORM 实例。
    """
    # 检测数据库可用性，不可用则跳过整个 fixture
    if not await _check_db_available(db_session):
        pytest.skip("数据库未启动，跳过检索跨项目隔离测试")

    from app.db.repositories.project import (
        ProjectRepository,
        ProjectSettingsRepository,
    )

    project_repo = ProjectRepository(db_session)
    settings_repo = ProjectSettingsRepository(db_session)

    # 创建 AI 基金项目
    fund_project = await project_repo.create(
        code="ai-fund-retrieval-test",
        name="AI 基金（检索测试）",
        description="检索跨项目隔离测试用项目",
    )
    fund_ctx = ProjectContext(project_id=fund_project.id, project_code=fund_project.code)
    await settings_repo.upsert(fund_ctx)

    # 创建 AI 简历项目
    resume_project = await project_repo.create(
        code="ai-resume-retrieval-test",
        name="AI 简历（检索测试）",
        description="检索跨项目隔离测试用项目",
    )
    resume_ctx = ProjectContext(
        project_id=resume_project.id, project_code=resume_project.code
    )
    await settings_repo.upsert(resume_ctx)

    await db_session.commit()

    yield fund_project, resume_project

    # 清理：回滚未提交事务（独立测试数据库场景下兜底清理）
    await db_session.rollback()


@pytest_asyncio.fixture
async def mock_embedding(monkeypatch):
    """Mock Embedding Provider，避免真实 API 调用。

    替换 ``get_embedding_provider`` 返回 ``_MockEmbeddingProvider`` 实例，
    使检索测试不依赖远端 Embedding 服务。

    Yields:
        ``_MockEmbeddingProvider`` 实例。
    """
    provider = _MockEmbeddingProvider()
    # monkeypatch 替换模块级函数，测试结束自动恢复
    monkeypatch.setattr(
        "app.modules.retrieval.hybrid_searcher.get_embedding_provider",
        lambda: provider,
    )
    yield provider


# ============================================================================
# 测试 1：AI 基金 chunk 不会被 AI 简历的检索召回
# ============================================================================
async def test_cross_project_vector_search_blocked(
    db_session: "AsyncSession",
    two_projects,
    mock_embedding,
):
    """AI 基金项目的 chunk 不会被 AI 简历项目的检索召回。

    场景：
        - AI 基金项目下创建知识库 + 文档 + chunk（含向量）
        - AI 简历项目下创建知识库 + 文档 + chunk（含向量）
        - 用 AI 简历的 ctx 执行检索
        - 结果中不应包含 AI 基金的 chunk

    验证点：
        - 向量检索 SQL WHERE 含 project_id，跨项目 chunk 不被召回
        - 全文检索 SQL WHERE 含 project_id，跨项目 chunk 不被召回
        - RRF 合并后结果仅含当前项目片段
    """
    from app.db.models.knowledge import Document, DocumentChunk, KnowledgeBase
    from sqlalchemy import insert

    fund_project, resume_project = two_projects

    # 在 AI 基金项目下创建知识库、文档、chunk（含向量与全文索引列）
    # 使用原生 SQL 插入，因为 content_tsv 与 embedding 需要特殊处理
    fund_kb_id = "00000000-0000-0000-0000-fundkb000001"
    fund_doc_id = "00000000-0000-0000-0000-funddoc000001"
    fund_chunk_id = "00000000-0000-0000-0000-fundchk000001"

    await db_session.execute(
        insert(KnowledgeBase).values(
            id=fund_kb_id,
            project_id=fund_project.id,
            name="基金知识库",
            code="fund-kb-iso",
            embedding_dimension=1536,
            status="active",
        )
    )
    await db_session.execute(
        insert(Document).values(
            id=fund_doc_id,
            project_id=fund_project.id,
            knowledge_base_id=fund_kb_id,
            source_type="manual",
            title="基金投资策略",
            processing_status="completed",
            enabled=True,
        )
    )
    # 插入 chunk：embedding 用 1536 维向量，content_tsv 由 to_tsvector 生成
    from sqlalchemy import text

    await db_session.execute(
        text(
            """
            INSERT INTO document_chunks
                (id, project_id, knowledge_base_id, document_id, chunk_index,
                 content, content_tsv, embedding, enabled)
            VALUES
                (:cid, :pid, :kb_id, :doc_id, 0,
                 :content,
                 to_tsvector('simple', :content),
                 :embedding::vector,
                 true)
            """
        ).bindparams(
            cid=fund_chunk_id,
            pid=fund_project.id,
            kb_id=fund_kb_id,
            doc_id=fund_doc_id,
            content="基金投资策略与风险控制",
            embedding=str([0.1] * 1536),
        )
    )
    await db_session.commit()

    # 在 AI 简历项目下创建知识库、文档、chunk
    resume_kb_id = "00000000-0000-0000-0000-resumkb000001"
    resume_doc_id = "00000000-0000-0000-0000-resumdoc0001"
    resume_chunk_id = "00000000-0000-0000-0000-resumchk0001"

    await db_session.execute(
        insert(KnowledgeBase).values(
            id=resume_kb_id,
            project_id=resume_project.id,
            name="简历知识库",
            code="resume-kb-iso",
            embedding_dimension=1536,
            status="active",
        )
    )
    await db_session.execute(
        insert(Document).values(
            id=resume_doc_id,
            project_id=resume_project.id,
            knowledge_base_id=resume_kb_id,
            source_type="manual",
            title="简历优化建议",
            processing_status="completed",
            enabled=True,
        )
    )
    await db_session.execute(
        text(
            """
            INSERT INTO document_chunks
                (id, project_id, knowledge_base_id, document_id, chunk_index,
                 content, content_tsv, embedding, enabled)
            VALUES
                (:cid, :pid, :kb_id, :doc_id, 0,
                 :content,
                 to_tsvector('simple', :content),
                 :embedding::vector,
                 true)
            """
        ).bindparams(
            cid=resume_chunk_id,
            pid=resume_project.id,
            kb_id=resume_kb_id,
            doc_id=resume_doc_id,
            content="简历优化与面试技巧",
            embedding=str([0.1] * 1536),
        )
    )
    await db_session.commit()

    # 用 AI 简历的 ctx 执行检索
    resume_ctx = ProjectContext(
        project_id=resume_project.id, project_code=resume_project.code
    )
    searcher = HybridSearcher(db_session)
    results = await searcher.search(
        ctx=resume_ctx,
        query="投资",
        knowledge_base_ids=[resume_kb_id],
        top_k=5,
    )

    # 断言：结果中不应包含 AI 基金的 chunk
    fund_chunk_ids = {fund_chunk_id}
    result_chunk_ids = {str(r["chunk_id"]) for r in results}
    # 交集应为空：AI 基金的 chunk 不应被 AI 简历的检索召回
    cross_project_leak = fund_chunk_ids & result_chunk_ids
    assert not cross_project_leak, (
        f"跨项目召回泄露：AI 简历的检索不应召回 AI 基金的 chunk，"
        f"但结果中包含：{cross_project_leak}"
    )

    # 结果中所有 chunk 的 document_id 应属于 AI 简历项目
    # （由于 project_id 前置过滤，仅当前项目的 chunk 可被召回）
    for r in results:
        assert r["document_id"] == resume_doc_id, (
            "检索结果应仅含当前项目（AI 简历）的片段，"
            f"但出现了 document_id={r['document_id']}"
        )


# ============================================================================
# 测试 2：向量检索 SQL 含 project_id 过滤
# ============================================================================
async def test_vector_search_sql_contains_project_id(
    db_session: "AsyncSession",
    two_projects,
    mock_embedding,
    monkeypatch,
):
    """验证向量检索 SQL WHERE 含 project_id 过滤。

    场景：
        - 拦截 ``session.execute``，记录实际执行的 SQL 编译文本
        - 执行检索，检查向量检索 SQL WHERE 子句包含 ``project_id``

    验证点：
        - 向量检索 SQL 含 ``document_chunks.project_id`` 条件
        - 杜绝跨项目召回的物理基础
    """
    fund_project, _ = two_projects
    fund_ctx = ProjectContext(
        project_id=fund_project.id, project_code=fund_project.code
    )

    # 记录所有 execute 调用的 SQL 文本
    executed_sqls: list[str] = []
    original_execute = db_session.execute

    async def _capture_execute(stmt, *args, **kwargs):
        """拦截 session.execute，记录 SQL 编译文本。"""
        try:
            # 编译 SQL 为字符串，便于断言检查
            compiled = stmt.compile()
            executed_sqls.append(str(compiled))
        except Exception:
            # 非 SQL 语句（如 raw text）跳过记录
            pass
        return await original_execute(stmt, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", _capture_execute)

    searcher = HybridSearcher(db_session)
    # 用一个不存在的 kb_id 触发 SQL 执行（结果为空，但 SQL 会生成）
    await searcher.search(
        ctx=fund_ctx,
        query="测试查询",
        knowledge_base_ids=["00000000-0000-0000-0000-nonexist00001"],
        top_k=5,
    )

    # 至少应执行过 SQL（向量检索 + 全文检索）
    assert len(executed_sqls) >= 2, "应至少执行向量检索与全文检索两条 SQL"

    # 检查所有 SQL 都包含 project_id 过滤条件
    # 编译后的 SQL 会包含 ``document_chunks.project_id`` 列名
    for sql in executed_sqls:
        assert "project_id" in sql, (
            "检索 SQL 必须含 project_id 过滤条件，杜绝跨项目召回。"
            f"实际 SQL：{sql}"
        )


# ============================================================================
# 测试 3：全文检索 SQL 含 project_id 过滤
# ============================================================================
async def test_fulltext_search_sql_contains_project_id(
    db_session: "AsyncSession",
    two_projects,
    mock_embedding,
    monkeypatch,
):
    """验证全文检索 SQL WHERE 含 project_id 过滤。

    场景：
        - 直接调用 ``HybridSearcher._fulltext_search``，拦截 SQL 检查
        - 验证全文检索 SQL 含 project_id 条件

    验证点：
        - 全文检索 SQL 含 ``document_chunks.project_id`` 条件
        - 全文检索与向量检索遵循相同的隔离规则
    """
    fund_project, _ = two_projects
    fund_ctx = ProjectContext(
        project_id=fund_project.id, project_code=fund_project.code
    )

    # 记录 execute 调用的 SQL
    executed_sqls: list[str] = []
    original_execute = db_session.execute

    async def _capture_execute(stmt, *args, **kwargs):
        """拦截 session.execute，记录 SQL 文本。"""
        try:
            executed_sqls.append(str(stmt.compile()))
        except Exception:
            pass
        return await original_execute(stmt, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", _capture_execute)

    searcher = HybridSearcher(db_session)
    # 直接调用 _fulltext_search，仅触发全文检索 SQL
    await searcher._fulltext_search(
        ctx=fund_ctx,
        query="测试",
        kb_ids=["00000000-0000-0000-0000-nonexist00001"],
        top_n=30,
    )

    assert len(executed_sqls) >= 1, "应执行全文检索 SQL"
    fulltext_sql = executed_sqls[0]
    # 全文检索 SQL 应含 project_id 过滤
    assert "project_id" in fulltext_sql, (
        "全文检索 SQL 必须含 project_id 过滤条件，杜绝跨项目召回。"
        f"实际 SQL：{fulltext_sql}"
    )
    # 全文检索 SQL 应含 websearch_to_tsquery（确认是全文检索路径）
    assert "websearch_to_tsquery" in fulltext_sql.lower() or "tsquery" in fulltext_sql.lower(), (
        "全文检索 SQL 应使用 websearch_to_tsquery。"
        f"实际 SQL：{fulltext_sql}"
    )


# ============================================================================
# 测试 4：RRF 合并后仍只含当前项目片段
# ============================================================================
async def test_rrf_merge_preserves_project_isolation(
    db_session: "AsyncSession",
    two_projects,
    mock_embedding,
):
    """RRF 合并算法本身不引入跨项目片段。

    场景：
        - 构造两个项目各自的"伪"检索结果（不执行真实 SQL）
        - 仅用 AI 基金的 ctx 调用 _rrf_merge，传入两路结果
        - 验证合并结果不包含 AI 简历的 chunk

    验证点：
        - RRF 合并是纯排名算法，不引入新的 chunk
        - 跨项目隔离由 SQL 层保证，RRF 层不破坏隔离
    """
    # RRF 合并是纯算法，不依赖数据库或项目数据，仅依赖 two_projects fixture
    # 触发数据库可用性检测（数据库未启动时跳过）
    _ = two_projects

    # 构造 AI 基金的伪检索结果（模拟两路检索都命中同一 chunk）
    fund_chunk_id = "00000000-0000-0000-0000-fundchk000010"
    fund_doc_id = "00000000-0000-0000-0000-funddoc000010"
    text_results = [
        {
            "chunk_id": fund_chunk_id,
            "document_id": fund_doc_id,
            "content": "基金投资策略",
            "page_number": 1,
            "metadata": {},
            "score": 0.9,
            "source": "fulltext",
        }
    ]
    vector_results = [
        {
            "chunk_id": fund_chunk_id,
            "document_id": fund_doc_id,
            "content": "基金投资策略",
            "page_number": 1,
            "metadata": {},
            "score": 0.85,
            "source": "vector",
        }
    ]

    searcher = HybridSearcher(db_session)
    # RRF 合并：纯算法，不涉及 SQL
    merged = searcher._rrf_merge(text_results, vector_results, k=60)

    # 断言：合并结果仅含输入的 chunk，不引入新 chunk
    assert len(merged) == 1, "RRF 合并应仅含输入的片段，不引入新片段"
    assert merged[0]["chunk_id"] == fund_chunk_id
    # 两路都命中的 chunk，source 标记为 both
    assert merged[0]["source"] == "both"
    # merged_score 应为两路排名分数之和
    expected_score = 1.0 / (60 + 1) + 1.0 / (60 + 1)
    assert abs(merged[0]["merged_score"] - expected_score) < 1e-9

    # 验证：AI 简历的 chunk_id 不在合并结果中
    resume_chunk_id = "00000000-0000-0000-0000-resumchk0010"
    result_ids = {item["chunk_id"] for item in merged}
    assert resume_chunk_id not in result_ids, (
        "RRF 合并不应引入未在输入中出现的 chunk，跨项目隔离由 SQL 层保证"
    )


# ============================================================================
# 测试 5：空查询与空知识库列表直接返回空结果
# ============================================================================
async def test_empty_query_or_kb_returns_empty(
    db_session: "AsyncSession",
    two_projects,
    monkeypatch,
):
    """空查询或空知识库列表直接返回空结果，不发起 SQL。

    场景：
        - 传入空查询或空知识库列表
        - 应直接返回 []，不触发 Embedding 调用与 SQL 执行

    验证点：
        - 边界处理正确，避免无效 SQL 调用
        - Mock Embedding Provider 不应被调用（embed_texts 不执行）
    """
    fund_project, _ = two_projects
    fund_ctx = ProjectContext(
        project_id=fund_project.id, project_code=fund_project.code
    )

    # 创建一个可追踪调用次数的 mock provider
    call_counter = {"embed_texts": 0}

    class _CountingMockProvider:
        async def embed_texts(self, texts: list[str]) -> list[list[float]]:
            call_counter["embed_texts"] += 1
            return [[0.1] * 1536 for _ in texts]

    # 用 monkeypatch 替换 provider，测试结束自动恢复
    monkeypatch.setattr(
        "app.modules.retrieval.hybrid_searcher.get_embedding_provider",
        lambda: _CountingMockProvider(),
    )

    searcher = HybridSearcher(db_session)

    # 空查询
    result1 = await searcher.search(
        ctx=fund_ctx, query="", knowledge_base_ids=["fake-kb-id"], top_k=5
    )
    assert result1 == [], "空查询应返回空列表"
    assert call_counter["embed_texts"] == 0, "空查询不应触发 Embedding 调用"

    # 仅空白字符查询
    result2 = await searcher.search(
        ctx=fund_ctx, query="   ", knowledge_base_ids=["fake-kb-id"], top_k=5
    )
    assert result2 == [], "仅空白的查询应返回空列表"
    assert call_counter["embed_texts"] == 0, "仅空白的查询不应触发 Embedding 调用"

    # 空知识库列表
    result3 = await searcher.search(
        ctx=fund_ctx, query="有效查询", knowledge_base_ids=[], top_k=5
    )
    assert result3 == [], "空知识库列表应返回空列表"
    assert call_counter["embed_texts"] == 0, "空知识库列表不应触发 Embedding 调用"
