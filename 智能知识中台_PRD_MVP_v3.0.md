# 智能知识中台 PRD

版本：MVP v3.0  
日期：2026-07-30  
产品原则：项目强隔离、标准 API 接入、后台定时采集、短链路、一次生成、证据可追溯、失败可降级

---

## 1. 产品概述

### 1.1 产品名称

产品名称：智能知识中台（Intelligent Knowledge Hub）

### 1.2 产品定位

本产品不是单纯的文件存储或知识库问答工具，而是一套供多个 AI 项目接入的智能研究与决策中台。

系统以“项目空间”为最高隔离边界。AI 基金、AI 简历、AI 电商分别拥有自己的知识库、文件、网络来源、业务工具、提示词、模型配置、API Key 和执行记录。一个项目发起的任务，只能使用当前项目允许的数据和工具，不能检索或引用其他项目的内容。

每个项目可以组合三类能力：

- 内部知识：文档、网页归档、手动知识、历史研究结果。
- 外部信息：互联网搜索、指定网站内容和最新新闻。
- 业务工具：基金行情、招聘 JD、商品信息等结构化数据接口。
- 后台任务：按项目定时采集网页、同步业务数据、更新知识和运行固定研究任务。

系统对三类证据进行过滤、去重和排序，最后只调用一次大模型，输出有来源、有时间、有风险说明的结构化结论。

### 1.3 项目示例

| 项目空间 | 独立知识库示例 | 独立工具示例 | 典型输出 |
|---|---|---|---|
| AI 基金 | 基金基础、投资规则、政策资料、历史研报 | 行情、指数、基金持仓、财经新闻 | 持仓分析、风险提示、调整建议 |
| AI 简历 | 岗位知识、简历规则、JD、面试题 | JD 搜索、简历解析 | 岗位匹配、简历优化、学习建议 |
| AI 电商 | 产品资料、平台规则、竞品和评论 | 商品查询、关键词和竞品数据 | Listing、A+ 文案、合规检查 |

### 1.4 核心目标

MVP 需要完成以下目标：

1. 建立真正的项目级数据隔离，杜绝跨项目知识召回。
2. 支持每个项目独立创建多个知识库。
3. 支持内部知识、网络信息和业务工具并行获取证据。
4. 使用固定短链路完成分析，避免多轮 Agent 带来的延迟和成本。
5. 通过统一 API 为现有及未来业务项目提供能力。
6. 每次结果都能追溯来源、数据时间、执行步骤和项目归属。
7. 让 AI 简历、AI 基金、AI 电商通过一次业务请求获得结果，不需要了解文档切割、向量检索和模型调用细节。
8. 支持每个项目独立配置定时任务和公开网页采集，并在后台异步更新自己的知识库。

### 1.5 MVP 不做

- 不做用户注册、登录、组织和角色权限。
- 不做跨项目共享知识库。
- 不做多租户 SaaS。
- 不做多 Agent 协作。
- 不做 Agent 自主循环、反思后再次搜索。
- 不做复杂 LangGraph 编排。
- 不做自动交易、自动投递简历或自动发布商品。
- 不自动把网络搜索结果永久写入正式知识库。
- 不把一个项目的历史对话作为另一个项目的记忆。
- 不允许运行用户提交的任意 JavaScript、Java、Python 或 Shell 代码。
- 不绕过登录、验证码、付费墙、robots 规则或网站访问限制。

---

## 2. 核心概念与层级

### 2.1 资源层级

```text
知识中台
└── 项目空间 Project
    ├── 知识库 Knowledge Base
    │   └── 文档 Document
    │       └── 文本片段 Chunk + Embedding
    ├── 数据源 Source
    ├── 爬虫源 Crawl Source
    ├── 定时任务 Schedule
    ├── 工具 Tool
    ├── 提示词 Prompt
    ├── API Key
    └── 研究任务、证据、结果和日志
```

### 2.2 项目空间

项目空间是最高业务边界，例如：

- `ai-fund`
- `ai-resume`
- `ai-ecommerce`

项目创建后生成唯一的 `projectId` 和 `projectCode`。`projectCode` 用于人工识别，`projectId` 用于数据库关联。创建后的 `projectCode` 默认不可修改。

每个项目独立配置：

- 知识库和文档。
- Embedding 模型。
- Chat 模型。
- 系统提示词和输出格式。
- 是否允许联网。
- 可信网站和禁用网站。
- 可调用的业务工具白名单。
- 可采集域名、采集规则和目标知识库。
- 定时任务、执行时区、失败重试和通知规则。
- 最大证据数、超时和 Token 上限。
- 独立 API Key。
- 执行记录、统计和费用。

### 2.3 知识库

知识库必须属于一个且只能属于一个项目。

例如 AI 基金项目下可创建：

- 基金基础知识库。
- 投资纪律与风险规则库。
- 政策资料库。
- 历史研报库。
- 用户持仓说明库。

AI 简历项目下可创建：

- 前端岗位知识库。
- Java 岗位知识库。
- 简历优化规则库。
- 招聘 JD 库。
- 面试题库。

### 2.4 共享知识处理原则

MVP 不允许一个知识库同时绑定多个项目。如果某份资料确实需要被多个项目使用，先复制到各自项目中，保证边界清晰。

后期如增加公共知识库，也必须通过显式授权关系实现，不能默认共享。

---

## 3. 项目隔离设计

### 3.1 隔离范围

| 层级 | 隔离要求 |
|---|---|
| 数据库 | 所有业务表必须包含 `project_id` |
| 知识检索 | 每次关键词和向量检索必须强制过滤 `project_id` |
| 文件存储 | 文件路径以 `projects/{projectId}/` 开头 |
| 缓存 | Redis/本地缓存键必须包含 `projectId` |
| API | 业务请求必须携带项目 API Key，由服务端解析项目 |
| 提示词 | 只加载当前项目启用的提示词 |
| 工具 | 只允许调用当前项目白名单中的工具 |
| 联网来源 | 只使用当前项目配置的可信域名和搜索策略 |
| 爬虫任务 | 采集源、页面、快照和运行记录必须绑定 `projectId` |
| 定时任务 | Cron、参数、队列任务和运行日志必须绑定 `projectId` |
| 对话与记忆 | 会话、消息和长期记忆必须绑定当前项目 |
| 日志和统计 | 调用记录、Token 和费用按项目分别统计 |

### 3.2 隔离规则

1. 客户端不能通过传入任意 `projectId` 获得项目权限。
2. 后端先根据 API Key 得到真实 `projectId`，再执行查询。
3. 请求体中的 `projectCode` 只用于校验和日志，不能作为唯一权限依据。
4. Repository/Service 层所有查询方法必须接收 `projectId`。
5. 禁止出现不带 `projectId` 条件的文档、片段、提示词、任务查询。
6. 文档所属项目和目标知识库所属项目不一致时，拒绝写入。
7. 删除、更新、重新向量化时，也必须同时校验资源 ID 和 `projectId`。
8. 模型上下文中不传入其他项目的名称、摘要、提示词或历史数据。
9. 调度器触发任务时必须恢复任务所属 `ProjectContext`，不能使用全局默认项目。
10. 爬虫发现的 URL、正文、快照和目标知识库必须属于同一项目。

### 3.3 数据库约束示例

为避免只依赖业务代码隔离，数据库需增加联合约束：

```text
knowledge_bases: UNIQUE(project_id, id)
documents:       FOREIGN KEY(project_id, knowledge_base_id)
                 → knowledge_bases(project_id, id)
document_chunks: FOREIGN KEY(project_id, document_id)
                 → documents(project_id, id)
```

向量检索必须采用类似条件：

```sql
WHERE project_id = :currentProjectId
  AND knowledge_base_id = ANY(:allowedKnowledgeBaseIds)
  AND status = 'ACTIVE'
```

不得先全库向量召回，再在应用层过滤项目。

### 3.4 无登录状态下的访问方式

MVP 虽然不做登录，但仍需两类密钥：

- 管理密钥：仅本地或私有后台使用，配置在服务端环境变量中。
- 项目 API Key：每个项目独立生成，供 AI 基金、AI 简历等业务调用。

API Key 数据库只保存哈希，不保存可直接使用的明文。明文只在创建时展示一次。

---

## 4. 短链路智能分析

### 4.1 设计原则

“思考能力”不等于让 Agent 无限循环。MVP 使用项目预先配置好的规则进行路由，不调用大模型规划工具；内部检索、网络搜索和业务工具并行执行；证据整理后只调用一次大模型完成判断和自检。

### 4.2 标准执行链路

