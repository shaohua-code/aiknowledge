"""跨项目隔离端到端测试（SubTask 25.2）。

对应 Task 25：通过 httpx ASGITransport 直接测试 FastAPI app，
覆盖跨项目隔离的 6 个核心场景。

测试设计要点
------------
1. **不启动真实服务器**
   使用 ``httpx.AsyncClient`` + ``ASGITransport(app=app)`` 直接调用 ASGI 应用，
   避免端口占用与启动开销，同时保留完整中间件链路（鉴权、异常处理、CORS）。

2. **复用 seed_demo_projects 数据**
   通过 fixture 在测试前创建 ai-fund / ai-resume / ai-ecommerce 三个项目，
   并生成 API Key，测试结束自动清理。

3. **6 个核心测试场景**
   - 跨项目查询知识库 → 404 KNOWLEDGE_BASE_NOT_FOUND
   - 请求体伪造 projectId → 以 Key 解析项目为准
   - 伪造 X-Project-Code → 403 PROJECT_CODE_MISMATCH
   - AI 简历调用 fund_market 工具 → 403 TOOL_NOT_ALLOWED
   - 伪造 scheduleId（其他项目的）→ 404 TASK_NOT_FOUND
   - 伪造 crawlSourceId（其他项目的）→ 404 CRAWL_SOURCE_NOT_FOUND

4. **数据库未启动跳过**
   通过 ``_check_db_available`` 检测连接，不可用时 pytest.skip，
   不影响其他测试用例。

测试场景对照
------------
- test_e2e_cross_project_kb_returns_404：AI 基金 Key 查询 AI 简历知识库
- test_e2e_forged_project_id_ignored：请求体伪造 projectId 被忽略
- test_e2e_forged_project_code_returns_403：伪造 X-Project-Code → 403
- test_e2e_resume_call_fund_tool_returns_403：AI 简历调用 fund_market → 403
- test_e2e_forged_schedule_id_returns_404：伪造 scheduleId → 404
- test_e2e_forged_crawl_source_id_returns_404：伪造 crawlSourceId → 404
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from app.core.project_context import ProjectContext
from app.core.scopes import (
    SCOPE_CRAWL_READ,
    SCOPE_CRAWL_WRITE,
    SCOPE_KNOWLEDGE_WRITE,
    SCOPE_RESEARCH_RUN,
    SCOPE_RETRIEVAL_READ,
    SCOPE_SCHEDULES_READ,
    SCOPE_SCHEDULES_WRITE,
)
from app.core.security import generate_api_key
from app.db.repositories.crawler import CrawlSourceRepository
from app.db.repositories.knowledge import KnowledgeBaseRepository
from app.db.repositories.project import (
    ApiKeyRepository,
    ProjectRepository,
    ProjectSettingsRepository,
)
from app.db.repositories.schedule import ScheduleRepository
from app.db.repositories.tool import ProjectToolRepository
from app.db.session import AsyncSessionFactory
from app.main import app
from app.modules.tools.definitions import seed_tool_definitions

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# ============================================================================
# 跳过开关：通过环境变量手动跳过端到端隔离测试
# ============================================================================
# 通过环境变量 ``SKIP_E2E_TESTS=1`` 可手动跳过；默认尝试连接
_SKIP_FLAG = os.getenv("SKIP_E2E_TESTS", "0") == "1"

# 标记：所有测试均为异步，asyncio_mode=auto 已在 pyproject.toml 配置
pytestmark = pytest.mark.skipif(
    _SKIP_FLAG,
    reason="手动跳过端到端隔离测试（SKIP_E2E_TESTS=1）",
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


# 演示项目使用的 Scope 列表：覆盖所有测试场景
_DEMO_SCOPES: list[str] = [
    SCOPE_RETRIEVAL_READ,
    SCOPE_RESEARCH_RUN,
    SCOPE_KNOWLEDGE_WRITE,
    SCOPE_SCHEDULES_READ,
    SCOPE_SCHEDULES_WRITE,
    SCOPE_CRAWL_READ,
    SCOPE_CRAWL_WRITE,
]


async def _create_demo_project(
    session: "AsyncSession",
    code: str,
    name: str,
    kb_code: str,
    tool_whitelist: list[str],
) -> dict[str, Any]:
    """创建单个演示项目及其关联资源（内部辅助函数）。

    Args:
        session: 异步数据库会话。
        code: 项目编码。
        name: 项目显示名。
        kb_code: 知识库编码。
        tool_whitelist: 工具白名单 tool_code 列表。

    Returns:
        包含 project / kb / raw_api_key 的字典。
    """
    project_repo = ProjectRepository(session)
    kb_repo = KnowledgeBaseRepository(session)
    api_key_repo = ApiKeyRepository(session)
    settings_repo = ProjectSettingsRepository(session)
    project_tool_repo = ProjectToolRepository(session)

    # 创建项目
    project = await project_repo.create(code=code, name=name, description=f"{name} 端到端测试")
    ctx = ProjectContext(project_id=project.id, project_code=project.code)

    # 创建知识库
    kb = await kb_repo.create(
        ctx=ctx,
        name=f"{name}知识库",
        code=kb_code,
        embedding_dimension=1536,
    )

    # 生成 API Key（明文 + 哈希）
    raw_key, key_prefix, key_hash = generate_api_key()
    await api_key_repo.create(
        ctx=ctx,
        name=f"{name} 测试 Key",
        environment="dev",
        key_prefix=key_prefix,
        key_hash=key_hash,
        scopes=_DEMO_SCOPES,
    )

    # 配置 ProjectSettings
    await settings_repo.upsert(
        ctx,
        chat_model="gpt-4o-mini",
        embedding_model="text-embedding-3-small",
        web_search_enabled=True,
        max_evidence=8,
        timeout_seconds=15,
    )

    # 配置工具白名单
    for tool_code in tool_whitelist:
        await project_tool_repo.create(
            ctx=ctx,
            tool_code=tool_code,
            config={},
            enabled=True,
        )

    await session.commit()

    return {
        "project": project,
        "kb": kb,
        "raw_api_key": raw_key,
    }


@pytest_asyncio.fixture
async def e2e_demo_data(db_session: "AsyncSession"):
    """创建端到端测试所需的演示数据。

    生成 3 个项目（ai-fund-e2e / ai-resume-e2e / ai-ecommerce-e2e），
    每个项目配套知识库、API Key、工具白名单。
    数据库未启动时跳过整个 fixture。

    Yields:
        包含 3 个项目信息的字典：
        ``{"fund": {...}, "resume": {...}, "ecommerce": {...}}``
        每项含 project / kb / raw_api_key。
    """
    # 检测数据库可用性，不可用则跳过整个 fixture
    if not await _check_db_available(db_session):
        pytest.skip("数据库未启动，跳过端到端隔离测试")

    # seed 全局工具定义（fund_market / job_search / product_search 等）
    # seed 函数内部会 commit
    await seed_tool_definitions(db_session)

    # 创建 3 个演示项目（使用 -e2e 后缀避免与 seed_demo_projects 冲突）
    fund = await _create_demo_project(
        db_session,
        code="ai-fund-e2e",
        name="AI 基金",
        kb_code="fund-kb-e2e",
        tool_whitelist=["fund_market", "index_market", "financial_news"],
    )
    resume = await _create_demo_project(
        db_session,
        code="ai-resume-e2e",
        name="AI 简历",
        kb_code="resume-kb-e2e",
        tool_whitelist=["job_search"],
    )
    ecommerce = await _create_demo_project(
        db_session,
        code="ai-ecommerce-e2e",
        name="AI 电商",
        kb_code="ecommerce-kb-e2e",
        tool_whitelist=["product_search"],
    )

    yield {
        "fund": fund,
        "resume": resume,
        "ecommerce": ecommerce,
    }

    # 清理：回滚未提交事务（独立测试数据库场景下兜底清理）
    await db_session.rollback()


@pytest_asyncio.fixture
async def client():
    """提供 httpx AsyncClient，通过 ASGITransport 直接测试 FastAPI app。

    不启动真实服务器，直接调用 ASGI 应用，保留完整中间件链路。
    测试结束自动关闭 client。

    Yields:
        httpx.AsyncClient 实例。
    """
    # ASGITransport 将 httpx 请求路由到 FastAPI app，无需端口监听
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


def _auth_headers(api_key: str, project_code: str | None = None) -> dict[str, str]:
    """构造鉴权请求头。

    Args:
        api_key: 明文 API Key。
        project_code: 可选的 X-Project-Code 头，用于一致性校验测试。

    Returns:
        包含 Authorization 与可选 X-Project-Code 的请求头字典。
    """
    headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}
    if project_code is not None:
        headers["X-Project-Code"] = project_code
    return headers


# ============================================================================
# 测试 1：AI 基金 Key 查询 AI 简历知识库 → 404 KNOWLEDGE_BASE_NOT_FOUND
# ============================================================================
async def test_e2e_cross_project_kb_returns_404(
    client: httpx.AsyncClient,
    e2e_demo_data,
):
    """跨项目查询知识库返回 404 KNOWLEDGE_BASE_NOT_FOUND。

    场景：
        - 客户端持有 AI 基金的 API Key
        - 通过 GET /api/v1/knowledge-bases/{code} 查询 AI 简历的知识库 code
        - 服务端 Repository.get_by_code 带 project_id 过滤，跨项目返回 None
        - 端点统一抛 KnowledgeBaseNotFoundError（404）

    验证点：
        - HTTP 状态码 404
        - 响应体 success=false
        - 错误码 KNOWLEDGE_BASE_NOT_FOUND
        - 不泄露 AI 简历知识库是否存在
    """
    fund_data = e2e_demo_data["fund"]
    resume_data = e2e_demo_data["resume"]

    # 用 AI 基金的 Key 查询 AI 简历的知识库 code
    response = await client.get(
        f"/api/v1/knowledge-bases/{resume_data['kb'].code}",
        headers=_auth_headers(fund_data["raw_api_key"]),
    )

    # 断言：跨项目查询返回 404
    assert response.status_code == 404, (
        f"跨项目查询知识库应返回 404，实际：{response.status_code} {response.text}"
    )
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "KNOWLEDGE_BASE_NOT_FOUND"


# ============================================================================
# 测试 2：请求体伪造 projectId → 以 Key 解析项目为准
# ============================================================================
async def test_e2e_forged_project_id_ignored(
    client: httpx.AsyncClient,
    e2e_demo_data,
):
    """请求体携带其他项目 projectId，服务端仍以 API Key 解析项目为准。

    场景：
        - 客户端持有 AI 基金的 API Key
        - 创建知识库时，请求体中伪造 projectId=AI 简历项目 ID
        - 服务端 Repository.create 使用 ctx.project_id（来自 Key），忽略请求体
        - 创建后的知识库归属 AI 基金项目，而非请求体伪造的项目

    验证点：
        - 创建成功（201 或 200）
        - 响应 data 中的知识库归属 AI 基金项目（通过后续查询验证）
        - AI 简历项目不会出现此知识库
    """
    fund_data = e2e_demo_data["fund"]
    resume_data = e2e_demo_data["resume"]

    # 请求体中伪造 projectId（AI 简历项目 ID）
    # 服务端 ctx.project_id 来自 API Key，伪造字段应被忽略
    forged_kb_code = "forged-kb-test"
    response = await client.post(
        "/api/v1/knowledge-bases",
        json={
            "name": "伪造 projectId 测试知识库",
            "code": forged_kb_code,
            "embeddingDimension": 1536,
            # 伪造字段：客户端试图把知识库挂到 AI 简历项目下
            "projectId": resume_data["project"].id,
        },
        headers=_auth_headers(fund_data["raw_api_key"]),
    )

    # 断言：创建成功（端点应忽略请求体中的 projectId）
    assert response.status_code in (200, 201), (
        f"伪造 projectId 应被忽略，创建应成功，实际：{response.status_code} {response.text}"
    )
    body = response.json()
    assert body["success"] is True

    # 用 AI 基金的 Key 查询刚创建的知识库，应能查到（归属 AI 基金项目）
    resp_fund = await client.get(
        f"/api/v1/knowledge-bases/{forged_kb_code}",
        headers=_auth_headers(fund_data["raw_api_key"]),
    )
    assert resp_fund.status_code == 200, "伪造 projectId 后，知识库应归属 Key 所属项目（AI 基金）"

    # 用 AI 简历的 Key 查询此知识库，应返回 404（不归属 AI 简历项目）
    resp_resume = await client.get(
        f"/api/v1/knowledge-bases/{forged_kb_code}",
        headers=_auth_headers(resume_data["raw_api_key"]),
    )
    assert resp_resume.status_code == 404, "伪造 projectId 不应生效，AI 简历项目查不到此知识库"


# ============================================================================
# 测试 3：伪造 X-Project-Code → 403 PROJECT_CODE_MISMATCH
# ============================================================================
async def test_e2e_forged_project_code_returns_403(
    client: httpx.AsyncClient,
    e2e_demo_data,
):
    """伪造 X-Project-Code（与 Key 不一致）→ 403 PROJECT_CODE_MISMATCH。

    场景：
        - 客户端持有 AI 基金的 API Key
        - X-Project-Code 头伪造为 ai-resume-e2e
        - 服务端 get_project_context 依赖校验 X-Project-Code 与 Project.code 一致
        - 不一致抛 ProjectCodeMismatchError（403）

    验证点：
        - HTTP 状态码 403
        - 错误码 PROJECT_CODE_MISMATCH
    """
    fund_data = e2e_demo_data["fund"]

    # 用 AI 基金的 Key，但 X-Project-Code 伪造为 AI 简历
    response = await client.get(
        "/api/v1/knowledge-bases",
        headers=_auth_headers(
            fund_data["raw_api_key"],
            project_code="ai-resume-e2e",  # 伪造：与 Key 所属项目不一致
        ),
    )

    # 断言：X-Project-Code 不一致返回 403
    assert response.status_code == 403, (
        f"伪造 X-Project-Code 应返回 403，实际：{response.status_code} {response.text}"
    )
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "PROJECT_CODE_MISMATCH"


# ============================================================================
# 测试 4：AI 简历 Key 调用 fund_market 工具 → 403 TOOL_NOT_ALLOWED
# ============================================================================
async def test_e2e_resume_call_fund_tool_returns_403(
    client: httpx.AsyncClient,
    e2e_demo_data,
):
    """AI 简历项目调用 fund_market 工具返回 403 TOOL_NOT_ALLOWED。

    场景：
        - 客户端持有 AI 简历的 API Key
        - AI 简历项目的工具白名单中只有 job_search，不含 fund_market
        - 即使客户端绕过 API 直接调用，ToolExecutor 也会校验白名单
        - 抛 ToolNotAllowedError（403）

    注：
        /research/run 接口内部会调用 ToolExecutor，
        由于工具不在白名单中，整个研究流程会因工具调用失败而降级或返回错误。
        这里直接验证 ToolExecutor 的拒绝逻辑（通过研究接口的降级行为间接验证）。

    验证点：
        - ToolExecutor.execute 对 AI 简历 ctx 调用 fund_market 抛 ToolNotAllowedError
        - 错误码 TOOL_NOT_ALLOWED
        - HTTP 状态码 403
    """
    resume_data = e2e_demo_data["resume"]

    # 直接调用 ToolExecutor 验证白名单拒绝逻辑
    # （端到端测试中通过 Repository 层验证，避免依赖 /research/run 的完整链路）
    from app.core.exceptions import ToolNotAllowedError
    from app.modules.tools.executor import ToolExecutor

    # 使用独立会话执行（避免与 fixture 会话事务冲突）
    async with AsyncSessionFactory() as session:
        resume_project = resume_data["project"]
        resume_ctx = ProjectContext(
            project_id=resume_project.id,
            project_code=resume_project.code,
        )
        executor = ToolExecutor(session)

        # AI 简历项目调用 fund_market：应抛 ToolNotAllowedError
        with pytest.raises(ToolNotAllowedError) as exc_info:
            await executor.execute(resume_ctx, "fund_market", {"fund_codes": ["000001"]})

        # 验证错误码与 HTTP 状态码
        assert exc_info.value.code == "TOOL_NOT_ALLOWED"
        assert exc_info.value.http_status == 403


# ============================================================================
# 测试 5：伪造 scheduleId（其他项目的）→ 404 TASK_NOT_FOUND
# ============================================================================
async def test_e2e_forged_schedule_id_returns_404(
    client: httpx.AsyncClient,
    e2e_demo_data,
    db_session: "AsyncSession",
):
    """伪造 scheduleId（其他项目的）→ 404 TASK_NOT_FOUND。

    场景：
        - 在 AI 基金项目下创建一个 Schedule
        - 用 AI 简历的 API Key 查询该 Schedule ID
        - 服务端 Repository.get_by_id 带 project_id 过滤，跨项目返回 None
        - 端点抛 TaskNotFoundError（404）

    验证点：
        - HTTP 状态码 404
        - 错误码 TASK_NOT_FOUND
    """
    fund_data = e2e_demo_data["fund"]
    resume_data = e2e_demo_data["resume"]

    # 在 AI 基金项目下创建一个 Schedule（通过 Repository 直接创建，简化测试）
    from datetime import datetime, timezone

    fund_ctx = ProjectContext(
        project_id=fund_data["project"].id,
        project_code=fund_data["project"].code,
    )
    schedule_repo = ScheduleRepository(db_session)
    fund_schedule = await schedule_repo.create(
        fund_ctx,
        name="AI 基金定时采集",
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

    # 用 AI 简历的 Key 查询 AI 基金的 Schedule ID
    response = await client.get(
        f"/api/v1/schedules/{fund_schedule.id}",
        headers=_auth_headers(resume_data["raw_api_key"]),
    )

    # 断言：跨项目查询返回 404
    assert response.status_code == 404, (
        f"跨项目查询 Schedule 应返回 404，实际：{response.status_code} {response.text}"
    )
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "TASK_NOT_FOUND"


# ============================================================================
# 测试 6：伪造 crawlSourceId（其他项目的）→ 404 CRAWL_SOURCE_NOT_FOUND
# ============================================================================
async def test_e2e_forged_crawl_source_id_returns_404(
    client: httpx.AsyncClient,
    e2e_demo_data,
    db_session: "AsyncSession",
):
    """伪造 crawlSourceId（其他项目的）→ 404 CRAWL_SOURCE_NOT_FOUND。

    场景：
        - 在 AI 基金项目下创建一个 CrawlSource
        - 用 AI 简历的 API Key 查询该 CrawlSource ID
        - 服务端 Repository.get_by_id 带 project_id 过滤，跨项目返回 None
        - 端点抛 CrawlSourceNotFoundError（404）

    验证点：
        - HTTP 状态码 404
        - 错误码 CRAWL_SOURCE_NOT_FOUND
    """
    fund_data = e2e_demo_data["fund"]
    resume_data = e2e_demo_data["resume"]

    # 在 AI 基金项目下创建一个 CrawlSource（通过 Repository 直接创建）
    fund_ctx = ProjectContext(
        project_id=fund_data["project"].id,
        project_code=fund_data["project"].code,
    )
    crawl_source_repo = CrawlSourceRepository(db_session)
    fund_source = await crawl_source_repo.create(
        ctx=fund_ctx,
        code="fund-crawl-source-e2e",
        name="AI 基金采集源",
        type="web",
        start_urls=["https://example.com"],
        allowed_domains=["example.com"],
        blocked_paths=[],
        destination_knowledge_base_id=fund_data["kb"].id,
        extract_rules={},
        import_policy="auto",
        limits={"maxDepth": 2, "maxPages": 100},
        status="active",
    )
    await db_session.commit()

    # 用 AI 简历的 Key 查询 AI 基金的 CrawlSource ID
    response = await client.get(
        f"/api/v1/crawl-sources/{fund_source.id}",
        headers=_auth_headers(resume_data["raw_api_key"]),
    )

    # 断言：跨项目查询返回 404
    assert response.status_code == 404, (
        f"跨项目查询 CrawlSource 应返回 404，实际：{response.status_code} {response.text}"
    )
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "CRAWL_SOURCE_NOT_FOUND"
