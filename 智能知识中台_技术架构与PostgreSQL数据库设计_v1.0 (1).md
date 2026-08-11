# 智能知识中台：技术架构与 PostgreSQL 数据库设计

版本：v1.0  
日期：2026-07-30  
配套文档：《智能知识中台 PRD MVP v3.0》

---

## 1. 技术目标

智能知识中台为 AI 基金、AI 简历、AI 电商及未来项目统一提供：

- 项目级强隔离的知识库。
- 文件、网页和业务数据导入。
- PostgreSQL 全文检索与 pgvector 向量检索。
- 联网搜索、业务工具和内部知识的并行取证。
- 单次大模型生成的短链路研究接口。
- 定时任务、网页采集、增量更新和异步向量化。
- 标准 API、API Key、执行记录、来源引用和成本追踪。

第一版不做登录和多租户。项目身份由 API Key 确定，调用方不能通过请求参数切换项目。

---

## 2. 最终技术栈

### 2.1 前端

| 技术 | 用途 |
|---|---|
| React 19 | 管理后台与研究台 |
| TypeScript | 类型约束 |
| Vite | 构建与本地开发 |
| Ant Design | 表格、表单、弹窗、上传等后台组件 |
| Tailwind CSS | 页面布局和局部样式 |
| Zustand | 项目上下文、界面状态 |
| TanStack Query | 服务端状态、缓存、重试 |
| React Router | 路由 |

Ant Design 负责复杂业务组件，Tailwind CSS 负责布局和间距。不要用 Tailwind 重写 Ant Design 的完整组件体系。

### 2.2 后端

| 技术 | 用途 |
|---|---|
| Python 3.12+ | 后端运行时 |
| FastAPI | REST API、依赖注入、OpenAPI |
| Pydantic | 请求、响应和配置校验 |
| SQLAlchemy 2 | ORM 与数据库访问 |
| Alembic | 数据库迁移 |
| PostgreSQL 16+ | 业务数据、全文检索、任务记录 |
| pgvector | Embedding 向量存储与相似度检索 |
| Redis | Celery Broker、分布式锁、短期缓存 |
| Celery | 文档、采集、向量化后台任务 |
| Celery Beat | 定时任务触发 |
| httpx | 异步 HTTP 请求 |
| Trafilatura / BeautifulSoup | 网页正文提取和清洗 |
| PyMuPDF / python-docx | PDF、Word 解析 |

### 2.3 测试与质量

- pytest：后端单元测试和集成测试。
- pytest-asyncio：异步测试。
- Ruff：Python 格式与静态检查。
- mypy：关键模块类型检查。
- Vitest：前端单元测试。
- Playwright：前端 E2E 测试；P1 可用于必要的动态网页采集。

---

## 3. 总体架构

```mermaid
flowchart TD
    A["AI 简历 / AI 基金 / AI 电商"] --> G["FastAPI 接入层"]
    M["React 管理后台"] --> G
    G --> C["ProjectContext 与权限范围"]
    C --> R["短链路研究服务"]
    R --> K["PostgreSQL + pgvector"]
    R --> W["联网搜索 / 网页提取"]
    R --> T["项目业务工具"]
    K --> E["证据合并与排序"]
    W --> E
    T --> E
    E --> L["一次大模型生成"]
    B["Celery Worker / Beat"] --> K
    B --> W
```

系统分为两条链路：

### 3.1 在线短链路

```text
API Key 识别项目
→ 内部检索、联网搜索、业务工具并行
→ 程序去重、评分、截断
→ 大模型只调用一次
→ 返回结论、证据、来源、置信度和耗时
```

限制：

- 单次任务最多调用大模型 1 次。
- 默认最多使用 8 条证据。
- 联网搜索默认 5 秒超时。
- 整体硬超时 15 秒。
- 网络失败时使用内部知识降级返回。
- 在线请求不执行网页批量采集和文档向量化。

### 3.2 后台任务链路

```text
Celery Beat / 手动触发 / 文件上传
→ Celery 队列
→ 抓取或解析
→ 清洗与去重
→ 文本切割
→ Embedding
→ 写入当前项目的 PostgreSQL / pgvector
```

后台任务失败不影响在线查询；数据库保存最终状态，Redis 不作为业务事实来源。

---

## 4. 项目级强隔离

### 4.1 身份确定

外部请求使用：

```http
Authorization: Bearer ikh_live_xxx
X-Request-Id: 业务方生成的唯一请求号
```

服务端根据 API Key 得到：

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class ProjectContext:
    project_id: str
    project_code: str
    environment: str
    api_key_id: str
    scopes: tuple[str, ...]