```text
业务请求
→ 校验 API Key 并确定项目
→ 读取当前项目配置
→ 内部检索 / 网络搜索 / 业务工具并行
→ 程序去重、过滤、评分、截断
→ 大模型一次生成结构化结论
→ 保存结果、证据、耗时和项目归属
```

### 4.3 链路限制

| 控制项 | MVP 限制 |
|---|---:|
| 单次请求大模型调用 | 最多 1 次 |
| 搜索轮次 | 1 轮 |
| Agent 循环 | 0 次 |
| 并行业务工具 | 最多 3 个 |
| 内部候选片段 | 最多 10 条 |
| 网络候选结果 | 最多 5 条 |
| 结构化工具结果 | 每类最多 5 条 |
| 最终送入模型的总证据 | 最多 8 条 |
| 单条证据最大长度 | 1,500 字符 |
| 联网超时 | 5 秒 |
| 业务工具超时 | 4 秒 |
| 整体请求超时 | 15 秒 |
| 性能目标 | 内部模式 P95 ≤ 5 秒；联网模式 P95 ≤ 12 秒 |

### 4.4 项目路由配置

每个项目预先配置可用能力，不让模型临时决定。

AI 基金默认路由：

```json
{
  "internalRetrieval": true,
  "webSearch": true,
  "allowedTools": ["fund_market", "index_market", "financial_news"],
  "maxParallelTools": 3,
  "singleModelCall": true
}
```

AI 简历默认路由：

```json
{
  "internalRetrieval": true,
  "webSearch": true,
  "allowedTools": ["job_search"],
  "maxParallelTools": 2,
  "singleModelCall": true
}
```

### 4.5 降级策略

- 网络搜索超时：用内部知识和业务工具生成，标记网络数据不可用。
- 业务工具超时：用内部知识和网络证据生成，不伪造实时数据。
- 内部知识未命中：继续使用当前项目的外部证据，并提示内部知识不足。
- 所有来源不足：返回“证据不足”，列出需要补充的数据，不强行生成建议。
- 大模型超时：返回程序已整理的证据列表和失败状态。
- 结构化输出解析失败：允许在同一次响应处理中修复 JSON 格式，但不能再次调用模型。

---

## 5. 用户角色与典型场景

### 5.1 MVP 用户

第一版由开发者本人使用知识中台后台，各业务项目通过 API 调用。

### 5.2 场景 A：AI 基金持仓分析

输入：

- 当前基金持仓。
- 计划持有周期。
- 风险承受能力。
- 是否使用网络和最新行情。

系统仅在 `ai-fund` 项目内执行：

1. 检索基金知识库中的投资纪律、风险规则和相关研报。
2. 并行查询最新行情、指数信息和财经新闻。
3. 根据时效、来源和相关性筛选证据。
4. 一次模型调用生成风险分析和不同情景下的调整建议。
5. 标注数据时间、事实、推断、风险和证据来源。

约束：

- 不自动买卖。
- 不承诺收益。
- 缺少实时数据时不能使用“当前”“今日”等误导性表述。
- 投资建议必须包含风险说明和适用条件。

### 5.3 场景 B：AI 简历岗位匹配

系统仅在 `ai-resume` 项目内读取用户真实经历、目标 JD 和岗位知识，输出：

- 已具备能力。
- 可迁移能力。
- 简历未体现能力。
- 确实缺失能力。
- 简历修改和学习建议。
- 内部知识及网络来源。

约束：

- 不得读取 AI 基金或 AI 电商的任何知识。
- 不得根据优秀案例虚构用户经历。

### 5.4 场景 C：AI 电商内容生成

系统仅在 `ai-ecommerce` 项目内读取真实商品参数、品牌规则、平台规则、竞品和评论，生成 Listing 或 A+ 文案。

约束：

- 不得虚构尺寸、材质、认证和功能。
- 需要区分真实产品信息、网络竞品信息和模型推断。

---

## 6. 功能需求

### 6.1 项目管理

#### 功能

- 创建、编辑、启停项目。
- 设置项目名称、`projectCode`、描述和图标。
- 配置 Chat 模型和 Embedding 模型。
- 配置联网开关、可信域名和禁用域名。
- 配置工具白名单、超时、证据上限和 Token 上限。
- 生成、停用和重新生成项目 API Key。
- 查看项目知识量、调用量、平均耗时、失败率和 Token 消耗。

#### 验收标准

- 停用项目后，该项目 API Key 无法继续调用。
- 不同项目可配置不同的模型、提示词和工具。
- 创建项目后自动生成默认隔离配置，不自动继承其他项目数据。

### 6.2 知识库管理

#### 功能

- 在指定项目下创建多个知识库。
- 编辑名称、描述、标签、检索权重和状态。
- 查看文档数、片段数、向量化状态和最后更新时间。
- 停用知识库但保留数据。
- 删除空知识库；非空知识库需先处理内部文档。

#### 验收标准

- 知识库必须显示所属项目。
- 在 AI 基金页面不能选择 AI 简历知识库。
- 后端对跨项目知识库 ID 请求返回 `403 PROJECT_SCOPE_MISMATCH`。

### 6.3 知识导入

#### 支持类型

- PDF。
- Word。
- TXT。
- Markdown。
- 网页 URL。
- 手动文本。

#### 后台处理流程

```text
待处理 → 解析 → 清洗 → 切割 → 向量化 → 可检索
```

文档处理属于异步任务，不占用在线分析链路。

#### 默认切割规则

- 目标长度：500～800 中文字符。
- 重叠长度：80～120 字符。
- 优先按标题、段落和列表边界切割。
- 每个片段保存项目、知识库、文档、标题、页码、来源、发布时间和更新时间。

#### 验收标准

- 上传时必须先选择项目和知识库。
- 文件只写入所属项目目录。
- 可查看处理状态、原文、片段和失败原因。
- 可重新处理、停用和删除文档。
- 重新处理不得改变文档的项目归属。

### 6.4 内部知识检索

MVP 使用混合检索：

- PostgreSQL 全文/关键词检索。
- pgvector 向量相似度检索。
- 项目、知识库、标签、时间和状态过滤。
- 程序加权合并。

默认评分：

```text
综合分 = 向量相似度 × 0.60
       + 关键词匹配度 × 0.25
       + 内容时效分 × 0.10
       + 来源质量分 × 0.05
```

MVP 暂不增加独立重排序模型，避免额外延迟。

#### 验收标准

- 每次检索必须有服务端解析出的 `projectId`。
- 返回内容、文档、页码/位置、匹配分和更新时间。
- 使用其他项目的 `knowledgeBaseId` 时不得返回任何片段。
- 检索过程不调用聊天模型。

### 6.5 联网搜索

#### 功能

- 每个任务只构造一个搜索查询。
- 只执行一轮搜索。
- 最多获取 5 条结果。
- 根据当前项目的可信域名和禁用域名过滤。
- 提取标题、摘要、正文、发布时间和抓取时间。
- 默认只作为本次临时证据，不进入正式知识库。

#### 网络资料池

用户可手动将有价值的网络证据保存到当前项目的“待审核资料池”：

```text
待审核 → 已采用 / 已拒绝 → 已过期
```

只有人工确认“已采用”后，才能进入指定知识库并向量化。

### 6.6 业务工具管理

工具由中台统一注册，再按项目配置白名单。

| 工具 | 适用项目 | 说明 |
|---|---|---|
| `fund_market` | AI 基金 | 基金净值、涨跌和更新时间 |
| `index_market` | AI 基金 | 指数和行业行情 |
| `financial_news` | AI 基金 | 财经新闻 |
| `job_search` | AI 简历 | 招聘 JD 和岗位趋势 |
| `product_search` | AI 电商 | 竞品和商品信息 |

每个工具必须定义：

- 输入参数 Schema。
- 输出数据 Schema。
- 超时时间。
- 可使用项目。
- 数据更新时间字段。
- 失败码和降级方式。

#### 验收标准

- AI 简历项目不能调用基金行情工具。
- 未加入项目白名单的工具请求直接拒绝。
- 工具原始结果和耗时写入当前项目任务日志。

### 6.7 提示词管理

每个项目独立维护：

- 系统提示词。
- 证据使用规则。
- 输出 JSON Schema。
- 禁止事项。
- 风险提示模板。

MVP 每个项目只允许启用一个主版本。修改提示词生成新版本，历史任务保留使用版本号。

### 6.8 智能研究台

#### 输入

- 当前项目。
- 任务问题。
- 当前项目下的知识库，可多选。
- 是否联网。
- 是否调用当前项目允许的业务工具。
- 可选结构化业务参数。

