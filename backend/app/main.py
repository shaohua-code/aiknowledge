"""FastAPI 应用入口。

对应 SubTask 2.4 / SubTask 6.2 / SubTask 6.3 / SubTask 23.3：
- 创建 FastAPI 实例（title="智能知识中台 API", version="1.0"）
- CORS 中间件（开发环境允许所有源）
- 请求 ID 中间件：从 X-Request-Id 头获取或生成，写入 contextvars
- 访问日志中间件（Task 23.3）：脱敏后记录请求方法/路径/耗时，不记录完整请求体
- 注册 /api/v1 前缀路由
- 健康检查 GET /health 与 GET /ready
- 统一异常处理：
  * KnowledgeHubError → ApiResponse.error(code, message, retryable, details) + http_status
  * RequestValidationError（FastAPI 422）→ VALIDATION_ERROR，details 含字段错误
  * Exception（兜底）→ INTERNAL_ERROR，retryable=True，生产环境不返回堆栈
"""
from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1 import crawlers, knowledge, projects, prompts, research, retrieval, schedules, stats, tools
from app.core.config import settings
from app.core.exceptions import KnowledgeHubError
from app.core.redactor import redact_authorization_header, truncate_query_for_log
from app.core.response import ApiResponse, set_request_id

# 应用日志器：异常兜底处理时记录堆栈，但不返回给客户端
logger = logging.getLogger(__name__)

# 敏感接口路径：这些接口的请求体可能含敏感业务问题，日志中不记录完整 body
# 仅记录 query 长度（由端点内部使用 truncate_query_for_log 脱敏写入 retrieval_logs）
_SENSITIVE_BODY_PATHS = frozenset({
    "/api/v1/retrieval/search",
    "/api/v1/research/run",
    "/api/v1/research/jobs",
})

# 创建 FastAPI 应用实例
app = FastAPI(
    title="智能知识中台 API",
    version="1.0",
    description="供 AI 基金、AI 简历、AI 电商等业务项目接入的智能研究与决策中台",
)

# CORS 中间件：开发环境允许所有源，生产环境应通过环境变量收紧
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# 请求 ID 中间件：从 X-Request-Id 头获取或生成，写入 contextvars
# ---------------------------------------------------------------------------
class RequestIdMiddleware(BaseHTTPMiddleware):
    """请求 ID 中间件：将 X-Request-Id 头的值写入 contextvars。

    作用：
        - 业务代码与 ApiResponse 通过 ``get_request_id()`` 自动获取当前请求 ID
        - 未携带 X-Request-Id 头时生成 ``req_<毫秒时间戳>`` 作为兜底
        - 同时将 X-Request-Id 写回响应头，便于客户端关联日志

    为什么用中间件而非依赖？
        中间件在所有路由与异常处理器之前执行，保证即使异常处理器中也能拿到
        requestId；依赖仅在路由函数中可用，异常处理器无法访问。
    """

    async def dispatch(self, request: Request, call_next):
        """中间件入口：设置 requestId 后继续请求链路。

        Args:
            request: 当前请求对象。
            call_next: 下一个中间件或路由处理函数。

        Returns:
            响应对象，响应头中携带 X-Request-Id。
        """
        # 优先使用客户端传入的 X-Request-Id，便于跨服务链路追踪
        request_id = request.headers.get("x-request-id")
        if not request_id:
            # 未传入：生成 req_ + 毫秒时间戳
            import time
            request_id = f"req_{time.time_ns() // 1_000_000}"

        # 写入 contextvars，后续 ApiResponse 自动读取
        set_request_id(request_id)

        # 继续请求链路
        response = await call_next(request)

        # 响应头回写 X-Request-Id，便于客户端关联日志
        response.headers["X-Request-Id"] = request_id
        return response


# 注册请求 ID 中间件
app.add_middleware(RequestIdMiddleware)


