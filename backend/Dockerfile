# =============================================================================
# 智能知识中台 - 后端服务镜像
# 基础镜像：python:3.12-slim（精简版 Debian，体积小，含 Python 3.12）
# =============================================================================
FROM python:3.12-slim

# -----------------------------------------------------------------------------
# 维护者信息（OCI 标准标签）
# -----------------------------------------------------------------------------
LABEL maintainer="Knowledge Hub Team"
LABEL description="智能知识中台后端 FastAPI + Celery 镜像"
LABEL version="1.0.0"

# -----------------------------------------------------------------------------
# 系统依赖安装
# -----------------------------------------------------------------------------
# 为什么需要这些系统包？
#   - libpq-dev：PostgreSQL 客户端开发库，asyncpg 编译时需要 pg_config
#   - gcc：C 编译器，asyncpg / argon2-cffi 等含 C 扩展的包需要编译
#   - curl：用于容器健康检查（HEALTHCHECK 指令调用 curl 探活）
#   - 默认已含 ca-certificates，保证 HTTPS 请求证书校验正常
# 清理 apt 缓存以减小镜像体积（--no-install-recommends 避免安装推荐包）
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq-dev \
        gcc \
        curl \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# 工作目录：容器内应用代码存放路径
# -----------------------------------------------------------------------------
WORKDIR /app

# -----------------------------------------------------------------------------
# 环境变量：Python 运行时优化
# -----------------------------------------------------------------------------
# PYTHONDONTWRITEBYTECODE=1：不生成 .pyc 文件，减少镜像层数与体积
# PYTHONUNBUFFERED=1：不缓冲 stdout/stderr，日志实时输出到 docker logs
#   （默认缓冲会导致 uvicorn / celery 日志延迟，调试困难）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# -----------------------------------------------------------------------------
# 依赖安装：先复制 pyproject.toml，利用 Docker 层缓存加速构建
# -----------------------------------------------------------------------------
# 为什么先复制 pyproject.toml 而非整个目录？
#   Docker 每一层基于上一层的差异构建。若先 COPY . 再 pip install，
#   任何代码改动都会使 pip install 层失效，导致每次构建都重装依赖。
#   先复制 pyproject.toml 单独安装依赖，代码改动时该层缓存命中，构建秒级完成。
COPY pyproject.toml ./

# 安装运行时依赖（不安装 dev 依赖，减小镜像体积）
# pip install . 会读取 pyproject.toml 中的 [project].dependencies
RUN pip install --upgrade pip \
    && pip install .

# -----------------------------------------------------------------------------
# 复制应用代码
# -----------------------------------------------------------------------------
# 此时依赖已安装，仅复制 app 源码与 alembic 迁移脚本
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

# 暴露 FastAPI 服务端口（仅声明用途，实际映射由 docker-compose 决定）
EXPOSE 8000

# -----------------------------------------------------------------------------
# 健康检查：每 30 秒调用 /health 接口探活
# -----------------------------------------------------------------------------
# --fail：HTTP 状态码 >= 400 时 curl 返回非 0，触发 unhealthy
# --max-time 5：5 秒超时，避免网络抖动误判
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --max-time 5 http://localhost:8000/health || exit 1

# -----------------------------------------------------------------------------
# 默认启动命令：uvicorn 启动 FastAPI
# -----------------------------------------------------------------------------
# Worker 容器通过 docker-compose 的 command 字段覆盖为 celery worker
# 参数说明：
#   app.main:app —— 模块路径 app/main.py 中的 app 实例
#   --host 0.0.0.0 —— 监听所有网卡（容器内必须 0.0.0.0 才能被外部访问）
#   --port 8000 —— 监听端口
#   --workers 4 —— 启动 4 个工作进程（生产推荐 CPU 核数 * 2 + 1）
#   --no-access-log —— 关闭 uvicorn 自带访问日志（由应用中间件记录，避免重复）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--no-access-log"]
