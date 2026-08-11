"""业务工具定义：5 个内置工具配置常量 + seed 函数。

对应 SubTask 13.1：业务工具注册。

工具注册机制说明（务必阅读）
----------------------------
1. 平台级工具清单
   ``BUILTIN_TOOL_DEFINITIONS`` 中的每个 dict 描述一个工具的元信息（编码、入参/出参
   JSON Schema、超时、适用项目、失败码、降级策略）。这些工具定义属于"全局表"
   ``tool_definitions``，不含 project_id，由平台统一维护。

2. 为什么需要项目白名单
   全局工具定义只声明"平台支持哪些工具"，并不能直接被某个项目调用。
   要让一个项目（如 ai-fund）能调用 ``fund_market``，必须在项目级表
   ``project_tools`` 中插入一条记录（project_id + tool_code + config + enabled）。
   这样设计有两点好处：
     - 跨项目工具隔离：ai-resume 项目即使知道 ``fund_market`` 的 code，
       也无法调用（白名单不存在），从根本上杜绝越权。
     - 项目级配置：同一个工具在不同项目下可配置不同的 API 端点、参数默认值，
       便于灰度与多租户。

3. ``applicable_projects`` 双重校验
   工具定义自身的 ``applicable_projects`` 限制了"哪些项目能加入白名单"。
   例如 ``fund_market`` 仅对 ``ai-fund`` 适用，即便运营同学误把 ``fund_market``
   加入 ``ai-resume`` 项目的白名单，Executor 在执行时也会校验
   ``tool.applicable_projects`` 是否包含当前项目 code，进一步拒绝越权。

4. ``seed_tool_definitions(session)``
   应用启动时调用此函数，将内置工具定义同步到数据库：
   - 已存在的 tool code 跳过（幂等）
   - 不存在则创建
   便于部署后立即可用，避免人工初始化。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.db.repositories.tool import ToolDefinitionRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# ============================================================================
# 内置工具定义常量
# 每个 dict 描述一个工具的元信息，用于首次启动时 seed 到 tool_definitions 表。
# ============================================================================

# 1. fund_market：基金行情查询
# 适用项目：ai-fund
# 用途：查询基金净值、年初至今收益、波动率、最大回撤等行情指标
FUND_MARKET_DEFINITION: dict[str, Any] = {
    # 工具编码：全局唯一，研究流程编排使用
    "code": "fund_market",
    "name": "基金行情查询",
    "description": (
        "查询基金行情数据，支持净值(nav)、年初至今收益(return_ytd)、"
        "波动率(volatility)、最大回撤(max_drawdown)等指标。"
        "适用于 AI 基金项目的研究流程编排。"
    ),
    # 入参 JSON Schema：约束客户端调用时必须传入 fund_codes
    "input_schema": {
        "type": "object",
        "properties": {
            # 基金代码列表，如 ["000001", "000002"]
            "fund_codes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "基金代码列表",
            },
            # 查询指标，可空（默认返回全部指标）
            "metrics": {
                "type": "array",
                "items": {"type": "string"},
                "enum": ["nav", "return_ytd", "volatility", "max_drawdown"],
                "description": "查询指标，默认返回全部",
            },
        },
        "required": ["fund_codes"],
    },
    # 出参 JSON Schema：基金行情数据结构
    "output_schema": {
        "type": "object",
        "properties": {
            "fund_codes": {"type": "array", "items": {"type": "string"}},
            "data": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "fund_code": {"type": "string"},
                        "nav": {"type": "number"},
                        "return_ytd": {"type": "number"},
                        "volatility": {"type": "number"},
                        "max_drawdown": {"type": "number"},
                    },
                },
            },
            # 数据截至时间，便于客户端判断时效性
            "data_as_of": {"type": "string", "format": "date-time"},
        },
    },
    # 超时秒数：4s（PRD 链路限制）
    "timeout_seconds": 4,
    # 适用项目：仅 AI 基金项目可加入白名单
    "applicable_projects": ["ai-fund"],
    # 失败码定义：用于降级判断与错误归因
    "failure_codes": {
        "PROVIDER_UNAVAILABLE": "行情数据源不可用",
        "INVALID_FUND_CODE": "基金代码非法或不存在",
    },
    # 降级策略：工具失败时按此策略处理（返回空结果 + 友好提示）
    "degradation": "返回空结果，提示行情服务暂不可用",
}


# 2. index_market：指数行情查询
# 适用项目：ai-fund
# 用途：查询股票指数（如沪深 300、中证 500）行情
INDEX_MARKET_DEFINITION: dict[str, Any] = {
    "code": "index_market",
    "name": "指数行情查询",
    "description": (
        "查询股票指数行情，如沪深 300、中证 500、创业板指等。"
        "适用于 AI 基金项目的研究流程编排。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            # 指数代码列表，如 ["000300", "000905"]
            "index_codes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "指数代码列表",
            },
        },
        "required": ["index_codes"],
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "index_codes": {"type": "array", "items": {"type": "string"}},
            "data": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index_code": {"type": "string"},
                        "name": {"type": "string"},
                        "close": {"type": "number"},
                        "change_pct": {"type": "number"},
                    },
                },
            },
            "data_as_of": {"type": "string", "format": "date-time"},
        },
    },
    "timeout_seconds": 4,
    "applicable_projects": ["ai-fund"],
    "failure_codes": {
        "PROVIDER_UNAVAILABLE": "指数行情数据源不可用",
        "INVALID_INDEX_CODE": "指数代码非法或不存在",
    },
    "degradation": "返回空结果，提示指数行情服务暂不可用",
}


# 3. financial_news：财经新闻检索
# 适用项目：ai-fund
# 用途：按关键词检索财经新闻，返回标题、摘要、来源、发布时间
FINANCIAL_NEWS_DEFINITION: dict[str, Any] = {
    "code": "financial_news",
    "name": "财经新闻检索",
    "description": (
        "按关键词检索财经新闻，返回标题、摘要、来源、发布时间。"
        "适用于 AI 基金项目的研究流程编排。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            # 检索关键词，如 "新能源汽车"
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "检索关键词列表",
            },
            # 返回条数，默认 10
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "返回条数，默认 10",
            },
        },
        "required": ["keywords"],
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "total": {"type": "integer"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "source": {"type": "string"},
                        "published_at": {"type": "string", "format": "date-time"},
                        "url": {"type": "string"},
                    },
                },
            },
        },
    },
    "timeout_seconds": 4,
    "applicable_projects": ["ai-fund"],
    "failure_codes": {
        "PROVIDER_UNAVAILABLE": "新闻数据源不可用",
        "RATE_LIMITED": "新闻数据源限流",
    },
    "degradation": "返回空结果，提示新闻服务暂不可用",
}


# 4. job_search：职位检索
# 适用项目：ai-resume
# 用途：按关键词、城市、薪资等条件检索职位，供 AI 简历项目使用
JOB_SEARCH_DEFINITION: dict[str, Any] = {
    "code": "job_search",
    "name": "职位检索",
    "description": (
        "按关键词、城市、薪资等条件检索职位，返回职位名称、公司、薪资、地点、链接。"
        "适用于 AI 简历项目的岗位推荐流程。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            # 检索关键词，如 "Python 工程师"
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "检索关键词列表",
            },
            # 城市，可空
            "city": {
                "type": "string",
                "description": "城市，可空",
            },
            # 薪资下限，可空
            "salary_min": {
                "type": "integer",
                "minimum": 0,
                "description": "薪资下限（元/月），可空",
            },
            # 返回条数
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "返回条数，默认 10",
            },
        },
        "required": ["keywords"],
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "total": {"type": "integer"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "company": {"type": "string"},
                        "salary": {"type": "string"},
                        "city": {"type": "string"},
                        "url": {"type": "string"},
                    },
                },
            },
        },
    },
    "timeout_seconds": 4,
    "applicable_projects": ["ai-resume"],
    "failure_codes": {
        "PROVIDER_UNAVAILABLE": "职位数据源不可用",
        "RATE_LIMITED": "职位数据源限流",
    },
    "degradation": "返回空结果，提示职位服务暂不可用",
}


# 5. product_search：商品检索
# 适用项目：ai-ecommerce
# 用途：按关键词、类目、价格区间检索商品，供 AI 电商项目使用
PRODUCT_SEARCH_DEFINITION: dict[str, Any] = {
    "code": "product_search",
    "name": "商品检索",
    "description": (
        "按关键词、类目、价格区间检索商品，返回商品名称、价格、销量、链接。"
        "适用于 AI 电商项目的商品推荐流程。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            # 检索关键词
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "检索关键词列表",
            },
            # 商品类目，可空
            "category": {
                "type": "string",
                "description": "商品类目，可空",
            },
            # 价格下限/上限，可空
            "price_min": {
                "type": "number",
                "minimum": 0,
                "description": "价格下限，可空",
            },
            "price_max": {
                "type": "number",
                "minimum": 0,
                "description": "价格上限，可空",
            },
            # 返回条数
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "返回条数，默认 10",
            },
        },
        "required": ["keywords"],
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "total": {"type": "integer"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "price": {"type": "number"},
                        "sales": {"type": "integer"},
                        "url": {"type": "string"},
                    },
                },
            },
        },
    },
    "timeout_seconds": 4,
    "applicable_projects": ["ai-ecommerce"],
    "failure_codes": {
        "PROVIDER_UNAVAILABLE": "商品数据源不可用",
        "RATE_LIMITED": "商品数据源限流",
    },
    "degradation": "返回空结果，提示商品服务暂不可用",
}


# 全部内置工具定义列表：seed 函数遍历此列表
BUILTIN_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    FUND_MARKET_DEFINITION,
    INDEX_MARKET_DEFINITION,
    FINANCIAL_NEWS_DEFINITION,
    JOB_SEARCH_DEFINITION,
    PRODUCT_SEARCH_DEFINITION,
]


# ============================================================================
# seed 函数：将内置工具定义同步到数据库
# ============================================================================
async def seed_tool_definitions(session: "AsyncSession") -> dict[str, int]:
    """将内置工具定义同步到 ``tool_definitions`` 表（幂等）。

    应用启动时调用此函数，确保平台内置工具（fund_market 等）在数据库中存在。
    - 已存在的 tool code 跳过（不覆盖，便于运营自定义后不被覆盖）
    - 不存在则创建

    设计要点
    --------
    1. 幂等：多次调用不会重复创建，也不会覆盖已有定义
    2. 仅在启动时调用一次，由应用入口（main.py 或 alembic 迁移）触发
    3. 返回统计信息，便于日志记录与排查

    Args:
        session: 异步数据库会话。

    Returns:
        dict 包含：
            - ``created``: 本次新建的工具数量
            - ``skipped``: 已存在跳过的工具数量
            - ``total``: 内置工具总数
    """
    # 构造 Repository
    repo = ToolDefinitionRepository(session)
    created = 0  # 本次新建数
    skipped = 0  # 已存在跳过数

    for tool_def_dict in BUILTIN_TOOL_DEFINITIONS:
        # 按 code 查询是否已存在
        existing = await repo.get_by_code(tool_def_dict["code"])
        if existing is not None:
            # 已存在：跳过，不覆盖运营可能的自定义
            skipped += 1
            continue

        # 不存在：创建
        # 注意 failure_codes 为 dict，存入 JSONB 字段
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

    # 提交事务：将新建的工具定义持久化
    await session.commit()

    return {
        "created": created,
        "skipped": skipped,
        "total": len(BUILTIN_TOOL_DEFINITIONS),
    }
