# 智能知识中台 - 后端结构说明

> 本文档说明 `backend/` 目录下每个文件夹的作用、对应技术以及关键文件，便于快速理解后端整体架构。
> 运行命令请参考 `README` 类文档，本文档专注于"结构说明"。

---

## 一、技术栈总览

| 类别 | 技术 / 库 | 说明 |
| --- | --- | --- |
| 运行时 | Python 3.12 | 主语言，使用 `from __future__ import annotations` 延迟注解求值 |
| Web 框架 | FastAPI + Starlette + Uvicorn | 异步 API、依赖注入、中间件 |
| 数据校验 / 配置 | Pydantic v2 + pydantic-settings | `BaseSettings` 读取 `.env`，模型校验 |
| ORM | SQLAlchemy 2 (asyncio) | 异步会话、声明式映射 |
| 数据库驱动 | asyncpg | PostgreSQL 异步驱动 |
| 数据库迁移 | Alembic | 版本化 schema 管理 |
| 向量存储 | pgvector | PostgreSQL 向量类型与检索 |
| 缓存 / 限流 / 幂等 | Redis | 项目级限流、幂等键、Celery broker |
| 任务队列 | Celery + Celery Beat | 4 队列异步任务 + 定时调度 |
| HTTP 客户端 | httpx | 异步抓取、外部 API 调用 |
| 文档解析 | PyMuPDF + python-docx + Trafilatura + BeautifulSoup4 | PDF / DOCX / HTML 正文抽取与清洗 |
| 密钥派生 | Argon2id (argon2-cffi) | API Key 哈希校验 |
| Cron 解析 | croniter | 定时任务表达式解析 |
| 日志 | structlog + logging | 结构化日志、脱敏访问日志 |
| 重试 | tenacity | 外部调用容错 |
| 序列化 | orjson | 高性能 JSON |
| Lint / 类型 | Ruff + mypy | 代码风格与静态检查 |
| 测试 | pytest + pytest-asyncio + pytest-cov | 异步测试自动模式 |

---

## 二、目录结构

```
backend/
├── alembic/                  # 数据库迁移
├── app/                      # 应用主体
│   ├── api/                  # API 路由层
│   ├── core/                 # 核心基础设施（配置、安全、限流、响应等）
│   ├── db/                   # 数据访问层（模型 + 仓储）
│   ├── modules/              # 业务模块（领域服务）
│   ├── providers/            # 外部服务 Provider（模型、存储、搜索）
│   ├── workers/              # Celery 异步任务
│   └── main.py               # FastAPI 入口
├── tests/                    # 测试
├── .env.example              # 环境变量样例
├── .dockerignore
├── .gitignore
├── Dockerfile
├── alembic.ini               # Alembic 配置
└── pyproject.toml            # 项目元数据与依赖
```

---

## 三、文件夹详细说明

### `alembic/` —— 数据库迁移

- **作用**：管理 PostgreSQL schema 的版本化迁移。通过 `alembic.ini` 配置连接，`env.py` 注入异步引擎与 ProjectContext 过滤策略，`versions/` 存放有序迁移脚本（如 `0001_initial_schema.py`）。
- **对应技术**：Alembic + SQLAlchemy 2 async + asyncpg。
- **关键文件**：
  - `env.py`：迁移运行环境，配置 `target_metadata` 读取模型元数据。
  - `script.py.mako`：迁移脚本模板。
  - `versions/`：每次迁移生成一个 Python 文件，包含 `upgrade()` / `downgrade()`。

---

### `app/` —— 应用主体

整个后端业务代码根包，所有模块统一以 `app.` 前缀导入（`pyproject.toml` 中 `include = ["app*"]`）。

---

#### `app/api/` —— API 路由层

- **作用**：对外暴露 HTTP 接口，负责请求校验、依赖注入、调用领域服务并返回统一 `ApiResponse`。所有路由按版本分组，当前为 `v1`。
- **对应技术**：FastAPI（APIRouter、Path/Query/Body 参数、`Depends`）、Pydantic 模型。
- **关键文件**：
  - `dependencies.py`：FastAPI 依赖注入工厂，提供 DB 会话、ProjectContext、API Key 鉴权、Scope 校验等。
  - `v1/crawlers.py`：网页采集任务接口。
  - `v1/knowledge.py`：知识库 / 文档管理接口。
  - `v1/projects.py`：项目（多租户隔离单元）管理接口。
  - `v1/prompts.py`：提示词模板接口。
  - `v1/research.py`：短链路研究任务接口（同步 / 异步）。
  - `v1/retrieval.py`：混合检索接口（全文 + 向量 + RRF）。
  - `v1/schedules.py`：定时任务调度接口。
  - `v1/tools.py`：业务工具调用接口。
  - `v1/schemas.py`：API 入参 / 出参 Pydantic 模型集中定义。

---

#### `app/core/` —— 核心基础设施