# ---------------------------------------------------------------------------
# 访问日志中间件（Task 23.3）：脱敏后记录请求方法/路径/耗时/状态码
# ---------------------------------------------------------------------------
class AccessLogMiddleware(BaseHTTPMiddleware):
    """访问日志中间件：记录每个请求的方法、路径、状态码、耗时。

    脱敏策略（Task 23.3）：
        - **不记录完整请求体**：POST /research/run 与 /retrieval/search 的 body
          可能含敏感业务问题（如客户数据、财务问题），日志中仅记录 method/path
        - **Authorization 头脱敏**：记录为 ``Bearer abcd****wxyz`` 格式
        - **X-API-Key 头脱敏**：记录为 ``abcd****wxyz`` 格式
        - 敏感接口路径的 query 长度由端点内部使用 ``truncate_query_for_log``
          写入 retrieval_logs，此处不重复记录

    为什么用中间件记录访问日志？
        中间件统一拦截所有请求，保证日志格式一致；端点内部日志关注业务细节，
        中间件日志关注流量与性能指标。
    """

    async def dispatch(self, request: Request, call_next):
        """中间件入口：记录请求开始与结束信息。

        Args:
            request: 当前请求对象。
            call_next: 下一个中间件或路由处理函数。

        Returns:
            响应对象。
        """
        # 记录请求开始时间，用于计算耗时
        start_time = time.time()
        # 脱敏后的 Authorization 头：仅记录前 4 后 4，避免泄露完整 API Key
        auth_header = request.headers.get("authorization")
        redacted_auth = redact_authorization_header(auth_header) if auth_header else None

        # 继续请求链路
        response = await call_next(request)

        # 计算耗时（毫秒）
        duration_ms = int((time.time() - start_time) * 1000)

        # 敏感接口路径不记录 query_string（避免泄露业务问题）
        # 仅记录是否存在 query_string，不记录具体内容
        path = request.url.path
        has_query = bool(request.url.query)

        # 记录访问日志：method / path / status / duration / 脱敏后的 auth
        # 敏感接口（/retrieval/search / /research/run / /research/jobs）不记录 body
        logger.info(
            "access %s %s -> %d %dms (auth=%s, has_query=%s)",
            request.method,
            path,
            response.status_code,
            duration_ms,
            redacted_auth,
            has_query,
        )

        return response


# 注册访问日志中间件
app.add_middleware(AccessLogMiddleware)


# ---------------------------------------------------------------------------
# 统一异常处理
# ---------------------------------------------------------------------------
@app.exception_handler(KnowledgeHubError)
async def knowledge_hub_error_handler(
    request: Request, exc: KnowledgeHubError
) -> JSONResponse:
    """将业务异常统一转换为标准 JSON 错误响应。

    所有继承 ``KnowledgeHubError`` 的业务异常都由此处理器捕获，
    转换为 ``ApiResponse.error(code, message, retryable, details)`` 格式，
    HTTP 状态码取自 ``exc.http_status``。

    Args:
        request: 当前请求对象（未使用，签名要求）。
        exc: 抛出的业务异常。

    Returns:
        JSONResponse: 包含 success=false、error 字段的标准响应。
    """
    # 使用 ApiResponse 构造标准响应体（requestId 自动从 contextvars 读取）
    content = ApiResponse.error(
        code=exc.code,
        message=exc.message,
        retryable=exc.retryable,
        details=exc.details,
    )
    return JSONResponse(status_code=exc.http_status, content=content)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """将 FastAPI 请求参数校验异常转换为标准 JSON 错误响应。

    FastAPI 在路径参数、查询参数、请求体校验失败时抛出 ``RequestValidationError``，
    默认返回 422 与裸错误结构。本处理器统一转换为 ``VALIDATION_ERROR`` 错误码，
    details 中包含字段级错误列表，便于客户端精准定位错误字段。

    Args:
        request: 当前请求对象（未使用）。
        exc: FastAPI 校验异常，``exc.errors()`` 返回错误列表。

    Returns:
        JSONResponse: 422 状态码，标准错误响应体。
    """
    # exc.errors() 返回 list[dict]，每条包含 loc / msg / type / input
    # 直接放入 details，便于客户端按字段定位错误
    content = ApiResponse.error(
        code="VALIDATION_ERROR",
        message="请求参数校验失败",
        retryable=False,
        details={"errors": exc.errors()},
    )
    return JSONResponse(status_code=422, content=content)


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """兜底异常处理器：所有未捕获异常统一返回 INTERNAL_ERROR。

    设计要点：
        1. 记录完整堆栈到日志，便于运维排查。
        2. 生产环境响应体不泄露堆栈，仅返回通用错误码与消息。
        3. retryable=True：未预期错误多为临时性（如数据库连接抖动），客户端可重试。

    Args:
        request: 当前请求对象（未使用）。
        exc: 未捕获的异常。

    Returns:
        JSONResponse: 500 状态码，标准错误响应体。
    """
    # 记录完整堆栈到日志，附带请求路径与方法便于排查
    logger.exception(
        "未捕获异常: %s %s",
        request.method,
        request.url.path,
        exc_info=exc,
    )

    # 生产环境不返回堆栈，仅返回通用错误码
    # 开发环境可通过 settings.app_env 判断是否返回更多调试信息
    details: dict = {}
    if settings.app_env == "development":
        # 开发环境返回异常类型与消息，便于调试
        details["exception_type"] = type(exc).__name__
        details["exception_message"] = str(exc)

    content = ApiResponse.error(
        code="INTERNAL_ERROR",
        message="服务器内部错误",
        retryable=True,
        details=details,
    )
    return JSONResponse(status_code=500, content=content)


