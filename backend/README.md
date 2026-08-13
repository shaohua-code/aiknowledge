# 后端工程

本目录只包含 FastAPI、Celery Worker、数据库迁移、PostgreSQL 初始化和后端工具，不包含前端代码。

## CMD 启动 API

```bat
cd /d D:\project\aiknowledge\backend\services\api

REM 第一次运行时创建并安装虚拟环境
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .

REM 启动 API
.venv\Scripts\python.exe -m uvicorn knowledge_core.main:app --reload --port 8000
```

API 文档：`http://localhost:8000/docs`

本地直接运行 API 前，需要准备 PostgreSQL 和 Redis。若希望一次启动全部依赖，请在项目根目录使用 `docker compose up -d --build postgres redis api worker`。

## 常用命令

```bat
cd /d D:\project\aiknowledge\backend\services\api

REM 执行数据库迁移
.venv\Scripts\python.exe -m alembic upgrade head

REM 执行后端测试
.venv\Scripts\python.exe -m pytest tests
```

