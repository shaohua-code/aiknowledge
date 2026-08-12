from __future__ import annotations

import asyncio
import logging
import time

from celery.exceptions import CeleryError
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from knowledge_core import __version__
from knowledge_core.config import settings
from knowledge_core.control.auth import router as session_router
from knowledge_core.domains.applications.router import router as applications_router
from knowledge_core.domains.intelligence.control_router import router as intelligence_control_router
from knowledge_core.domains.knowledge.router import router as knowledge_router
from knowledge_core.domains.operations.router import router as operations_router
from knowledge_core.infrastructure.database import engine
from knowledge_core.runtime.router import router as runtime_router
from knowledge_core.shared.errors import CoreError
from knowledge_core.shared.request_id import new_request_id, set_request_id
from knowledge_core.shared.response import failure
from knowledge_core.workers.celery_app import celery_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("knowledge_core")

app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description="面向 AI 应用的知识、检索、回答与运行治理底座",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-Id", "Idempotency-Key"],
)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        candidate = request.headers.get("x-request-id", "").strip()
        request_id = candidate[:80] if candidate else new_request_id()
        set_request_id(request_id)
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        logger.info(
            "request method=%s path=%s status=%s duration_ms=%s request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            round((time.perf_counter() - started) * 1000),
            request_id,
        )
        return response


app.add_middleware(RequestContextMiddleware)


@app.exception_handler(CoreError)
async def core_error_handler(_request: Request, exc: CoreError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content=failure(exc))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    error = CoreError(
        "请求参数校验失败",
        details={"fields": jsonable_encoder(exc.errors())},
        suggestion="请根据字段错误修正请求后重试",
    )
    error.code = "VALIDATION_ERROR"
    error.title = "请求参数不正确"
    error.http_status = 422
    return JSONResponse(status_code=422, content=failure(error))


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_error method=%s path=%s", request.method, request.url.path)
    error = CoreError(
        "服务器内部错误",
        suggestion="请复制请求 ID，并在运行中心查看诊断详情",
    )
    error.code = "INTERNAL_ERROR"
    error.title = "服务暂时无法完成请求"
    error.http_status = 500
    error.retryable = True
    return JSONResponse(status_code=500, content=failure(error))


@app.get("/health", tags=["平台健康"])
async def health() -> dict:
    return {"status": "ok", "service": "api", "version": __version__}


async def _check_worker() -> bool:
    def ping() -> bool:
        try:
            replies = celery_app.control.inspect(timeout=1).ping() or {}
            return bool(replies)
        except (CeleryError, OSError):
            return False

    return await asyncio.to_thread(ping)


@app.get("/ready", tags=["平台健康"])
async def ready() -> JSONResponse:
    checks: dict[str, dict[str, str]] = {}
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception:
        checks["database"] = {"status": "error"}

    redis = Redis.from_url(settings.redis_url)
    try:
        await redis.ping()
        checks["redis"] = {"status": "ok"}
    except Exception:
        checks["redis"] = {"status": "error"}
    finally:
        await redis.aclose()

    checks["worker"] = {"status": "ok" if await _check_worker() else "degraded"}
    checks["chatProvider"] = {
        "status": "ok" if settings.chat_provider != "disabled" else "unconfigured"
    }
    checks["embeddingProvider"] = {
        "status": "development" if settings.embedding_provider == "local_hash" else "ok"
    }
    checks["searchProvider"] = {
        "status": "unconfigured"
        if settings.web_search_provider == "disabled"
        else ("ok" if settings.web_search_api_key else "error")
    }
    core_ready = checks["database"]["status"] == "ok" and checks["redis"]["status"] == "ok"
    overall = "ok" if core_ready and checks["worker"]["status"] == "ok" else "degraded"
    return JSONResponse(
        status_code=200 if core_ready else 503,
        content={"status": overall, "ready": core_ready, "checks": checks},
    )


app.include_router(session_router)
app.include_router(applications_router)
app.include_router(knowledge_router)
app.include_router(intelligence_control_router)
app.include_router(operations_router)
app.include_router(runtime_router)


@app.get("/", tags=["平台信息"])
async def root() -> dict:
    return {
        "name": settings.app_name,
        "version": __version__,
        "controlApi": "/control/v1",
        "runtimeApi": "/runtime/v1",
        "docs": "/docs",
    }