# ---------------------------------------------------------------------------
# 健康检查接口
# ---------------------------------------------------------------------------
@app.get("/health", tags=["health"])
async def health() -> dict:
    """存活探针：进程存活即返回 ok，不检查依赖。"""
    return {"status": "ok"}


@app.get("/ready", tags=["health"])
async def ready() -> dict:
    """就绪探针：检查数据库可用性。

    当前为占位实现，后续 Task 4 完成数据库模型后，
    将真实执行 SELECT 1 校验连接。
    """
    # TODO Task 4: 接入数据库连接检查
    return {"status": "ok", "database": "ok"}


# ---------------------------------------------------------------------------
# 注册 /api/v1 前缀路由
# 各模块路由通过 APIRouter 聚合后统一挂载到 /api/v1 前缀
# 每个路由模块在自身 APIRouter 中已声明 tags，此处 include_router 时追加 tags
# 用于在 OpenAPI 文档中按业务域分组展示
# ---------------------------------------------------------------------------
from fastapi import APIRouter  # noqa: E402  局部导入避免顶部循环依赖风险

api_router = APIRouter(prefix="/api/v1")
# 项目管理：项目 CRUD + API Key 管理（管理密钥保护）
api_router.include_router(projects.router, tags=["项目管理"])
# 知识库管理：知识库 CRUD + 启停（项目 API Key + Scope 校验）
api_router.include_router(knowledge.router, tags=["知识库管理"])
# 文档处理：文档状态查询（SubTask 8.3，独立前缀 /documents）
api_router.include_router(knowledge.documents_router, tags=["文档处理"])
api_router.include_router(retrieval.router, tags=["检索"])
api_router.include_router(research.router, tags=["研究"])
api_router.include_router(schedules.router, tags=["调度"])
# 调度运行记录详情：独立前缀 /schedule-runs（不在 /schedules 下）
api_router.include_router(schedules.schedule_runs_router, tags=["调度"])
api_router.include_router(crawlers.router, tags=["爬虫"])
# 爬虫运行记录详情：独立前缀 /crawl-runs（不在 /crawl-sources 下）
api_router.include_router(crawlers.crawl_runs_router, tags=["爬虫"])
# 采集页面审核：独立前缀 /crawl-pages（approve / reject）
api_router.include_router(crawlers.crawl_pages_router, tags=["爬虫"])
# 网络待审核资料：独立前缀 /web-materials
api_router.include_router(crawlers.web_materials_router, tags=["爬虫"])
# 全局工具定义：列出平台全部工具（管理密钥保护）
api_router.include_router(tools.tools_router, tags=["工具管理"])
# 项目工具白名单：项目 API Key + Scope 校验
api_router.include_router(tools.project_tools_router, tags=["项目工具白名单"])
# 提示词版本管理：版本 CRUD + 激活切换（项目 API Key + Scope 校验）
api_router.include_router(prompts.router, tags=["提示词版本管理"])
# 控制台统计：平台总览使用管理密钥，项目总览继续通过 ProjectContext 隔离。
api_router.include_router(stats.router, tags=["运行统计"])

app.include_router(api_router)


@app.get("/", tags=["root"])
async def root() -> dict:
    """根路径：返回应用基本信息。"""
    return {
        "name": "智能知识中台 API",
        "version": "1.0",
        "env": settings.app_env,
        "docs": "/docs",
    }