#### 输出

- 最终结论。
- 关键判断。
- 建议动作。
- 内部知识证据。
- 网络证据。
- 工具数据。
- 数据时间。
- 不确定项。
- 风险提示。
- 置信度。
- 总耗时和各阶段耗时。

### 6.9 执行记录

每次请求保存：

- 项目。
- 调用来源。
- 输入摘要。
- 使用的知识库。
- 使用的提示词版本。
- 调用的工具。
- 内部、网络和工具证据。
- 模型和 Token。
- 各阶段耗时。
- 结果和错误。
- 创建时间。

后台只能查看当前选择项目的记录，切换项目后重新加载。

### 6.10 项目定时任务

每个项目可独立创建定时任务，用于在用户没有发起请求时更新数据或执行固定流程。

#### 支持的任务类型

| 任务类型 | 说明 | 示例 |
|---|---|---|
| `CRAWL_SOURCE` | 运行指定爬虫源 | 每小时采集财经新闻 |
| `TOOL_SYNC` | 调用项目允许的结构化工具并保存结果 | 每小时同步基金行情摘要 |
| `RESEARCH_RUN` | 按固定问题运行一次短链路研究 | 每天下午生成市场风险摘要 |
| `REINDEX_KNOWLEDGE` | 对指定知识库重新切割或向量化 | Embedding 模型升级后重建索引 |
| `EXPIRE_KNOWLEDGE` | 检查并标记过期资料 | 每天检查旧政策和旧 JD |

MVP 不提供“粘贴一段代码然后定时执行”的功能。定时任务只能选择平台预先注册的任务类型，并通过 JSON Schema 配置参数，避免任意代码执行和不可控资源消耗。

#### 定时配置

- 任务名称。
- 任务类型。
- Cron 表达式。
- IANA 时区，例如 `Asia/Shanghai`、`Asia/Tokyo`。
- 任务参数。
- 是否启用。
- 单次超时。
- 最大重试次数。
- 失败后是否暂停。
- 并发策略。

并发策略：

| 策略 | 行为 |
|---|---|
| `SKIP` | 上一次未结束时跳过本次，默认策略 |
| `QUEUE` | 本次进入队列，等待上一次完成 |
| `REPLACE` | 取消旧任务并运行新任务，MVP 暂不启用 |

#### 运行规则

1. 所有计划以配置时区解释，不依赖服务器系统时区。
2. 调度器每分钟扫描到期任务，并写入后台队列。
3. 使用 `scheduleId + plannedAt` 作为幂等键，避免多实例重复执行。
4. 每次运行创建独立 `schedule_run` 记录。
5. 任务开始前恢复其项目上下文，并重新校验项目、知识库、工具和爬虫源状态。
6. 项目停用后，所属定时任务全部停止触发。
7. 单个任务失败不影响其他项目任务。
8. 在线 `/research/run` 请求队列优先级高于后台采集任务。

#### 项目示例

| 项目 | 定时任务 | 推荐频率 |
|---|---|---|
| AI 基金 | 财经新闻采集 | 每小时一次 |
| AI 基金 | 尾盘风险摘要 | 交易日下午固定时间 |
| AI 简历 | 招聘 JD 更新 | 每天一次 |
| AI 简历 | 过期岗位清理 | 每周一次 |
| AI 电商 | 平台规则更新 | 每天一次 |
| AI 电商 | 竞品页面更新 | 每 6～12 小时一次 |

#### 验收标准

- AI 基金的定时任务只能选择 AI 基金的数据源、工具和知识库。
- 同一计划时间在服务重启或多实例部署时最多创建一个有效运行任务。
- 可手动执行、暂停、恢复和查看最近运行记录。
- 运行失败时保存明确阶段、错误码、重试次数和耗时。
- 后台任务不能占满在线请求的数据库连接、队列 Worker 或模型并发。

### 6.11 网页与数据采集

爬虫用于提前采集项目需要的公开数据，避免每次用户提问时都临时抓网页。

#### MVP 支持的采集源

| 类型 | 说明 |
|---|---|
| `SINGLE_PAGE` | 采集一个固定公开页面 |
| `URL_LIST` | 采集配置的一组 URL |
| `RSS` | 从 RSS/Atom 发现新文章 |
| `SITEMAP` | 从 sitemap.xml 发现页面 |
| `LIST_PAGE` | 从列表页按链接规则发现详情页 |

MVP 优先支持服务端可直接访问的 HTML、RSS 和 Sitemap。必须使用浏览器渲染的复杂动态页面放到 P1，以免第一版引入过高的内存和维护成本。

#### 爬虫源配置

- 名称和说明。
- 起始 URL。
- 允许域名列表。
- 禁止路径。
- 页面类型。
- 链接发现规则。
- 标题、正文、发布时间和作者提取规则。
- 请求间隔和单域名并发数。
- 单次最大页面数。
- 最大抓取深度。
- 超时、重试和 User-Agent。
- 目标知识库。
- 默认进入待审核池或可信源自动入库。

提取规则支持：

- 默认正文提取算法。
- CSS Selector。
- JSON-LD 和 Open Graph 元数据。
- RSS 字段映射。

MVP 不支持用户编写任意 JavaScript 提取脚本。

#### 采集处理链路

```text
定时任务或手动触发
→ URL 安全校验
→ 下载公开页面
→ 提取标题、正文、作者和发布时间
→ 规范化 URL
→ 按 URL 与正文哈希去重
→ 质量和时效检查
→ 待审核资料池或可信源自动入库
→ 切割与向量化
```

采集过程完全异步，不加入在线智能分析链路。

#### 增量更新与去重

- 规范化 URL 后生成 `canonical_url_hash`。
- 正文清洗后生成 `content_hash`。
- URL 和内容都未变化时只更新检查时间，不重复向量化。
- 内容发生变化时创建新版本，保留上一个版本的来源和时间。
- 支持 `ETag`、`Last-Modified` 和条件请求时优先使用，减少流量。
- RSS 和 Sitemap 保存游标或最近发布时间，仅处理新增内容。
- 已删除页面默认标记 `SOURCE_UNAVAILABLE`，不立即物理删除历史知识。

#### 入库策略

| 策略 | 适用情况 | 行为 |
|---|---|---|
| `REVIEW_REQUIRED` | 默认、未知来源 | 进入当前项目待审核资料池 |
| `AUTO_IMPORT` | 已配置的可信公开来源 | 自动进入指定知识库并向量化 |
| `EVIDENCE_ONLY` | 高频变化、无需长期保存 | 只保存短期证据和抓取记录 |

AI 基金的行情数字不建议通过普通网页抓取后永久当作稳定知识，优先使用结构化行情工具；爬虫主要采集新闻、政策、公告和研报摘要。

#### 安全与合规

- 仅采集公开可访问内容。
- 遵守目标网站服务条款、robots 规则、版权和合理请求频率。
- 不绕过登录、验证码、付费墙或反爬限制。
- 拒绝 `localhost`、内网 IP、云元数据地址、`file://` 和非 HTTP(S) 协议。
- DNS 解析后再次检查 IP，防止 DNS Rebinding 和 SSRF。
- 限制重定向次数、响应大小、下载时间和 MIME 类型。
- HTML 清洗时移除脚本、iframe、事件属性和危险标签。
- 页面内容被视为不可信数据，不能让网页中的文字改变系统提示词或调用未授权工具。

#### 验收标准

- 每次采集必须绑定项目和目标知识库。
- AI 基金爬虫不能将内容写入 AI 简历知识库。
- 同一页面未变化时不会重复生成文档和向量。
- 可查看发现数、成功数、重复数、失败数、入库数和总耗时。
- 单个网站失败不会终止同一项目的其他采集源。
- 暂停爬虫源后，相关定时任务不再创建新运行。

---

## 7. 后台信息架构

### 7.1 全局页面

| 页面 | 主要功能 | 优先级 |
|---|---|---|
| 项目列表 | 创建、启停和进入项目 | P0 |
| 全局概览 | 项目数量、总调用量和异常概览 | P1 |

### 7.2 项目内部页面

进入项目后，顶部必须持续显示项目名称和颜色标识，所有菜单数据限定在当前项目。

