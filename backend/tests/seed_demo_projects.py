"""演示项目种子脚本：创建 3 个业务项目及其关联资源。

对应 SubTask 25.1：端到端验收测试前置数据准备。

脚本功能
--------
1. 创建 3 个演示项目：
   - ``ai-fund``（AI 基金）
   - ``ai-resume``（AI 简历）
   - ``ai-ecommerce``（AI 电商）
2. 每个项目创建 1 个知识库：
   - fund-kb / resume-kb / ecommerce-kb
3. 每个项目生成 1 个 API Key（明文打印到控制台，便于复制使用）
4. 每个项目配置 ProjectSettings（模型、联网、工具白名单等）
5. 每个项目配置工具白名单：
   - ai-fund：fund_market / index_market / financial_news
   - ai-resume：job_search
   - ai-ecommerce：product_search
6. seed 全局工具定义（fund_market / job_search / product_search 等）

独立运行方式
------------
    cd backend
    python -m tests.seed_demo_projects

设计要点
--------
1. 幂等：相同 code 的项目/知识库/API Key 重复执行不会报错，已存在则跳过。
2. 仅创建演示数据，不涉及业务逻辑调用，确保最小副作用。
3. API Key 明文仅在创建时打印一次，落库的是 Argon2 哈希。
4. 中文注释完整，便于运维理解每个步骤。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select

from app.core.project_context import ProjectContext
from app.core.scopes import (
    SCOPE_CAPABILITIES_READ,
    SCOPE_CRAWL_READ,
    SCOPE_CRAWL_WRITE,
    SCOPE_FEEDBACK_WRITE,
    SCOPE_KNOWLEDGE_WRITE,
    SCOPE_RESEARCH_RUN,
    SCOPE_RETRIEVAL_READ,
    SCOPE_SCHEDULES_READ,
    SCOPE_SCHEDULES_WRITE,
    SCOPE_TASKS_READ,
)
from app.core.security import generate_api_key
from app.db.models.tool import ToolDefinition
from app.db.repositories.knowledge import KnowledgeBaseRepository
from app.db.repositories.project import (
    ApiKeyRepository,
    ProjectRepository,
    ProjectSettingsRepository,
)
from app.db.repositories.tool import ProjectToolRepository
from app.db.session import AsyncSessionFactory
from app.modules.tools.definitions import BUILTIN_TOOL_DEFINITIONS

# 配置日志：输出到控制台，便于观察脚本执行过程
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("seed_demo")


# ============================================================================
# 演示项目配置常量
# ============================================================================
# 每个项目的元信息：code / name / description / 知识库 code / 工具白名单
# 工具白名单与 ToolDefinition.applicable_projects 一一对应，避免执行时被拒
_DEMO_PROJECTS: list[dict[str, Any]] = [
    {
        "code": "ai-fund",
        "name": "AI 基金",
        "description": "AI 基金研究与决策中台演示项目",
        "kb_code": "fund-kb",
        "kb_name": "基金知识库",
        # AI 基金项目适用的工具白名单
        "tool_whitelist": ["fund_market", "index_market", "financial_news"],
        # 项目级设置：模型与联网配置
        "settings": {
            "chat_model": "gpt-4o-mini",
            "embedding_model": "text-embedding-3-small",
            "web_search_enabled": True,
            # 联网搜索允许域名白名单（财经数据常见来源）
            "allowed_domains": ["eastmoney.com", "sina.com.cn", "cs.com.cn"],
            "blocked_domains": [],
            "max_evidence": 8,
            "timeout_seconds": 15,
        },
    },
    {
        "code": "ai-resume",
        "name": "AI 简历",
        "description": "AI 简历优化与岗位推荐演示项目",
        "kb_code": "resume-kb",
        "kb_name": "简历知识库",
        # AI 简历项目适用的工具白名单
        "tool_whitelist": ["job_search"],
        "settings": {
            "chat_model": "gpt-4o-mini",
            "embedding_model": "text-embedding-3-small",
            "web_search_enabled": True,
            "allowed_domains": ["zhaopin.com", "51job.com", "liepin.com"],
            "blocked_domains": [],
            "max_evidence": 6,
            "timeout_seconds": 15,
        },
    },
    {
        "code": "ai-ecommerce",
        "name": "AI 电商",
        "description": "AI 电商商品推荐与决策演示项目",
        "kb_code": "ecommerce-kb",
        "kb_name": "电商知识库",
        # AI 电商项目适用的工具白名单
        "tool_whitelist": ["product_search"],
        "settings": {
            "chat_model": "gpt-4o-mini",
            "embedding_model": "text-embedding-3-small",
            "web_search_enabled": False,
            "allowed_domains": ["jd.com", "taobao.com", "tmall.com"],
            "blocked_domains": [],
            "max_evidence": 6,
            "timeout_seconds": 15,
        },
    },
]

# 演示 API Key 授予的全部 Scope（运行时 + 写入 + 调度 + 爬虫）
# 便于端到端测试覆盖所有接口，生产环境应按需收紧
_DEMO_SCOPES: list[str] = [
    SCOPE_CAPABILITIES_READ,
    SCOPE_RETRIEVAL_READ,
    SCOPE_RESEARCH_RUN,
    SCOPE_TASKS_READ,
    SCOPE_KNOWLEDGE_WRITE,
    SCOPE_FEEDBACK_WRITE,
    SCOPE_SCHEDULES_READ,
    SCOPE_SCHEDULES_WRITE,
    SCOPE_CRAWL_READ,
    SCOPE_CRAWL_WRITE,
]


async def _seed_tool_definitions(session) -> None:
    """同步内置工具定义到数据库（幂等）。

    遍历 ``BUILTIN_TOOL_DEFINITIONS``，已存在则跳过，不存在则创建。
    确保 fund_market / index_market / financial_news / job_search /
    product_search 等工具在 tool_definitions 表中存在，
    后续项目白名单配置才能引用这些 tool_code。

    Args:
        session: 异步数据库会话。
    """
    from app.db.repositories.tool import ToolDefinitionRepository

    repo = ToolDefinitionRepository(session)
    created = 0
    skipped = 0

    for tool_def_dict in BUILTIN_TOOL_DEFINITIONS:
        # 按 code 查询是否已存在
        existing = await repo.get_by_code(tool_def_dict["code"])
        if existing is not None:
            # 已存在：跳过，避免覆盖运营自定义
            skipped += 1
            continue

        # 不存在：创建新工具定义
        await repo.create(
            code=tool_def_dict["code"],
            name=tool_def_dict["name"],
            description=tool_def_dict["description"],
            input_schema=tool_def_dict["input_schema"],
            output_schema=tool_def_dict["output_schema"],
            timeout_seconds=tool_def_dict["timeout_seconds"],
            applicable_projects=tool_def_dict["applicable_projects"],
            failure_codes=tool_def_dict["failure_codes"],
            degradation=tool_def_dict["degradation"],
        )
        created += 1
        logger.info("创建工具定义：%s", tool_def_dict["code"])

    await session.commit()
    logger.info("工具定义 seed 完成：新建 %d 个，跳过 %d 个", created, skipped)


async def _seed_project(session, config: dict[str, Any]) -> dict[str, Any]:
    """创建单个演示项目及其关联资源。

    步骤：
        1. 创建项目（已存在则复用）
        2. 创建知识库（已存在则复用）
        3. 生成 API Key（每次都生成新 Key，便于多次运行测试）
        4. 配置 ProjectSettings（upsert 语义）
        5. 配置工具白名单（已存在则跳过）

    Args:
        session: 异步数据库会话。
        config: 项目配置字典，见 ``_DEMO_PROJECTS``。

    Returns:
        包含 project / kb / api_key 明文 的字典，供主流程汇总打印。
    """
    project_repo = ProjectRepository(session)
    kb_repo = KnowledgeBaseRepository(session)
    api_key_repo = ApiKeyRepository(session)
    settings_repo = ProjectSettingsRepository(session)
    project_tool_repo = ProjectToolRepository(session)

    # ------------------------------------------------------------------
    # 步骤 1：创建项目（已存在则复用，幂等）
    # ------------------------------------------------------------------
    project = await project_repo.get_by_code(config["code"])
    if project is None:
        # 不存在：创建新项目
        project = await project_repo.create(
            code=config["code"],
            name=config["name"],
            description=config["description"],
        )
        await session.flush()
        logger.info("创建项目：%s (%s)", config["code"], config["name"])
    else:
        logger.info("项目已存在，复用：%s", config["code"])

    # 构造项目上下文：所有 Repository 操作都以 ctx.project_id 为锚点
    ctx = ProjectContext(
        project_id=project.id,
        project_code=project.code,
    )

    # ------------------------------------------------------------------
    # 步骤 2：创建知识库（已存在则复用，幂等）
    # ------------------------------------------------------------------
    kb = await kb_repo.get_by_code(ctx, config["kb_code"])
    if kb is None:
        # 不存在：创建新知识库，使用默认 1536 维向量
        kb = await kb_repo.create(
            ctx=ctx,
            name=config["kb_name"],
            code=config["kb_code"],
            embedding_dimension=1536,
        )
        await session.flush()
        logger.info("创建知识库：%s (%s)", config["kb_code"], config["kb_name"])
    else:
        logger.info("知识库已存在，复用：%s", config["kb_code"])

    # ------------------------------------------------------------------
    # 步骤 3：生成 API Key（每次运行都生成新 Key，便于测试）
    # ------------------------------------------------------------------
    # generate_api_key 返回三元组：(明文 Key, 前缀, 哈希)
    # 明文 Key 仅此处打印一次，落库的是哈希
    raw_key, key_prefix, key_hash = generate_api_key()
    api_key = await api_key_repo.create(
        ctx=ctx,
        name=f"{config['name']} 演示 Key",
        environment="dev",
        key_prefix=key_prefix,
        key_hash=key_hash,
        scopes=_DEMO_SCOPES,
    )
    await session.flush()
    logger.info("生成 API Key：%s (prefix=%s)", config["code"], key_prefix)

    # ------------------------------------------------------------------
    # 步骤 4：配置 ProjectSettings（upsert 语义）
    # ------------------------------------------------------------------
    # upsert：存在则更新字段，不存在则插入
    settings_params = config["settings"]
    await settings_repo.upsert(ctx, **settings_params)
    await session.flush()
    logger.info("配置项目设置：%s", config["code"])

    # ------------------------------------------------------------------
    # 步骤 5：配置工具白名单（已存在则跳过）
    # ------------------------------------------------------------------
    for tool_code in config["tool_whitelist"]:
        # 查询当前项目是否已配置此工具
        existing = await project_tool_repo.get_by_code(ctx, tool_code)
        if existing is not None:
            # 已存在：跳过，避免重复创建
            continue
        # 不存在：创建白名单记录，默认 enabled=True
        await project_tool_repo.create(
            ctx=ctx,
            tool_code=tool_code,
            config={},
            enabled=True,
        )
        logger.info("配置工具白名单：%s -> %s", config["code"], tool_code)

    # 提交事务：保证项目及其关联资源持久化
    await session.commit()

    # 返回关键信息：项目 / 知识库 / 明文 API Key（供主流程汇总打印）
    return {
        "project": project,
        "kb": kb,
        "raw_key": raw_key,
    }


async def main() -> None:
    """脚本主入口：依次创建 3 个演示项目并打印汇总信息。

    执行流程：
        1. seed 全局工具定义（fund_market / job_search / product_search 等）
        2. 依次创建 ai-fund / ai-resume / ai-ecommerce 三个项目及其关联资源
        3. 汇总打印每个项目的明文 API Key，便于复制到 .env 或测试用例

    注意事项：
        - 脚本需在 backend 目录下运行（``python -m tests.seed_demo_projects``）
        - 数据库需已启动且迁移完成（PostgreSQL + pgvector）
        - 重复运行会生成新的 API Key，旧 Key 仍保留可用
    """
    logger.info("=" * 60)
    logger.info("开始 seed 演示项目数据")
    logger.info("=" * 60)

    # 收集每个项目的明文 API Key，最终汇总打印
    results: list[dict[str, Any]] = []

    # 使用应用自身的 AsyncSessionFactory，保证与生产环境一致
    async with AsyncSessionFactory() as session:
        try:
            # 步骤 1：seed 全局工具定义
            await _seed_tool_definitions(session)

            # 步骤 2：依次创建 3 个演示项目
            for config in _DEMO_PROJECTS:
                logger.info("-" * 40)
                result = await _seed_project(session, config)
                results.append(result)

            logger.info("=" * 60)
            logger.info("演示项目 seed 完成")
            logger.info("=" * 60)

            # 步骤 3：汇总打印每个项目的明文 API Key
            # 明文 Key 仅此处打印一次，调用方需立即保存
            print("\n")
            print("=" * 60)
            print("演示项目 API Key 汇总（请立即保存，仅显示一次）")
            print("=" * 60)
            for config, result in zip(_DEMO_PROJECTS, results):
                project = result["project"]
                kb = result["kb"]
                raw_key = result["raw_key"]
                print(f"\n[{config['name']}] project_code={project.code}")
                print(f"  project_id : {project.id}")
                print(f"  kb_code    : {kb.code}")
                print(f"  kb_id      : {kb.id}")
                print(f"  api_key    : {raw_key}")
                print(f"  tools      : {', '.join(config['tool_whitelist'])}")
            print("\n" + "=" * 60)
            print("可将上述 api_key 配置到 .env 或测试用例中")
            print("=" * 60)

        except Exception:
            # 异常时回滚未提交事务，避免脏数据
            await session.rollback()
            logger.exception("seed 演示项目失败")
            raise


if __name__ == "__main__":
    # 入口：通过 asyncio.run 启动异步主函数
    # 运行方式：cd backend && python -m tests.seed_demo_projects
    asyncio.run(main())