```

请求体中的 `projectId` 不参与鉴权，也不能覆盖 `ProjectContext`。

### 4.2 强制规则

1. 所有项目资源表必须包含 `project_id`。
2. Repository 的查询方法必须接收 `ProjectContext`。
3. 向量检索必须先按 `project_id` 和 `knowledge_base_id` 过滤。
4. API Key 只绑定一个项目和一个环境。
5. Redis Key 必须以 `project:{project_id}:` 开头。
6. 对象存储目录必须以 `projects/{project_id}/` 开头。
7. Celery 任务参数必须包含 `project_id`，Worker 读取数据时再次校验。
8. 日志、研究任务、证据、定时任务和采集结果全部记录 `project_id`。
9. 数据库外键使用 `(project_id, resource_id)` 复合约束防止跨项目关联。
10. MVP 不提供公共知识库和跨项目检索。

### 4.3 Repository 示例

```python
async def get_document(
    session: AsyncSession,
    context: ProjectContext,
    document_id: UUID,
) -> Document | None:
    statement = select(Document).where(
        Document.id == document_id,
        Document.project_id == context.project_id,
    )
    return await session.scalar(statement)
```

禁止只用 `document_id` 查询后再判断归属。

---

## 5. Python 后端模块设计

```text
backend/
├── app/
│   ├── main.py
│   ├── api/
│   │   ├── dependencies.py
│   │   └── v1/
│   │       ├── projects.py
│   │       ├── knowledge.py
│   │       ├── retrieval.py
│   │       ├── research.py
│   │       ├── schedules.py
│   │       └── crawlers.py
│   ├── core/
│   │   ├── config.py
│   │   ├── security.py
│   │   ├── project_context.py
│   │   └── exceptions.py
│   ├── db/
│   │   ├── session.py
│   │   ├── models/
│   │   ├── repositories/
│   │   └── migrations/
│   ├── modules/
│   │   ├── knowledge/
│   │   ├── ingestion/
│   │   ├── retrieval/
│   │   ├── web_research/
│   │   ├── crawler/
│   │   ├── scheduler/
│   │   ├── tools/
│   │   ├── prompts/
│   │   ├── research/
│   │   └── audit/
│   ├── providers/
│   │   ├── chat_models/
│   │   ├── embeddings/
│   │   ├── web_search/
│   │   └── object_storage/
│   └── workers/
│       ├── celery_app.py
│       ├── ingestion_tasks.py
│       ├── crawl_tasks.py
│       └── schedule_tasks.py
├── tests/
├── alembic.ini
└── pyproject.toml
```

关键原则：

- API 层只做校验、鉴权和响应映射。
- Service 负责业务流程。
- Repository 负责带 `project_id` 的数据库访问。
- Provider 封装模型、Embedding、搜索和对象存储，业务代码不绑定供应商。
- Worker 调用同一套 Service，不复制业务逻辑。

---

## 6. React 前端模块设计

```text
frontend/
├── src/
│   ├── api/
│   ├── components/
│   ├── layouts/
│   ├── pages/
│   │   ├── projects/
│   │   ├── knowledge-bases/
│   │   ├── documents/
│   │   ├── retrieval-test/
│   │   ├── research/
│   │   ├── schedules/
│   │   ├── crawl-sources/
│   │   └── execution-logs/
│   ├── stores/
│   ├── hooks/
│   ├── router/
│   ├── types/
│   └── utils/
├── vite.config.ts
└── package.json
```

前端所有项目内页面使用 `/projects/:projectId/...` 路由。切换项目时清空 TanStack Query 的项目相关缓存，避免界面显示上一个项目的数据。

---

## 7. PostgreSQL 设计原则

### 7.1 扩展

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;
```

### 7.2 通用字段

核心表统一包含：

- `id uuid primary key default gen_random_uuid()`
- `project_id uuid not null`
- `created_at timestamptz not null default now()`
- `updated_at timestamptz not null default now()`
- `status varchar(32)`

所有时间使用 `timestamptz`，接口统一返回 ISO 8601。定时任务额外保存 IANA 时区，例如 `Asia/Tokyo`、`Asia/Shanghai`。

### 7.3 JSONB 使用边界

适合 JSONB：

- 模型参数。
- 工具参数 Schema。
- 爬虫规则。
- 结构化研究结果。
- 不同文档类型的扩展元数据。

不适合只存 JSONB：

- 项目归属。
- 状态。
- 外键。
- 调度时间。
- 去重哈希。
- 需要频繁筛选和排序的字段。

---

## 8. 核心表关系