| 页面 | 主要功能 | 优先级 |
|---|---|---|
| 项目概览 | 知识量、今日调用、耗时、错误率 | P1 |
| 知识库 | 创建、编辑、启停知识库 | P0 |
| 文档 | 导入、处理状态、片段和来源 | P0 |
| 智能研究台 | 测试内部知识、联网和工具分析 | P0 |
| 网络资料池 | 审核、采用或拒绝网络资料 | P1 |
| 采集源 | 配置 RSS、Sitemap、列表页、提取和入库策略 | P0 |
| 采集记录 | 页面状态、去重、失败原因和入库结果 | P0 |
| 定时任务 | Cron、时区、参数、启停和手动运行 | P0 |
| 定时运行记录 | 计划时间、实际时间、状态、耗时和重试 | P0 |
| 工具配置 | 工具白名单、参数和超时 | P0 |
| 提示词 | 项目提示词和版本 | P0 |
| API Key | 创建、停用和轮换密钥 | P0 |
| 执行记录 | 证据、结果、耗时、Token 和错误 | P0 |
| 项目设置 | 模型、联网、证据和性能配置 | P0 |

### 7.3 防误操作设计

- 所有详情页显示“当前项目”面包屑。
- 切换项目时清空上一个项目的筛选条件和缓存列表。
- 上传文件前二次展示目标项目和知识库。
- 删除、重新处理和批量操作时展示项目名称。
- 不提供跨项目批量选择控件。
- 创建定时任务时二次展示所属项目、时区和目标知识库。
- 开启爬虫自动入库前明确展示可信来源和写入范围。

---

## 8. 外部项目接入 API

### 8.1 接入目标

知识中台必须作为独立服务部署，为 AI 简历、AI 基金、AI 电商及未来项目提供标准 HTTP API。

业务项目不需要自行实现：

- 文件解析与文本切割。
- Embedding 和向量存储。
- 关键词、向量和元数据混合检索。
- 联网搜索和网页内容整理。
- 业务工具并行调用。
- 证据去重、排序和截断。
- 提示词拼装和模型调用。
- 来源引用、置信度和执行日志。

业务项目的最短调用链路为：

```text
业务后端
→ 调用一次 POST /api/v1/research/run
→ 知识中台内部并行检索
→ 返回结构化结论
→ 业务前端展示
```

禁止前端浏览器直接保存和调用项目 API Key。AI 简历、AI 基金和 AI 电商均由各自后端调用知识中台。

### 8.2 API 分类

| 分类 | 用途 | 主要调用方 |
|---|---|---|
| 运行时 API | 检索知识、智能分析、查询任务 | AI 简历、AI 基金、AI 电商后端 |
| 知识写入 API | 上传文件、写入文本、导入网页 | 业务后端、管理后台、数据采集任务 |
| 配置读取 API | 查询当前项目可用能力和知识库 | 业务后端、接入调试页 |
| 反馈 API | 回传用户是否采纳结果 | 业务后端 |
| 管理 API | 创建项目、知识库、API Key 和配置工具 | 知识中台后台，不向普通业务暴露 |

外部业务接入主要使用运行时 API。管理 API 使用单独的管理密钥和网络访问限制，不能与项目 API Key 混用。

### 8.3 基础约定

生产环境基础地址示例：

```text
https://knowledge-api.example.com/api/v1
```

统一请求头：

```http
Authorization: Bearer <PROJECT_API_KEY>
Content-Type: application/json
X-Project-Code: ai-fund
X-Request-Id: optional-business-request-id
Idempotency-Key: optional-idempotency-key
```

规则：

1. `Authorization` 必填，由服务端解析真实 `projectId`。
2. `X-Project-Code` 建议必填，但只用于一致性校验，不能用于授权。
3. 请求中不接受可自由切换的 `projectId`。
4. `X-Request-Id` 未传时由知识中台生成。
5. 文档导入和异步任务建议传 `Idempotency-Key`，相同 Key 不重复创建任务。
6. 时间统一使用 ISO 8601，并在值中包含时区。
7. 接口版本放在 URL 中；不兼容修改必须发布 `/api/v2`。
8. API Key 只在业务服务端环境变量中保存，不返回给浏览器。

### 8.4 API Key、环境和权限范围

每个项目可以创建多把 API Key，以区分：

- 本地开发：`development`。
- 测试环境：`staging`。
- 生产环境：`production`。
- 定时采集任务：`collector`。

每把 Key 独立配置名称、环境、有效期、限流和权限范围。

| Scope | 能力 |
|---|---|
| `capabilities:read` | 读取项目能力和知识库列表 |
| `retrieval:read` | 调用内部知识检索 |
| `research:run` | 调用智能研究接口 |
| `tasks:read` | 查询当前 Key 创建的任务 |
| `knowledge:write` | 向当前项目知识库导入资料 |
| `feedback:write` | 回传业务反馈 |
| `schedules:read` | 查看当前项目定时任务和运行记录 |
| `schedules:write` | 创建、修改、暂停和手动运行定时任务 |
| `crawl:read` | 查看当前项目采集源和采集记录 |
| `crawl:write` | 创建、修改和运行当前项目采集源 |

AI 简历生产 Key 示例：

```text
project = ai-resume
environment = production
scopes = capabilities:read,retrieval:read,research:run,tasks:read,feedback:write
```

该 Key 无论在请求体中传入什么内容，都不能访问 `ai-fund` 或 `ai-ecommerce` 的知识库。

### 8.5 统一响应格式

成功响应：

```json
{
  "success": true,
  "requestId": "req_01J...",
  "data": {},
  "meta": {
    "projectCode": "ai-fund",
    "apiVersion": "v1",
    "generatedAt": "2026-07-30T15:00:00+09:00"
  }
}
```

失败响应：

```json
{
  "success": false,
  "requestId": "req_01J...",
  "error": {
    "code": "KNOWLEDGE_BASE_NOT_FOUND",
    "message": "当前项目中不存在指定知识库",
    "retryable": false,
    "details": {}
  }
}
```

生产环境的错误信息不能返回 SQL、文件物理路径、第三方密钥、模型原始异常栈或其他项目信息。

### 8.6 项目能力查询接口

接入新业务时，先通过该接口确认当前 API Key 对应的项目和可用能力。

```http
GET /api/v1/capabilities
```

返回示例：

```json
{
  "success": true,
  "requestId": "req_xxx",
  "data": {
    "projectCode": "ai-fund",
    "projectName": "AI 基金",
    "knowledgeBases": [
      {
        "code": "fund-basics",
        "name": "基金基础知识库",
        "status": "ACTIVE"
      },
      {
        "code": "fund-rules",
        "name": "投资纪律与风险规则库",
        "status": "ACTIVE"
      }
    ],
    "strategies": ["knowledge_only", "knowledge_web", "full"],
    "tools": ["fund_market", "index_market", "financial_news"],
    "limits": {
      "requestsPerMinute": 60,
      "maxContextBytes": 32768,
      "maxKnowledgeBasesPerRequest": 5
    }
  },
  "meta": {
    "projectCode": "ai-fund",
    "apiVersion": "v1",
    "generatedAt": "2026-07-30T15:00:00+09:00"
  }
}
```

`knowledgeBase.code` 在同一项目内唯一，并作为外部接口的稳定标识。数据库内部 ID 不建议暴露给业务项目。

### 8.7 纯知识检索接口

```http
POST /api/v1/retrieval/search
```

适用场景：

- AI 简历先获取岗位规则，再由原业务模型生成内容。
- AI 电商查询真实产品参数。
- 调试知识是否成功入库。
- 不需要联网和大模型，追求低延迟、低成本。

请求示例：

```json
{
  "query": "Vue 3 前端项目经历应突出哪些内容？",
  "knowledgeBaseCodes": ["frontend-jobs", "resume-rules"],
  "topK": 5,
  "filters": {
    "documentTypes": ["guide", "job_description"],
    "publishedAfter": "2026-01-01T00:00:00+08:00",
    "tags": ["frontend"]
  },
  "includeContent": true
}
```

请求规则：

- `query` 必填，长度 2～2000 字符。
- `knowledgeBaseCodes` 可选；不传时使用当前项目配置的默认知识库。
- 每个 Code 都必须属于 API Key 对应项目。
- `topK` 默认 5，最大 10。
- 仅执行当前项目内的关键词、向量和元数据混合检索。
- 不调用联网搜索和大模型。

返回示例：