- **作用**：与业务无关或横切关注点的通用组件，被各层复用。集中管理配置、安全、限流、幂等、响应封装、异常体系等。
- **对应技术**：Pydantic Settings、Redis、Argon2、contextvars、structlog。
- **关键文件**：
  - `config.py`：`Settings` 单例，从 `.env` 读取所有外部服务与限制参数，`lru_cache` 缓存。
  - `exceptions.py`：统一异常体系（`KnowledgeHubError` 基类 + 子类），携带错误码 / http_status / retryable。
  - `idempotency.py`：基于 Redis 的幂等键存储，防止重复提交产生副作用。
  - `project_context.py`：`ProjectContext` 不可变上下文（项目 ID + Scope），贯穿请求生命周期，强制项目级隔离。
  - `rate_limiter.py`：项目级限流（基于 Redis 计数 / 令牌桶）。
  - `redactor.py`：敏感数据脱敏（Authorization 头、query 截断、日志字段过滤）。
  - `response.py`：`ApiResponse` 统一封装 + 请求 ID（contextvars）管理。
  - `scopes.py`：Scope 权限定义（读 / 写 / 管理等枚举）。
  - `security.py`：API Key 的 Argon2id 哈希校验、双 Token（访问 Token + 管理 Token）校验逻辑。

---

#### `app/db/` —— 数据访问层

- **作用**：持久化相关代码。`models/` 定义 ORM 实体，`repositories/` 封装数据访问逻辑（CRUD + 项目过滤），`session.py` 提供异步会话工厂。
- **对应技术**：SQLAlchemy 2 (async) + asyncpg + pgvector。
- **关键文件**：
  - `session.py`：`async_sessionmaker` 与 `AsyncEngine`，依赖注入使用的会话生成器。
  - `models/base.py`：声明式 `Base`、公共 Mixin（时间戳、软删除、项目 ID 等）。
  - `models/project.py`、`knowledge.py`、`crawler.py`、`ingestion.py`、`prompt.py`、`research.py`、`schedule.py`、`tool.py`、`audit.py`：9 个文件，共 22 个模型。
  - `repositories/base.py`：仓储基类，封装通用查询与项目级过滤。
  - `repositories/*.py`：10 个仓储文件，共 22 个 Repository，与模型一一对应。
  - `migrations/`：预留的运行时迁移辅助（区别于 Alembic 的版本化迁移）。

---

#### `app/modules/` —— 业务模块（领域服务）

- **作用**：承载核心业务逻辑，被 `api` 层调用、由 `workers` 层异步执行。每个子模块对应一个业务域，内聚该域的服务、工具与策略。
- **对应技术**：httpx、BeautifulSoup4、Trafilatura、PyMuPDF、python-docx、自研算法。

##### `modules/crawler/` —— 网页采集
- **作用**：URL 规整、SSRF 防护、HTML 正文清洗与采集服务。
- **关键文件**：`ssrf_guard.py`（IP 解析 + 私网 / 元数据地址拦截）、`html_sanitizer.py`（标签 / 属性白名单清洗）、`url_utils.py`（URL 规整与校验）、`service.py`（采集编排）。

##### `modules/ingestion/` —— 文档处理
- **作用**：文档解析与文本分块（chunker），将上传文件转为可向量化的小段。
- **关键文件**：`chunker.py`（按语义 / 长度分块策略）。

##### `modules/knowledge/` —— 知识库
- **作用**：知识库与文档元数据的业务编排（具体逻辑在 `service` 内，当前包预留扩展）。

##### `modules/prompts/` —— 提示词服务
- **作用**：提示词模板渲染与变量替换。
- **关键文件**：`templates.py`（模板定义）、`service.py`（渲染服务）。

##### `modules/research/` —— 短链路研究
- **作用**：并行取证、证据合并与综合回答生成。区别于长链路 Agent，强调"少跳数、可追溯"。
- **关键文件**：`service.py`（研究编排）、`evidence_merger.py`（多源证据合并去重）。

##### `modules/retrieval/` —— 混合检索
- **作用**：融合全文检索、向量检索与 RRF（Reciprocal Rank Fusion）重排，返回统一排序结果。
- **关键文件**：`hybrid_searcher.py`（混合检索执行器）。

##### `modules/scheduler/` —— 定时任务
- **作用**：Cron 表达式解析与下次运行时间计算，调度幂等控制。
- **关键文件**：`cron_utils.py`（基于 croniter）。

##### `modules/tools/` —— 业务工具
- **作用**：面向 LLM 的业务工具集，统一注册与执行。
- **关键文件**：`definitions.py`（工具元数据 / Schema）、`executor.py`（执行分发）、`handlers/`（5 个具体工具：`financial_news` / `fund_market` / `index_market` / `job_search` / `product_search`）。

##### `modules/web_research/` —— 联网搜索
- **作用**：基于联网搜索 Provider 的查询、域名过滤与正文抽取。
- **关键文件**：`service.py`（搜索编排）、`domain_filter.py`（黑 / 白名单过滤）、`extractor.py`（结果正文抽取）。