```mermaid
erDiagram
    PROJECTS ||--o{ API_KEYS : owns
    PROJECTS ||--o{ KNOWLEDGE_BASES : owns
    KNOWLEDGE_BASES ||--o{ DOCUMENTS : contains
    DOCUMENTS ||--o{ DOCUMENT_CHUNKS : splits
    PROJECTS ||--o{ RESEARCH_TASKS : runs
    RESEARCH_TASKS ||--o{ RESEARCH_EVIDENCE : uses
    PROJECTS ||--o{ SCHEDULES : configures
    SCHEDULES ||--o{ SCHEDULE_RUNS : produces
    PROJECTS ||--o{ CRAWL_SOURCES : owns
    CRAWL_SOURCES ||--o{ CRAWL_PAGES : discovers
```

---

## 9. 核心数据表

### 9.1 `projects`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | 主键 |
| code | citext | 唯一项目编码 |
| name | varchar(100) | 项目名称 |
| status | varchar(32) | active / disabled |
| settings | jsonb | 模型、超时、证据数等配置 |
| created_at | timestamptz | 创建时间 |
| updated_at | timestamptz | 更新时间 |

### 9.2 `api_keys`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | 主键 |
| project_id | uuid | 所属项目 |
| environment | varchar(16) | dev / test / prod |
| key_prefix | varchar(24) | 用于后台识别 |
| key_hash | varchar(128) | 只存哈希，不存明文 |
| scopes | text[] | retrieval:read 等权限 |
| last_used_at | timestamptz | 最近调用 |
| expires_at | timestamptz | 可空 |
| status | varchar(32) | active / revoked |

### 9.3 `knowledge_bases`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | 主键 |
| project_id | uuid | 所属项目 |
| name | varchar(120) | 名称 |
| code | citext | 项目内唯一编码 |
| description | text | 描述 |
| embedding_model | varchar(120) | 向量模型 |
| embedding_dimension | integer | 向量维度 |
| status | varchar(32) | active / disabled |

### 9.4 `documents`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | 主键 |
| project_id | uuid | 所属项目 |
| knowledge_base_id | uuid | 所属知识库 |
| source_type | varchar(32) | file / url / manual / crawler |
| title | text | 标题 |
| source_url | text | 原地址，可空 |
| storage_key | text | 原文件地址，可空 |
| mime_type | varchar(120) | 文件类型 |
| content_hash | char(64) | 正文 SHA-256 |
| processing_status | varchar(32) | pending / processing / completed / failed |
| enabled | boolean | 是否参与检索 |
| metadata | jsonb | 页数、作者、发布时间等 |

### 9.5 `document_chunks`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | 主键 |
| project_id | uuid | 所属项目 |
| knowledge_base_id | uuid | 所属知识库 |
| document_id | uuid | 所属文档 |
| chunk_index | integer | 片段序号 |
| content | text | 片段正文 |
| content_tsv | tsvector | PostgreSQL 全文索引 |
| embedding | vector(N) | N 由选定模型决定 |
| token_count | integer | Token 数 |
| page_number | integer | 页码，可空 |
| metadata | jsonb | 标题层级、原文位置等 |
| enabled | boolean | 是否参与检索 |

向量维度在首次选择 Embedding 模型时确定。MVP 不允许同一个知识库混用不同维度；更换维度必须新建知识库或完成全量重建。

### 9.6 其他业务表

| 表 | 用途 |
|---|---|
| prompts | 项目提示词及版本 |
| tool_definitions | 平台工具定义 |
| project_tools | 项目工具白名单与配置 |
| ingestion_jobs | 文档解析、切割、向量化任务 |
| research_tasks | 在线/异步研究任务 |
| research_evidence | 内部、网络、工具证据 |
| research_results | 结构化结论与模型输出 |
| retrieval_logs | 检索词、命中、耗时与评分 |
| schedules | 项目定时任务配置 |
| schedule_runs | 每次调度运行记录 |
| crawl_sources | RSS、Sitemap、列表页等采集源 |
| crawl_runs | 每次采集运行 |
| crawl_pages | 页面 URL、哈希、抓取与审核状态 |
| feedback | 外部项目对结果的接受/拒绝反馈 |

---

## 10. 关键约束

知识库代码在项目内唯一：

```sql
CREATE UNIQUE INDEX uq_knowledge_bases_project_code
ON knowledge_bases(project_id, code);
```

文档与知识库归属一致：

```sql
ALTER TABLE knowledge_bases
ADD CONSTRAINT uq_knowledge_bases_project_id_id
UNIQUE (project_id, id);

ALTER TABLE documents
ADD CONSTRAINT fk_documents_project_knowledge_base
FOREIGN KEY (project_id, knowledge_base_id)
REFERENCES knowledge_bases(project_id, id);
```