```json
{
  "success": true,
  "requestId": "req_xxx",
  "data": {
    "query": "Vue 3 前端项目经历应突出哪些内容？",
    "items": [
      {
        "evidenceId": "ev_xxx",
        "knowledgeBaseCode": "resume-rules",
        "documentId": "doc_xxx",
        "documentTitle": "前端项目经历表达规范",
        "content": "项目经历应说明业务目标、个人职责、技术难点和量化结果……",
        "score": 0.87,
        "pageNumber": 3,
        "sourceUrl": null,
        "publishedAt": "2026-06-10T09:00:00+08:00",
        "metadata": {
          "tags": ["frontend", "resume"]
        }
      }
    ],
    "total": 1,
    "timingMs": 185
  },
  "meta": {
    "projectCode": "ai-resume",
    "apiVersion": "v1",
    "generatedAt": "2026-07-30T15:00:00+09:00"
  }
}
```

性能目标：P95 不超过 800ms。

### 8.8 一次调用智能研究接口

```http
POST /api/v1/research/run
```

这是其他业务项目最主要的接入接口。一次请求在知识中台内部完成证据获取、合并和一次模型生成。

支持的执行策略：

| `strategy` | 内部知识 | 联网 | 业务工具 | 模型生成 |
|---|---:|---:|---:|---:|
| `knowledge_only` | 是 | 否 | 否 | 是 |
| `knowledge_web` | 是 | 是 | 否 | 是 |
| `knowledge_tools` | 是 | 否 | 是 | 是 |
| `full` | 是 | 是 | 是 | 是 |

调用方只能选择当前项目允许策略的子集，不能通过请求临时开启被项目禁用的联网或工具。

通用请求结构：

```json
{
  "question": "需要系统分析的问题",
  "outputType": "业务输出类型",
  "strategy": "full",
  "knowledgeBaseCodes": ["当前项目内的知识库 Code"],
  "toolCodes": ["当前项目允许的工具 Code"],
  "context": {},
  "options": {
    "language": "zh-CN",
    "includeEvidence": true,
    "maxEvidence": 8
  }
}
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---:|---|
| `question` | 是 | 本次任务，最长 4000 字符 |
| `outputType` | 是 | 当前项目预先配置的输出类型 |
| `strategy` | 否 | 默认使用项目配置，不能超出项目权限 |
| `knowledgeBaseCodes` | 否 | 不传则使用输出类型绑定的默认知识库 |
| `toolCodes` | 否 | 不传则由输出类型使用固定工具，不由模型自由选择 |
| `context` | 否 | 业务项目提供的实时上下文 |
| `options.language` | 否 | 默认 `zh-CN` |
| `options.includeEvidence` | 否 | 默认 `true` |
| `options.maxEvidence` | 否 | 默认 8，最大 8 |

`context` 只用于本次分析，默认不写入正式知识库。需要沉淀时必须另外调用知识写入接口，避免用户简历、持仓或商品内部资料被无意永久保存。

AI 基金调用示例：

```json
{
  "question": "根据当前持仓、最新行情和新闻，分析主要风险及调整方向",
  "outputType": "portfolio_analysis",
  "strategy": "full",
  "knowledgeBaseCodes": ["fund-basics", "fund-rules", "fund-reports"],
  "toolCodes": ["fund_market", "index_market", "financial_news"],
  "context": {
    "holdings": [
      {
        "fundCode": "示例代码",
        "positionRatio": 0.25,
        "cost": 1.26
      }
    ],
    "riskLevel": "balanced",
    "holdingPeriod": "6-12 months"
  }
}
```

AI 简历调用示例：

```json
{
  "question": "判断这份简历与目标前端岗位的匹配度，并给出不虚构经历的修改建议",
  "outputType": "resume_job_match",
  "strategy": "knowledge_web",
  "knowledgeBaseCodes": ["frontend-jobs", "resume-rules"],
  "context": {
    "resume": {
      "skills": ["Vue 3", "TypeScript", "Node.js"],
      "projects": [
        {
          "name": "AI 简历助手",
          "description": "用户提供的真实项目描述"
        }
      ]
    },
    "jobDescription": "目标岗位 JD 原文"
  }
}
```

AI 电商调用示例：

```json
{
  "question": "根据真实产品资料和最新平台规则生成亚马逊五点描述",
  "outputType": "amazon_bullets",
  "strategy": "knowledge_web",
  "knowledgeBaseCodes": ["product-data", "amazon-rules", "brand-copy"],
  "context": {
    "productId": "product_001",
    "marketplace": "US",
    "language": "en-US"
  }
}
```

成功返回示例：

```json
{
  "success": true,
  "requestId": "req_xxx",
  "data": {
    "status": "SUCCESS",
    "outputType": "portfolio_analysis",
    "answer": "综合分析结果",
    "conclusions": [
      {
        "title": "行业集中度偏高",
        "detail": "判断说明",
        "evidenceIds": ["ev_001", "ev_002"]
      }
    ],
    "suggestedActions": [
      {
        "action": "控制单一行业集中度",
        "priority": "HIGH",
        "reason": "对应理由",
        "evidenceIds": ["ev_001"]
      }
    ],
    "evidence": [
      {
        "id": "ev_001",
        "type": "INTERNAL",
        "title": "投资纪律与风险规则",
        "snippet": "证据摘要",
        "sourceUrl": null,
        "publishedAt": "2026-07-01T10:00:00+08:00",
        "dataAsOf": null,
        "score": 0.88
      },
      {
        "id": "ev_002",
        "type": "TOOL",
        "title": "基金行情数据",
        "snippet": "结构化数据摘要",
        "sourceUrl": null,
        "publishedAt": null,
        "dataAsOf": "2026-07-30T14:55:00+08:00",
        "score": 0.93
      }
    ],
    "confidence": 0.81,
    "uncertainties": [],
    "riskNotice": "结果仅用于信息分析，不承诺收益，也不构成自动交易指令。",
    "degraded": false,
    "degradedReasons": [],
    "timing": {
      "internalRetrievalMs": 320,
      "externalParallelMs": 1820,
      "generationMs": 3100,
      "totalMs": 5240
    }
  },
  "meta": {
    "projectCode": "ai-fund",
    "apiVersion": "v1",
    "generatedAt": "2026-07-30T15:00:00+09:00"
  }
}
```

接口性能要求：

- 内部知识、联网和工具并行执行。
- 单次请求最多调用一次大模型。
- 不进行模型规划、循环搜索和反思。
- 网络或业务工具超时后返回降级结果，不阻塞整个请求。
- 普通同步请求硬超时 15 秒。
- P95 目标不超过 12 秒。

### 8.9 异步研究接口

MVP 主要使用同步 `/research/run`。只有批量分析或预计超过 15 秒的任务才使用异步接口。

```http
POST /api/v1/research/jobs
```

请求体与 `/research/run` 相同，必须提供 `Idempotency-Key`。

立即返回：

```json
{
  "success": true,
  "requestId": "req_xxx",
  "data": {
    "jobId": "job_xxx",
    "status": "PENDING",
    "statusUrl": "/api/v1/research/jobs/job_xxx"
  }
}
```

查询任务：

```http
GET /api/v1/research/jobs/{jobId}
```

任务状态：

```text
PENDING → RUNNING → SUCCESS
                  ↘ PARTIAL_SUCCESS
                  ↘ FAILED
                  ↘ TIMEOUT
```

只能查询同一项目且由当前 Key 或同项目授权 Key 创建的任务。MVP 不强制实现 Webhook；P1 可增加完成回调。

### 8.10 知识写入接口

#### 8.10.1 上传文件

```http
POST /api/v1/knowledge-bases/{knowledgeBaseCode}/documents/file
Content-Type: multipart/form-data
```

表单字段：

| 字段 | 必填 | 说明 |
|---|---:|---|
| `file` | 是 | PDF、DOCX、TXT 或 Markdown |
| `title` | 否 | 未传时使用文件名 |
| `tags` | 否 | JSON 字符串数组 |
| `externalId` | 否 | 业务项目中的稳定资源 ID |
| `metadata` | 否 | JSON 对象 |

#### 8.10.2 写入文本或网页

```http
POST /api/v1/knowledge-bases/{knowledgeBaseCode}/documents
```

文本示例：

```json
{
  "type": "TEXT",
  "title": "前端岗位分析规则",
  "content": "正文内容",
  "externalId": "resume-rule-001",
  "tags": ["frontend", "resume"],
  "metadata": {}
}
```

网页示例：

```json
{
  "type": "URL",
  "title": "资料标题",
  "url": "https://example.com/article",
  "externalId": "article-001",
  "tags": ["policy"],
  "metadata": {}
}
```

返回文档和异步入库任务：

```json
{
  "success": true,
  "requestId": "req_xxx",
  "data": {
    "documentId": "doc_xxx",
    "ingestionTaskId": "ing_xxx",
    "status": "PENDING"
  }
}
```

查询处理状态：

```http
GET /api/v1/documents/{documentId}
```

状态：

```text
PENDING → PARSING → CHUNKING → EMBEDDING → READY
                                      ↘ FAILED