##### `modules/audit/` —— 审计
- **作用**：关键操作审计日志写入（包预留扩展）。

---

#### `app/providers/` —— 外部服务 Provider

- **作用**：抽象外部依赖（LLM、Embedding、对象存储、联网搜索），通过工厂按配置切换实现，便于环境隔离与替换。
- **对应技术**：httpx、boto3 兼容接口、各 SaaS API。

##### `providers/chat_models/`
- **作用**：聊天模型 Provider，当前为 OpenAI 兼容实现。
- **关键文件**：`openai_provider.py`（OpenAI Chat Completions 封装）。

##### `providers/embeddings/`
- **作用**：Embedding 模型 Provider。
- **关键文件**：`openai_provider.py`（向量化接口封装）。

##### `providers/object_storage/`
- **作用**：对象存储抽象，支持本地磁盘与 S3 两后端。
- **关键文件**：`local_storage.py`（本地存储实现；S3 实现按需扩展）。

##### `providers/web_search/`
- **作用**：联网搜索 Provider，支持 serper 与 duckduckgo 双引擎。
- **关键文件**：`serper_provider.py`、`duckduckgo_provider.py`。

---

#### `app/workers/` —— Celery 异步任务

- **作用**：将耗时任务（采集、文档处理、研究、调度）异步化，通过 4 条独立队列实现物理资源隔离，避免长任务阻塞在线请求。
- **对应技术**：Celery + Celery Beat + Redis（broker / backend）+ kombu。
- **关键文件**：
  - `celery_app.py`：Celery 实例与队列定义（`online` / `ingestion` / `crawler` / `maintenance`），配置任务路由、超时、`acks_late`、`prefetch`，自动发现任务模块。
  - `crawl_tasks.py`：网页采集任务。
  - `ingestion_tasks.py`：文档解析 / 分块 / 向量化任务。
  - `research_tasks.py`：异步研究任务（用户不等待的长链路）。
  - `schedule_tasks.py`：Celery Beat 派发的调度任务（驱动 `scheduler` 模块）。

---

### `tests/` —— 测试

- **作用**：单元测试、集成测试与端到端隔离测试。覆盖项目隔离、检索隔离、工具隔离、SSRF 防护、调度幂等等关键路径。
- **对应技术**：pytest + pytest-asyncio + httpx（ASGI 测试客户端）。
- **关键文件**：
  - `conftest.py`：测试夹具（异步会话、测试客户端、数据清理）。
  - `seed_demo_projects.py`：演示数据种子。
  - `test_isolation.py` / `test_e2e_isolation.py`：项目级强隔离验证。
  - `test_retrieval_isolation.py` / `test_tool_isolation.py`：检索与工具隔离验证。
  - `test_ssrf_guard.py`：SSRF 防护验证。
  - `test_schedule_idempotency.py`：调度幂等验证。
  - `PERFORMANCE_TEST.md`：性能测试记录。

---

## 四、根级配置文件

| 文件 | 作用 |
| --- | --- |
| `pyproject.toml` | 项目元数据、运行时 / 开发依赖、Ruff / mypy / pytest 配置。Python 3.12、SQLAlchemy 2 async、Celery、pgvector、Argon2 等均在此声明。 |
| `alembic.ini` | Alembic 迁移配置（连接、脚本位置、日志）。 |
| `.env.example` | 环境变量样例，与 `core/config.py` 字段一一对应。 |
| `Dockerfile` | 容器构建镜像。 |
| `.dockerignore` / `.gitignore` | 构建 / 版本控制忽略规则。 |

---

## 五、核心设计模式

| 模式 | 说明 |
| --- | --- |
| **项目级强隔离** | 以 `ProjectContext`（不可变）贯穿请求 / 任务生命周期，所有仓储查询自动附加 `project_id` 过滤，确保多租户数据物理隔离，测试中以 `test_isolation*` 验证。 |
| **双 Token 鉴权** | 访问 Token（业务调用）+ 管理 Token（高权限操作），均使用 Argon2id 哈希存储与校验，`core/security.py` 统一封装，避免明文落库与恒定时间比较。 |
| **混合检索** | `modules/retrieval/hybrid_searcher.py` 并行执行全文检索与向量检索，使用 RRF 融合两路排序，兼顾关键词精确匹配与语义相似度，底层依赖 pgvector。 |
| **短链路研究** | `modules/research/` 限制工具调用跳数，并行取证后由 `evidence_merger` 合并去重，每条结论可追溯到原始证据，区别于无界 Agent。 |
| **SSRF 防护** | `modules/crawler/ssrf_guard.py` 在请求前解析目标 IP，拦截私网 / 回环 / 链路本地 / 元数据地址，配合 `html_sanitizer` 清洗返回内容，防止内网探测与脚本注入。 |
| **调度幂等** | `core/idempotency.py` + `modules/scheduler/`：定时任务派发前写入幂等键，重复触发（Beat 重试 / 手动补偿）自动跳过，避免重复采集与重复研究。 |