片段与文档归属一致：

```sql
ALTER TABLE documents
ADD CONSTRAINT uq_documents_project_id_id
UNIQUE (project_id, id);

ALTER TABLE document_chunks
ADD CONSTRAINT fk_chunks_project_document
FOREIGN KEY (project_id, document_id)
REFERENCES documents(project_id, id)
ON DELETE CASCADE;
```

这些复合约束可以阻止程序错误地把 AI 基金文档关联到 AI 简历知识库。

---

## 11. 检索索引

```sql
CREATE INDEX idx_chunks_project_kb
ON document_chunks(project_id, knowledge_base_id)
WHERE enabled = true;

CREATE INDEX idx_chunks_content_tsv
ON document_chunks USING gin(content_tsv);

CREATE INDEX idx_chunks_embedding_hnsw
ON document_chunks
USING hnsw (embedding vector_cosine_ops);

CREATE INDEX idx_documents_project_status
ON documents(project_id, processing_status, created_at DESC);

CREATE INDEX idx_research_tasks_project_created
ON research_tasks(project_id, created_at DESC);

CREATE INDEX idx_schedules_due
ON schedules(enabled, next_run_at)
WHERE enabled = true;

CREATE UNIQUE INDEX uq_crawl_page_url
ON crawl_pages(project_id, crawl_source_id, canonical_url_hash);
```

HNSW 索引适合读取多、持续增量写入的知识库。数据量很小时可以先不创建向量索引，避免过早优化。

---

## 12. 混合检索

### 12.1 检索步骤

1. 校验项目与知识库归属。
2. 对查询文本生成一次 Embedding。
3. PostgreSQL 全文检索取候选 Top 30。
4. pgvector 向量检索取候选 Top 30。
5. 使用 RRF 合并两个排名。
6. 按文档启用状态、来源和时间过滤。
7. 去除高度重复片段。
8. 返回 Top K，默认 5。

### 12.2 向量查询示例

```sql
SELECT
    id,
    document_id,
    content,
    1 - (embedding <=> :query_embedding) AS vector_score
FROM document_chunks
WHERE project_id = :project_id
  AND knowledge_base_id = ANY(:knowledge_base_ids)
  AND enabled = true
ORDER BY embedding <=> :query_embedding
LIMIT 30;
```

`project_id` 必须在向量查询阶段参与过滤，禁止全库召回后再按项目过滤。

### 12.3 全文查询示例

```sql
SELECT
    id,
    document_id,
    content,
    ts_rank_cd(content_tsv, websearch_to_tsquery('simple', :query)) AS text_score
FROM document_chunks
WHERE project_id = :project_id
  AND knowledge_base_id = ANY(:knowledge_base_ids)
  AND content_tsv @@ websearch_to_tsquery('simple', :query)
  AND enabled = true
ORDER BY text_score DESC
LIMIT 30;
```

中文全文检索效果不足时，MVP 可以先用 `simple` 配置加向量召回；P1 再引入专用中文分词或搜索引擎。

---

## 13. 定时任务与采集

### 13.1 任务类型

- `crawl_source`：采集指定来源。
- `sync_business_data`：同步基金、JD、商品等业务数据。
- `refresh_document`：刷新网页文档。
- `rebuild_embeddings`：重新向量化。
- `scheduled_research`：按固定主题生成研究结果。

### 13.2 Celery 队列

| 队列 | 优先级 | 用途 |
|---|---:|---|
| online | 高 | 异步研究任务 |
| ingestion | 中 | 文件解析和向量化 |
| crawler | 低 | 网页采集 |
| maintenance | 低 | 重建向量、清理过期数据 |

### 13.3 幂等

- 调度幂等键：`schedule_id + planned_run_at`。
- 文档去重键：`project_id + knowledge_base_id + content_hash`。
- 网页去重键：`project_id + crawl_source_id + canonical_url_hash`。
- API 写入幂等键：`project_id + Idempotency-Key`。

### 13.4 调度方式

Celery Beat 每分钟触发一次 `dispatch_due_schedules`。该任务从 PostgreSQL 使用 `FOR UPDATE SKIP LOCKED` 领取到期任务，写入 `schedule_runs` 后再投递具体 Worker，避免多实例重复执行。

---

## 14. 对外 API

### 14.1 两条主接口

```http
POST /api/v1/retrieval/search
```

只返回当前项目知识片段，不调用聊天模型。

```http
POST /api/v1/research/run
```

并行使用内部知识、联网搜索和项目工具，最多调用一次大模型。

### 14.2 辅助接口