```

写入规则：

1. API Key 必须拥有 `knowledge:write`。
2. URL 中的 `knowledgeBaseCode` 必须属于当前项目。
3. `externalId` 在同一项目、同一知识库内唯一，可用于幂等更新。
4. 文档解析和向量化异步执行，不阻塞业务请求。
5. 业务上下文不会因为调用 `/research/run` 自动写入知识库。

### 8.11 定时任务接口

创建定时任务：

```http
POST /api/v1/schedules
```

请求示例：

```json
{
  "name": "每小时采集财经新闻",
  "taskType": "CRAWL_SOURCE",
  "cronExpression": "0 * * * *",
  "timezone": "Asia/Shanghai",
  "enabled": true,
  "concurrencyPolicy": "SKIP",
  "timeoutSeconds": 300,
  "maxRetries": 2,
  "config": {
    "crawlSourceCode": "financial-news",
    "knowledgeBaseCode": "fund-news"
  }
}
```

项目由 API Key 确定，请求体不接收 `projectId`。

其他接口：

```http
GET    /api/v1/schedules
GET    /api/v1/schedules/{scheduleId}
PATCH  /api/v1/schedules/{scheduleId}
POST   /api/v1/schedules/{scheduleId}/pause
POST   /api/v1/schedules/{scheduleId}/resume
POST   /api/v1/schedules/{scheduleId}/run
GET    /api/v1/schedules/{scheduleId}/runs
GET    /api/v1/schedule-runs/{runId}
```

手动运行也进入后台队列，并返回 `runId`，不在 HTTP 请求中等待爬取或分析完成。

### 8.12 爬虫源与采集接口

创建采集源：

```http
POST /api/v1/crawl-sources
```

请求示例：

```json
{
  "code": "financial-news",
  "name": "财经新闻公开源",
  "type": "RSS",
  "startUrls": ["https://example.com/feed.xml"],
  "allowedDomains": ["example.com"],
  "destinationKnowledgeBaseCode": "fund-news",
  "importPolicy": "REVIEW_REQUIRED",
  "limits": {
    "maxPagesPerRun": 50,
    "maxDepth": 1,
    "requestIntervalMs": 1500,
    "concurrencyPerDomain": 1
  },
  "extract": {
    "title": "rss:title",
    "content": "rss:content",
    "publishedAt": "rss:pubDate"
  }
}
```

其他接口：

```http
GET    /api/v1/crawl-sources
GET    /api/v1/crawl-sources/{sourceId}
PATCH  /api/v1/crawl-sources/{sourceId}
POST   /api/v1/crawl-sources/{sourceId}/pause
POST   /api/v1/crawl-sources/{sourceId}/resume
POST   /api/v1/crawl-sources/{sourceId}/runs
GET    /api/v1/crawl-sources/{sourceId}/runs
GET    /api/v1/crawl-runs/{runId}
GET    /api/v1/crawl-runs/{runId}/pages
POST   /api/v1/crawl-pages/{pageId}/approve
POST   /api/v1/crawl-pages/{pageId}/reject
```

手动触发返回：

```json
{
  "success": true,
  "requestId": "req_xxx",
  "data": {
    "runId": "crawl_run_xxx",
    "status": "PENDING"
  }
}
```

### 8.13 结果反馈接口

```http
POST /api/v1/research/{requestId}/feedback
```

请求示例：

```json
{
  "rating": "HELPFUL",
  "accepted": true,
  "comment": "岗位差距分析有帮助",
  "businessResultId": "resume_analysis_123"
}
```

反馈状态可使用：

- `HELPFUL`
- `PARTIALLY_HELPFUL`
- `NOT_HELPFUL`

反馈用于统计和后续优化提示词、检索排序，不直接自动修改正式知识。

### 8.14 健康检查接口

```http
GET /health
GET /ready
```

- `/health`：服务进程存活即可返回成功。
- `/ready`：数据库和必要依赖可用时返回成功。
- 健康检查不需要项目 API Key，但不能返回内部连接信息。

### 8.15 HTTP 状态码与业务错误码

| HTTP 状态 | 错误码 | 含义 | 是否建议重试 |
|---:|---|---|---:|
| 400 | `VALIDATION_ERROR` | 请求字段错误 | 否 |
| 401 | `INVALID_API_KEY` | API Key 无效或过期 | 否 |
| 403 | `SCOPE_NOT_ALLOWED` | Key 缺少所需 Scope | 否 |
| 403 | `PROJECT_DISABLED` | 项目已停用 | 否 |
| 403 | `PROJECT_CODE_MISMATCH` | Header 项目标识与密钥不一致 | 否 |
| 403 | `PROJECT_SCOPE_MISMATCH` | 访问了其他项目资源 | 否 |
| 404 | `KNOWLEDGE_BASE_NOT_FOUND` | 当前项目内无此知识库 | 否 |
| 404 | `TASK_NOT_FOUND` | 当前项目内无此任务 | 否 |
| 404 | `CRAWL_SOURCE_NOT_FOUND` | 当前项目内无此采集源 | 否 |
| 409 | `SCHEDULE_RUN_CONFLICT` | 同一计划时间已有有效运行 | 否 |
| 409 | `IDEMPOTENCY_CONFLICT` | 同一幂等 Key 的请求内容不同 | 否 |
| 422 | `OUTPUT_TYPE_NOT_ALLOWED` | 项目未配置该输出类型 | 否 |
| 422 | `TOOL_NOT_ALLOWED` | 当前项目不允许使用该工具 | 否 |
| 422 | `INSUFFICIENT_EVIDENCE` | 证据不足，无法给出可靠结论 | 否 |
| 429 | `RATE_LIMITED` | 超过当前 Key 限流 | 是 |
| 502 | `EXTERNAL_SOURCE_FAILED` | 外部数据源失败 | 是 |
| 504 | `EXTERNAL_SOURCE_TIMEOUT` | 网络或业务工具超时 | 是 |
| 504 | `MODEL_TIMEOUT` | 模型生成超时 | 是 |
| 422 | `CRAWL_URL_NOT_ALLOWED` | URL 域名、协议或地址不允许采集 | 否 |
| 422 | `CRAWL_RULE_INVALID` | 页面提取规则无效 | 否 |
| 500 | `INTERNAL_ERROR` | 系统内部错误 | 是 |

发生外部来源超时时，如内部证据足够，优先返回 `success: true`、`degraded: true` 的降级结果，而不是直接失败。

### 8.16 限流、重试与幂等

- 默认每把 Key 每分钟 60 次，可按项目调整。
- `/retrieval/search` 和 `/research/run` 分开计数。
- 返回 429 时通过 `Retry-After` 告知建议等待秒数。
- 业务方仅对 429、502、504、500 进行重试。
- 同步分析最多自动重试 1 次，使用指数退避，避免重复消耗 Token。
- 文档写入和异步任务必须使用 `Idempotency-Key`。
- 服务端保存幂等记录 24 小时。
- 相同 Key、相同请求内容直接返回原任务；内容不同返回 409。
- 定时运行使用 `scheduleId + plannedAt` 作为内部幂等键。
- 采集页面使用项目、采集源、规范化 URL 和正文哈希去重。

### 8.17 接入代码示例

#### Node.js / TypeScript

```ts
const response = await fetch(
  `${process.env.KNOWLEDGE_API_URL}/api/v1/research/run`,
  {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${process.env.KNOWLEDGE_PROJECT_API_KEY}`,
      "Content-Type": "application/json",
      "X-Project-Code": "ai-resume"
    },
    body: JSON.stringify({
      question: "分析简历与目标岗位的匹配度",
      outputType: "resume_job_match",
      strategy: "knowledge_web",
      knowledgeBaseCodes: ["frontend-jobs", "resume-rules"],
      context: {
        resume: resumeData,
        jobDescription: jdText
      }
    })
  }
);

const result = await response.json();

if (!response.ok || !result.success) {
  throw new Error(result.error?.message ?? "知识中台调用失败");
}

return result.data;
```

#### Java / Spring Boot

```java
// 建议把地址和 API Key 放在 application.yml 或环境变量中，不要写死在代码里。
WebClient client = WebClient.builder()
        .baseUrl(knowledgeApiUrl)
        .defaultHeader(HttpHeaders.AUTHORIZATION, "Bearer " + projectApiKey)
        .defaultHeader("X-Project-Code", "ai-fund")
        .build();

Map<String, Object> body = Map.of(
        "question", "分析当前基金持仓风险",
        "outputType", "portfolio_analysis",
        "strategy", "full",
        "knowledgeBaseCodes", List.of("fund-basics", "fund-rules"),
        "context", Map.of("holdings", holdings)
);

KnowledgeResponse result = client.post()
        .uri("/api/v1/research/run")
        .contentType(MediaType.APPLICATION_JSON)
        .bodyValue(body)
        .retrieve()
        .bodyToMono(KnowledgeResponse.class)
        .timeout(Duration.ofSeconds(16))
        .block();
```

生产代码需统一封装为 `KnowledgeClient`，业务模块不能到处直接拼接 URL 和 Header。

### 8.18 三个项目的接入映射

| 业务项目 | API Key 绑定项目 | 默认知识库 | 主要接口 | 定时采集示例 |
|---|---|---|---|---|
| AI 简历 | `ai-resume` | `frontend-jobs`、`resume-rules` | `/research/run` | 每天更新招聘 JD |
| AI 基金 | `ai-fund` | `fund-basics`、`fund-rules`、`fund-reports` | `/research/run` | 每小时采集财经新闻 |
| AI 电商 | `ai-ecommerce` | `product-data`、`amazon-rules`、`brand-copy` | `/research/run` | 每天更新平台规则 |

每个项目的业务后端只配置自己的 API Key。即使三个业务项目部署在同一台服务器，也不得复用同一把 Key。

### 8.19 接入验收标准

1. AI 简历后端只调用一次 `/research/run` 即可获得岗位匹配结论、建议和引用。
2. AI 基金 Key 请求简历知识库 Code 时返回 `KNOWLEDGE_BASE_NOT_FOUND`，响应中不泄露该知识库是否真实存在。
3. AI 电商 Key 无法查询 AI 基金创建的 `requestId` 和 `documentId`。
4. Key 被停用后，所有运行时和写入接口立即返回 401 或 403。
5. 不具备 `knowledge:write` Scope 的 Key 无法上传文档。
6. 相同 `Idempotency-Key` 的相同文档导入请求只创建一个任务。
7. 联网超时但内部知识足够时，接口返回可用的降级结果并标明原因。
8. `/retrieval/search` P95 不超过 800ms。
9. `/research/run` P95 不超过 12 秒，硬超时不超过 15 秒。
10. 外部业务前端代码和浏览器网络请求中不出现项目 API Key。
11. 业务项目可以通过 API 创建、暂停、恢复和手动运行自己的定时任务。
12. 业务项目可以通过 API 创建公开网页采集源，并将结果写入本项目资料池或知识库。
13. 任何项目均不能查看或触发其他项目的定时任务和采集运行。

---

## 9. 数据库设计

推荐使用 PostgreSQL + pgvector。业务数据、元数据、全文检索和向量检索可以先放在同一个数据库中，降低部署复杂度。

### 9.1 核心数据表

| 表名 | 用途 | 必须包含 `project_id` |
|---|---|---|
| `projects` | 项目空间 | 自身主表 |
| `project_api_keys` | 项目 API Key | 是 |
| `project_settings` | 模型、联网和性能配置 | 是 |
| `knowledge_bases` | 项目下知识库 | 是 |
| `documents` | 文档和网页元数据 | 是 |
| `document_chunks` | 文本片段和向量 | 是 |
| `source_policies` | 可信/禁用来源 | 是 |
| `web_materials` | 网络待审核资料 | 是 |
| `crawl_sources` | 项目爬虫源和提取配置 | 是 |
| `crawl_runs` | 每次采集运行 | 是 |
| `crawl_pages` | 发现页面、哈希、状态和入库结果 | 是 |
| `schedules` | Cron、时区、任务类型和参数 | 是 |
| `schedule_runs` | 定时任务运行与幂等记录 | 是 |
| `tool_definitions` | 全局工具定义 | 否 |
| `project_tools` | 项目工具白名单和配置 | 是 |
| `prompt_versions` | 项目提示词版本 | 是 |
| `ingestion_tasks` | 文档处理任务 | 是 |
| `research_tasks` | 智能研究任务 | 是 |
| `evidence_items` | 内部、网络和工具证据 | 是 |
| `research_results` | 研究结果 | 是 |
| `usage_logs` | Token、费用和耗时 | 是 |

### 9.2 关键字段

#### `projects`

- `id`
- `project_code`
- `name`
- `description`
- `status`
- `created_at`
- `updated_at`

#### `document_chunks`

- `id`
- `project_id`
- `knowledge_base_id`
- `document_id`
- `content`
- `content_tsvector`
- `embedding`
- `title`
- `page_number`
- `source_url`
- `published_at`
- `metadata`
- `status`
- `created_at`

#### `research_tasks`

- `id`
- `project_id`
- `request_id`
- `question`
- `input_context`
- `knowledge_base_ids`
- `requested_tools`
- `use_web`
- `status`
- `prompt_version_id`
- `started_at`
- `completed_at`
- `total_duration_ms`
- `error_code`

#### `schedules`

- `id`
- `project_id`
- `name`
- `task_type`
- `cron_expression`
- `timezone`
- `config`
- `concurrency_policy`
- `timeout_seconds`
- `max_retries`
- `enabled`
- `next_run_at`
- `last_run_at`
- `created_at`
- `updated_at`

#### `schedule_runs`

- `id`
- `project_id`
- `schedule_id`
- `planned_at`
- `started_at`
- `completed_at`
- `status`
- `attempt`
- `queue_job_id`
- `result_summary`
- `error_code`
- `error_message`

联合唯一约束：

```text
UNIQUE(project_id, schedule_id, planned_at)
```

#### `crawl_sources`

- `id`
- `project_id`
- `code`
- `name`
- `type`
- `start_urls`
- `allowed_domains`
- `destination_knowledge_base_id`
- `extract_rules`
- `import_policy`
- `limits`
- `status`
- `created_at`
- `updated_at`

#### `crawl_pages`

- `id`
- `project_id`
- `crawl_source_id`
- `crawl_run_id`
- `url`
- `canonical_url`
- `canonical_url_hash`
- `title`
- `content_hash`
- `published_at`
- `fetched_at`
- `http_status`
- `status`
- `document_id`
- `error_code`
- `metadata`

### 9.3 必要索引

- `projects(project_code)` 唯一索引。
- `knowledge_bases(project_id, status)`。
- `documents(project_id, knowledge_base_id, status)`。
- `document_chunks(project_id, knowledge_base_id, status)`。
- `document_chunks` 的向量索引。
- `document_chunks` 的全文检索 GIN 索引。
- `research_tasks(project_id, created_at DESC)`。
- `evidence_items(project_id, research_task_id)`。
- `usage_logs(project_id, created_at DESC)`。
- `schedules(project_id, enabled, next_run_at)`。
- `schedule_runs(project_id, schedule_id, planned_at DESC)`。
- `schedule_runs(project_id, schedule_id, planned_at)` 唯一索引。
- `crawl_sources(project_id, code)` 唯一索引。
- `crawl_runs(project_id, crawl_source_id, created_at DESC)`。
- `crawl_pages(project_id, crawl_source_id, canonical_url_hash)`。
- `crawl_pages(project_id, content_hash)`。

---

## 10. 技术架构

### 10.1 推荐技术栈

前端：

- React 19
- TypeScript
- Vite
- Ant Design
- Tailwind CSS
- Zustand
- TanStack Query
- React Router

后端：

- Python 3.12+
- FastAPI
- Pydantic
- SQLAlchemy 2
- Alembic
- PostgreSQL
- pgvector
- Redis
- Celery
- Celery Beat

可选组件：

- 对象存储：生产环境保存原始文件；本地开发可先使用本地目录。
- Playwright：P1 支持确有需要的动态网页，MVP 静态 HTML/RSS/Sitemap 不依赖浏览器。
- Trafilatura/BeautifulSoup：网页正文提取与清洗。
- PyMuPDF/python-docx：PDF 与 Word 文档解析。

Redis 与 Celery 为 P0 组件，用于文档处理、定时任务和网页采集的后台队列。PostgreSQL 是任务状态与业务数据的最终数据源，Redis 只保存队列、锁和短期缓存。

### 10.2 模块划分

```text
projects            项目、配置和 API Key
knowledge           知识库、文档和片段
ingestion           解析、切割和向量化
retrieval           项目内混合检索
web_research        搜索、网页提取和来源过滤
crawler             采集源、页面发现、提取、去重和入库
scheduler           Cron 扫描、幂等触发和运行管理
workers             Celery 队列、优先级和重试
tools               工具注册、白名单和调用
prompts             项目提示词和版本
research            并行取证、合并和一次模型生成
audit               日志、耗时、Token 和隔离审计
```

### 10.3 请求上下文

FastAPI 鉴权依赖解析 API Key 后创建只读 `ProjectContext`：

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class ProjectContext:
    project_id: str
    project_code: str
    api_key_id: str
    allowed_tools: tuple[str, ...]
```

后续模块只能从服务端上下文读取 `project_id`，不能信任请求体提供的项目 ID。

---

## 11. 非功能需求

### 11.1 性能

- 纯内部检索 P95 ≤ 800ms。
- 内部问答 P95 ≤ 5 秒。
- 联网/业务工具分析 P95 ≤ 12 秒。
- 整体硬超时 15 秒。
- 内部检索、网络搜索和工具调用并行执行。
- 单次模型调用，禁止隐藏的二次总结。
- 网页采集、文档向量化和定时任务全部异步执行。
- 在线研究任务使用高优先级队列，后台采集使用低优先级队列。
- 每个项目限制最大并发采集数，避免单个项目耗尽 Worker。

### 11.2 安全

- API Key 只保存哈希。
- 日志不记录完整 API Key。
- 上传文件限制类型、大小和数量。
- 网页抓取需要防止 SSRF，拒绝内网和本机地址。
- 爬虫遵守允许域名、robots、请求频率和内容大小限制。
- 定时任务仅能选择注册任务类型，不能执行任意用户代码。
- 项目级限流，避免一个项目耗尽全部资源。
- 敏感业务输入在日志中支持脱敏。

### 11.3 可观察性

每次任务记录：

- 项目和请求 ID。
- 内部检索耗时。
- 网络和各工具耗时。
- 模型耗时。
- 命中证据数量。
- Token 和估算费用。
- 降级原因和错误码。
- 定时任务计划时间、实际开始时间、漂移时间和重试次数。
- 爬虫发现、成功、重复、失败、审核和入库数量。

### 11.4 隔离测试

必须建立自动化测试：

1. 使用 AI 基金 API Key 查询 AI 简历知识库，返回拒绝。
2. 伪造其他项目 `knowledgeBaseId`，不能召回任何片段。
3. 伪造 `X-Project-Code`，返回项目不一致。
4. AI 简历调用基金工具，返回工具未授权。
5. 切换后台项目后，不显示前一项目缓存数据。
6. 删除和更新其他项目资源时，影响行数必须为 0。
7. 向量相似度极高的跨项目片段也绝不能被召回。
8. AI 基金定时任务不能选择 AI 简历爬虫源或知识库。
9. 伪造其他项目 `scheduleId`、`crawlSourceId` 和 `crawlRunId` 时返回不可访问。
10. 多实例调度器同时扫描时，同一计划时间只能创建一个有效运行。
11. 爬虫访问内网、localhost 和云元数据地址时必须被拦截。

---

## 12. MVP 优先级

### P0：必须完成

- 项目创建、编辑、启停。
- 项目 API Key。
- 项目级知识库。
- PDF、Word、TXT、Markdown 和手动文本导入。
- 文档解析、切割和向量化。
- 带 `projectId` 强过滤的混合检索。
- 项目独立提示词。
- 项目工具白名单。
- 内部、联网和工具并行取证。
- 一次模型生成。
- 来源、时间、风险和不确定项。
- 执行记录和耗时。
- 跨项目隔离自动化测试。
- Redis + Celery 后台队列。
- 项目级定时任务、Cron、时区和运行记录。
- RSS、Sitemap、单页、URL 列表和列表页采集。
- 采集去重、增量更新、资料审核和可信源自动入库。
- 定时任务与爬虫对外 API。

### P1：MVP 稳定后

- 网络资料审核池。
- 项目统计面板。
- API Key 轮换和调用限额。
- 来源可信度评分。
- 研究结果人工确认后沉淀知识。
- Playwright 动态网页采集。
- 采集失败通知和 Webhook。

### P2：后续版本

- 用户登录和角色权限。
- 组织与多租户。
- 显式授权的公共知识库。
- 长期记忆。
- 固定业务 Workflow。
- 更复杂的 Agent 和人工审批节点。
- 分布式爬虫 Worker 自动扩缩容。

---

## 13. 开发计划

### 第 1 周：项目隔离骨架

- 初始化 React 前端与 FastAPI 后端。
- 建立 PostgreSQL 和 pgvector。
- 完成项目、配置、API Key 和知识库表。
- 实现 `ProjectContext`、FastAPI 鉴权依赖和统一项目过滤。
- 完成项目管理和知识库页面。
- 编写基础跨项目隔离测试。
- 建立 Redis、Celery Worker、Celery Beat 和在线/后台队列。

### 第 2 周：知识处理与检索

- 完成文件导入和文档状态。
- 实现 PDF、Word、TXT、Markdown 解析。
- 实现切割、Embedding 和向量存储。
- 完成项目内关键词 + 向量混合检索。
- 完成知识详情和检索测试页。

### 第 3 周：联网、工具和短链路

- 接入网络搜索与正文提取。
- 完成来源过滤。
- 建立工具注册和项目白名单。
- 实现内部、网络和工具并行执行。
- 实现证据去重、评分和截断。
- 完成一次模型生成和结构化输出。

### 第 4 周：定时任务与数据采集

- 完成定时任务 CRUD、Cron、时区和运行记录。
- 完成调度幂等、并发策略、重试和暂停。
- 完成 RSS、Sitemap、单页、URL 列表和列表页采集。
- 完成 SSRF 防护、域名限制、限速、正文提取和内容清洗。
- 完成 URL/正文去重、增量更新和目标知识库入库。
- 完成定时任务和爬虫管理页面。

### 第 5 周：业务接入与验收

- 建立 `ai-fund`、`ai-resume`、`ai-ecommerce` 三个演示项目。
- 至少完整接入一个现有业务项目。
- 为三个项目分别建立一个定时采集示例。
- 完成执行记录、耗时、Token 和错误展示。
- 完成跨项目攻击测试、超时和降级测试。
- 完成调度重复执行、爬虫安全和资源限额测试。
- 完成部署文档与 API 文档。

---

## 14. 验收标准

MVP 上线前必须同时满足：

1. 能创建 AI 基金、AI 简历和 AI 电商三个独立项目。
2. 每个项目能创建自己的知识库并导入文档。
3. 同一关键词在不同项目中只能命中各自知识。
4. 伪造知识库 ID、文档 ID 或项目代码均不能越权访问。
5. 不同项目可以使用不同提示词和工具白名单。
6. 单次研究任务最多调用一次大模型。
7. 内部检索、联网和业务工具可并行执行。
8. 网络或工具超时后能在 15 秒内降级返回。
9. 回答包含来源、时间、风险和不确定项。
10. 可以查看当前项目的执行步骤、证据、耗时和 Token。
11. AI 基金结果不包含简历或电商知识，反之亦然。
12. 至少一个真实业务项目通过 API 完成端到端接入。
13. 每个项目可以独立创建、暂停、恢复和手动执行定时任务。
14. 定时任务支持明确时区，服务重启和多实例部署不会重复执行同一计划。
15. 可以采集公开的 HTML、RSS 和 Sitemap，并完成清洗、去重、审核或自动入库。
16. 爬虫不能访问内网、本机、云元数据地址或其他项目资源。
17. 后台定时采集不明显影响在线检索和研究接口性能。

---

## 15. 首版开发结论

第一版应完成“项目强隔离 + 知识检索 + 外部证据 + 一次模型生成 + 后台定时采集”，不急着开发复杂 Agent。

最终链路保持为：

```text
项目 API Key 确定唯一项目
→ 当前项目内部知识、网络和允许工具并行取证
→ 程序整理最多 8 条证据
→ 大模型一次生成
→ 返回可追溯结果
```

这套设计既能让 AI 基金、AI 简历和 AI 电商共享同一套技术能力，又能保证各项目的知识、工具和业务规则互不污染。后续增加登录、用户记忆、工作流或多租户时，也可以继续沿用 `project_id` 作为最基础的隔离边界。

定时任务和爬虫属于后台数据更新链路，与在线问答链路分开：

```text
Cron 调度
→ 后台低优先级队列
→ 公开数据采集、去重和审核/入库
→ 更新当前项目知识

用户请求
→ 读取已经更新的知识和实时工具
→ 一次模型生成
→ 快速返回结果
```

这种拆分可以让系统持续获得新数据，同时避免用户每次提问都等待完整网页采集。
