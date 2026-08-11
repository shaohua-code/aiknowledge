"""API Key 权限范围（Scope）常量定义。

对应 SubTask 5.4：集中管理所有 Scope 字符串与常用组合。

Scope 设计说明（务必阅读）
--------------------------
1. 命名规范 ``<资源>:<动作>``
   - 资源：capabilities / retrieval / research / tasks / knowledge / feedback
            / schedules / crawl
   - 动作：read / write / run
   命名一旦发布即稳定，不可改名（否则旧 API Key 失效）。

2. 最小权限原则
   每个 API Key 仅授予其调用接口所需的最小 Scope 集合。
   例如只读检索的 Key 不应授予 ``knowledge:write``。

3. 常用组合
   为了便于后台批量签发 Key，预定义几个常用组合：
   - SCOPES_RUNTIME：运行时 API（检索 + 研究 + 任务 + 反馈）
   - SCOPES_WRITE：写入 API（知识库上传/删除文档）
   - SCOPES_SCHEDULE：定时任务管理
   - SCOPES_CRAWL：爬虫采集
   组合是 tuple 不可变，调用方按需选取并入库 ``api_keys.scopes``。

4. ALL_SCOPES
   所有合法 Scope 的全集，用于：
   - 后台校验创建 Key 时 scopes 是否合法
   - 文档生成与单元测试覆盖
"""
from __future__ import annotations

# ============================================================================
# 单个 Scope 常量
# ============================================================================

# 能力声明：查询当前 API Key / 项目支持的能力（模型、工具、输出类型）
# 主要用于 GET /capabilities 接口
SCOPE_CAPABILITIES_READ = "capabilities:read"

# 检索读取：调用向量检索 / 关键词检索接口
# 主要用于 POST /retrieval/search
SCOPE_RETRIEVAL_READ = "retrieval:read"

# 研究执行：触发一次完整研究链路（检索 + 联网 + 工具 + 生成）
# 主要用于 POST /research/run，是最重的接口，应严格管控
SCOPE_RESEARCH_RUN = "research:run"

# 任务查询：查询异步任务状态（研究任务、文档处理任务、调度运行）
# 主要用于 GET /tasks/{jobId} 等只读接口
SCOPE_TASKS_READ = "tasks:read"

# 知识库写入：上传、删除文档，触发重新 embedding
# 主要用于 POST /knowledge-bases/{code}/documents 等写接口
SCOPE_KNOWLEDGE_WRITE = "knowledge:write"

# 反馈写入：用户对研究结论的点赞 / 点踩 / 修正建议
# 主要用于 POST /feedback 接口
SCOPE_FEEDBACK_WRITE = "feedback:write"

# 定时任务读取：查询调度配置与运行历史
# 主要用于 GET /schedules 系列接口
SCOPE_SCHEDULES_READ = "schedules:read"

# 定时任务写入：创建、修改、启停调度
# 主要用于 POST/PATCH /schedules 接口
SCOPE_SCHEDULES_WRITE = "schedules:write"

# 爬虫读取：查询采集源配置与历史采集结果
# 主要用于 GET /crawl-sources 等只读接口
SCOPE_CRAWL_READ = "crawl:read"

# 爬虫写入：创建采集源、手动触发采集
# 主要用于 POST /crawl-sources、POST /crawl-sources/{id}/run
SCOPE_CRAWL_WRITE = "crawl:write"

# ============================================================================
# Scope 全集
# ============================================================================
# 所有合法 Scope 的元组，用于后台校验与文档生成
# 新增 Scope 时务必同步追加到此元组，否则会被认为非法
ALL_SCOPES: tuple[str, ...] = (
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
)

# ============================================================================
# 常用 Scope 组合（便于后台批量签发）
# ============================================================================

# 运行时 API：客户端调用一次完整研究链路所需的最小权限
# 包含：能力查询 + 检索 + 研究 + 任务状态 + 反馈
# 适用于：业务方生产环境的调用方 Key
SCOPES_RUNTIME: tuple[str, ...] = (
    SCOPE_CAPABILITIES_READ,
    SCOPE_RETRIEVAL_READ,
    SCOPE_RESEARCH_RUN,
    SCOPE_TASKS_READ,
    SCOPE_FEEDBACK_WRITE,
)

# 写入 API：仅授予知识库写入权限，不含检索/研究
# 适用于：内容运营批量上传文档的 Key
SCOPES_WRITE: tuple[str, ...] = (
    SCOPE_KNOWLEDGE_WRITE,
)

# 定时任务：调度读取 + 写入
# 适用于：调度管理系统 / 运维工具的 Key
SCOPES_SCHEDULE: tuple[str, ...] = (
    SCOPE_SCHEDULES_READ,
    SCOPE_SCHEDULES_WRITE,
)

# 爬虫：采集源读取 + 写入
# 适用于：采集器实例（environment=collector）的 Key
SCOPES_CRAWL: tuple[str, ...] = (
    SCOPE_CRAWL_READ,
    SCOPE_CRAWL_WRITE,
)