```text
POST /api/v1/research/tasks
GET  /api/v1/research/tasks/{taskId}
POST /api/v1/knowledge/files
POST /api/v1/knowledge/texts
POST /api/v1/feedback
GET  /api/v1/capabilities
POST /api/v1/schedules
POST /api/v1/schedules/{scheduleId}/run
POST /api/v1/crawl-sources
POST /api/v1/crawl-sources/{sourceId}/run
```

### 14.3 API 性能目标

| 接口 | P95 目标 |
|---|---:|
| 纯内部检索 | ≤ 800ms |
| 内部知识问答 | ≤ 5s |
| 联网/工具研究 | ≤ 12s |
| 整体硬超时 | 15s |

---

## 15. 安全设计

- API Key 仅展示一次，数据库只存 Argon2id/SHA-256 派生后的哈希。
- 日志不得记录完整 API Key、模型密钥或用户敏感文档正文。
- 爬虫只允许 HTTP/HTTPS。
- DNS 解析后拒绝环回、内网、链路本地和云元数据地址。
- 每次重定向后重新校验目标地址。
- 限制响应体大小、抓取时长、重定向次数和页面数量。
- 不执行网页脚本，不执行用户上传代码。
- 遵守 robots、站点条款、版权和访问频率限制。
- 基金场景必须展示数据时间、风险提示和不确定性，不承诺收益。
- 简历场景不得虚构用户经历。
- 电商场景不得生成与真实产品参数冲突的内容。

---

## 16. 部署拓扑

MVP 可以部署在一台服务器：

```text
Nginx
├── React 静态文件
└── /api → FastAPI

FastAPI × 2
Celery Worker × 2
Celery Beat × 1
PostgreSQL + pgvector
Redis
对象存储或本地文件目录
```

生产注意事项：

- Celery Beat 只能有一个有效调度实例。
- PostgreSQL 每日备份，定期验证恢复。
- 原始文件与数据库备份分开保存。
- FastAPI、Worker 和采集任务使用独立日志。
- 为在线 API 与后台采集设置不同的 CPU、并发和超时。

---

## 17. 环境变量

```dotenv
APP_ENV=development
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/knowledge_hub
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/2
OBJECT_STORAGE_PROVIDER=local
OBJECT_STORAGE_PATH=./data/uploads
CHAT_PROVIDER=
CHAT_MODEL=
CHAT_API_KEY=
EMBEDDING_PROVIDER=
EMBEDDING_MODEL=
EMBEDDING_API_KEY=
WEB_SEARCH_PROVIDER=
WEB_SEARCH_API_KEY=
```

真实密钥只放服务器环境变量或密钥管理服务，不能提交到 Git。

---

## 18. 第一阶段实施顺序

1. 初始化 React、FastAPI、PostgreSQL、pgvector、Redis。
2. 建立项目、API Key、知识库和文档表。
3. 完成 `ProjectContext` 与跨项目隔离测试。
4. 完成文件解析、切割、Embedding 和后台任务。
5. 完成 PostgreSQL 全文检索与 pgvector 向量检索。
6. 完成 `/retrieval/search`。
7. 完成联网搜索、工具白名单和证据合并。
8. 完成单次模型生成的 `/research/run`。
9. 完成定时任务、网页采集和增量入库。
10. 接入 AI 简历作为第一个真实业务。

---

## 19. 技术验收清单

- AI 基金 Key 无法读取 AI 简历的知识库、文档、片段和日志。
- 修改请求体中的 `projectId` 不会改变实际项目。
- 所有向量查询都包含 `project_id` 前置过滤。
- 文档解析、采集和向量化不阻塞在线 API。
- 联网失败时能够基于内部知识降级返回。
- 单次 `/research/run` 最多调用一次大模型。
- 相同调度时间不会重复执行同一个定时任务。
- 相同 URL 或正文不会在同一项目知识库重复入库。
- 所有结论可追溯到内部文档、网络 URL 或业务工具。
- PostgreSQL 备份可恢复，Celery 任务状态可从数据库核对。

---

## 20. 结论

智能知识中台第一版采用：

> React + TypeScript + Ant Design + Tailwind CSS + FastAPI + PostgreSQL + pgvector + Redis + Celery

PostgreSQL 不只是普通业务数据库，还统一承担项目数据、文档元数据、全文检索、向量检索、任务状态和审计记录。通过 `project_id` 前置过滤、复合外键、API Key 项目绑定和后台任务二次校验，保证 AI 基金、AI 简历、AI 电商之间的数据与能力隔离。

在线研究使用“并行取证 + 一次模型生成”的短链路，定时任务、爬虫和向量化全部放入后台队列，从架构上避免采集任务拖慢业务请求。
