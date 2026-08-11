# 性能与降级测试文档

> 对应 SubTask 25.5：性能测试方法说明与降级测试场景设计。
>
> 本文档为测试方法学说明，性能测试脚本与降级测试用例需根据实际部署环境编写。
> 降级测试场景中给出的 mock 示例代码片段可直接用于单元测试。

---

## 一、性能测试方法

### 1.1 测试目标

- 验证 `/research/run` 接口在并发场景下的响应时间与吞吐量
- 验证 `/retrieval/search` 接口的 P95 / P99 延迟
- 验证项目级限流（60/min）的生效阈值
- 验证数据库连接池（pool_size=10, max_overflow=20）的承载能力
- 发现系统瓶颈（CPU / 内存 / DB 连接 / 外部依赖）

### 1.2 推荐工具

#### Locust（推荐，Python 生态，可编写复杂场景）

```bash
# 安装 locust
pip install locust

# 启动 locust Web 控制台
cd backend
locust -f tests/locustfile_research.py --host=http://localhost:8000
```

#### wrk（轻量级 HTTP 压测，适合快速验证）

```bash
# 压测 /retrieval/search（需先准备 API Key 与请求体）
wrk -t4 -c50 -d30s -H "Authorization: Bearer ikh_live_xxx" \
    -H "Content-Type: application/json" \
    -s scripts/wrk_retrieval.lua \
    http://localhost:8000/api/v1/retrieval/search
```

### 1.3 Locust 测试脚本示例

新建 `backend/tests/locustfile_research.py`：

```python
"""Locust 性能测试脚本：模拟并发调用 /research/run。"""
from locust import HttpUser, task, between


class ResearchRunUser(HttpUser):
    """模拟业务方调用 /research/run 的用户。"""

    # 每个用户请求间隔：1-3 秒，模拟真实业务节奏
    wait_time = between(1, 3)

    # 演示用 API Key（需通过 seed_demo_projects 生成后替换）
    api_key = "ikh_live_REPLACE_WITH_REAL_KEY"

    def on_start(self):
        """用户启动时设置公共请求头。"""
        self.client.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Project-Code": "ai-fund",
        }

    @task(3)
    def research_run(self):
        """主任务：调用 /research/run（权重 3）。

        字段含义：
            question: 用户问题原文
            outputType: 输出类型 narrative / json / bullet_points
            strategy: 研究策略 knowledge_only / knowledge_web / knowledge_tools / full
            knowledgeBaseIds: 知识库 ID 列表（需先通过 API 查询得到真实 ID）
        """
        self.client.post(
            "/api/v1/research/run",
            json={
                "question": "新能源基金近一年表现如何？",
                "outputType": "narrative",
                "strategy": "full",
                "knowledgeBaseIds": ["REPLACE_WITH_REAL_KB_ID"],
                "toolCodes": ["fund_market"],
                "toolInputs": {"fund_market": {"fund_codes": ["000001"]}},
            },
        )

    @task(7)
    def retrieval_search(self):
        """高频任务：调用 /retrieval/search（权重 7，比研究接口更频繁）。"""
        self.client.post(
            "/api/v1/retrieval/search",
            json={
                "query": "基金投资策略",
                "knowledgeBaseIds": ["REPLACE_WITH_REAL_KB_ID"],
                "topK": 5,
            },
        )
```

### 1.4 测试场景与预期指标

| 场景 | 并发用户数 | 持续时间 | 预期 P95 延迟 | 预期吞吐 | 备注 |
|------|-----------|---------|--------------|---------|------|
| 检索接口基线 | 10 | 60s | < 500ms | > 20 RPS | 无外部依赖，纯 DB 查询 |
| 检索接口峰值 | 50 | 60s | < 1500ms | > 30 RPS | 连接池满载 |
| 研究接口基线 | 5 | 120s | < 8s | > 0.5 RPS | 含模型生成 + 检索 |
| 研究接口峰值 | 20 | 120s | < 15s | > 1 RPS | 受模型 API 限流 |
| 限流验证 | 100 | 30s | N/A | 60 RPS（被限流） | 429 响应占比应上升 |

### 1.5 监控指标

压测期间应监控以下指标：

- **应用层**：QPS / P95 / P99 / 错误率（通过 locust 控制台）
- **数据库**：连接数 / 慢查询 / 索引命中（通过 pg_stat_statements）
- **Redis**：连接数 / 内存（限流计数与幂等键）
- **外部依赖**：模型 API 延迟 / Web 搜索延迟 / 工具调用延迟
- **系统资源**：CPU / 内存 / 网络吞吐（通过 prometheus / node_exporter）

---

## 二、降级测试场景

### 2.1 降级策略概述

研究链路（`/research/run`）采用"并行取证 + 一次模型生成"设计，
单路取证失败不阻塞其他路，记录降级原因后继续。

降级场景分为以下几类：

| 场景 | 触发条件 | 预期行为 | 降级标志 |
|------|---------|---------|---------|
| 联网搜索超时 | web_search > 5s | 跳过联网证据，返回 degraded=true | degraded=true, reason=web_search_timeout |
| 工具调用超时 | tool > 4s | 跳过该工具，返回 degraded=true | degraded=true, reason=tool_timeout |
| 工具调用失败 | Handler 抛异常 | 跳过该工具，返回 degraded=true | degraded=true, reason=tool_failed |
| 模型生成超时 | chat > 配置超时 | 返回已整理证据 + degraded=true | degraded=true, reason=model_timeout |
| 整体硬超时 | research > 15s | 返回部分成果 + degraded=true | degraded=true, reason=hard_timeout |

### 2.2 场景 1：联网搜索超时 → degraded=true

#### 测试目标

验证联网搜索超过 5s 时，研究流程不中断，返回降级结果。

#### Mock 示例代码

```python
"""降级测试：联网搜索超时。"""
import asyncio
import pytest


async def test_web_search_timeout_degraded(monkeypatch):
    """联网搜索超时时返回 degraded=true。"""
    from app.modules.research.web_research import WebResearchService

    async def _mock_web_search_timeout(*args, **kwargs):
        """模拟联网搜索超时。"""
        # 模拟 6 秒超时（超过 web_search_timeout_seconds=5）
        await asyncio.sleep(6)
        return {"items": []}

    # 替换真实 web_search 方法为超时 mock
    monkeypatch.setattr(
        WebResearchService,
        "search",
        _mock_web_search_timeout,
    )

    # 调用研究流程，验证返回 degraded=true
    # ...（具体调用方式取决于 ResearchService 的接口）
    # 断言：result.degraded is True
    # 断言：result.degraded_reason 含 "web_search" 或 "timeout"
```

### 2.3 场景 2：工具调用超时 → degraded=true

#### 测试目标

验证工具调用超过 4s 时，ToolExecutor 抛 `ExternalSourceTimeoutError`，
研究流程捕获后返回降级结果。

#### Mock 示例代码

```python
"""降级测试：工具调用超时。"""
import asyncio
import pytest


async def test_tool_timeout_degraded(monkeypatch):
    """工具调用超时时返回 degraded=true。"""
    from app.modules.tools.executor import ToolExecutor
    from app.core.exceptions import ExternalSourceTimeoutError

    # mock ToolDefinition 的 timeout_seconds 为极小值，快速触发超时
    # 或直接 mock handler.execute 抛 asyncio.TimeoutError
    async def _mock_handler_execute_timeout(inputs, config):
        """模拟工具 Handler 执行超时。"""
        await asyncio.sleep(5)  # 超过 4s 超时
        return {"data": []}

    # 替换 handler 执行为超时 mock
    # ToolExecutor.execute 内部会用 asyncio.wait_for 限制超时
    # 这里 mock handler 本身，让 asyncio.wait_for 触发 TimeoutError
    monkeypatch.setattr(
        "app.modules.tools.executor.get_tool_handler",
        lambda code: type("MockHandler", (), {"execute": _mock_handler_execute_timeout})(),
    )

    # 调用 ToolExecutor，验证抛 ExternalSourceTimeoutError
    # 研究流程捕获后应返回 degraded=true
    # 断言：exc_info.value.code == "EXTERNAL_SOURCE_TIMEOUT"
    # 断言：exc_info.value.http_status == 504
```

### 2.4 场景 3：模型生成超时 → 返回失败状态

#### 测试目标

验证聊天模型调用超时时，研究流程返回已整理证据 + 失败状态（degraded=true）。

#### Mock 示例代码

```python
"""降级测试：模型生成超时。"""
import asyncio
import pytest


async def test_model_timeout_degraded(monkeypatch):
    """模型生成超时时返回降级结果。"""
    from app.providers.chat_provider import get_chat_provider

    async def _mock_chat_timeout(messages, **kwargs):
        """模拟聊天模型调用超时。"""
        await asyncio.sleep(30)  # 远超模型超时
        return {"content": ""}

    # 替换 chat provider 为超时 mock
    mock_provider = type("MockProvider", (), {"chat": _mock_chat_timeout})()
    monkeypatch.setattr(
        "app.modules.research.research_service.get_chat_provider",
        lambda: mock_provider,
    )

    # 调用研究流程，验证：
    # 1. 返回已整理证据（检索 + 联网 + 工具的结果）
    # 2. degraded=true
    # 3. conclusion 为空或为降级提示
    # 4. error_code 含 "MODEL_TIMEOUT"
```

### 2.5 场景 4：整体硬超时 15s → 返回失败状态

#### 测试目标

验证研究流程整体超过 15s（`research_hard_timeout_seconds`）时，
返回部分成果 + 失败状态。

#### Mock 示例代码

```python
"""降级测试：整体硬超时。"""
import asyncio
import pytest


async def test_hard_timeout_degraded(monkeypatch):
    """整体硬超时时返回降级结果。"""
    from app.modules.research.research_service import ResearchService

    # mock 各路取证都耗时较长，总和超过 15s
    async def _mock_slow_search(*args, **kwargs):
        """模拟慢速检索（6s）。"""
        await asyncio.sleep(6)
        return []

    async def _mock_slow_web(*args, **kwargs):
        """模拟慢速联网（6s）。"""
        await asyncio.sleep(6)
        return []

    async def _mock_slow_tool(*args, **kwargs):
        """模拟慢速工具（6s）。"""
        await asyncio.sleep(6)
        return {"data": []}

    # 替换所有取证方法为慢速 mock
    # asyncio.gather 并行执行，但总耗时仍可能超过 15s
    # 研究流程应通过 asyncio.wait_for 整体限制 15s
    monkeypatch.setattr(ResearchService, "_retrieve", _mock_slow_search)
    monkeypatch.setattr(ResearchService, "_web_search", _mock_slow_web)

    # 调用研究流程，验证：
    # 1. 在 15s 内返回（不等所有取证完成）
    # 2. degraded=true
    # 3. degraded_reason 含 "hard_timeout"
    # 4. 返回部分已完成的证据
```

### 2.6 降级测试注意事项

1. **Mock 位置**
   Mock 应替换外部依赖的最底层（如 `httpx.AsyncClient`、`socket.getaddrinfo`），
   而非研究流程的高层方法，确保测试覆盖完整链路。

2. **超时阈值**
   测试中可适当调小超时阈值（如将 `web_search_timeout_seconds` 调为 0.5s），
   避免真实等待 5s，加快测试执行。

3. **断言重点**
   - `degraded` 字段为 `True`
   - `degraded_reason` 包含对应原因（web_search / tool / model / hard_timeout）
   - 已完成的取证结果正常返回（不被超时清空）
   - HTTP 状态码仍为 200（降级而非失败）

4. **并发安全**
   降级逻辑在 `asyncio.gather` 中执行，需确保单路失败不影响其他路。
   可通过 `return_exceptions=True` 捕获异常而非抛出。

---

## 三、业务接入测试

> 对应 SubTask 25.6：说明业务方如何通过 `/research/run` 端到端接入。

### 3.1 接入流程概述

业务方接入智能知识中台的完整流程：

1. **平台方创建项目**
   通过管理接口 `POST /api/v1/projects` 创建项目（如 ai-fund），
   由平台运维操作，需管理密钥。

2. **平台方生成 API Key**
   通过项目 API Key 管理接口为业务方生成 Key，明文 Key 仅展示一次。

3. **业务方配置知识库**
   通过 `POST /api/v1/knowledge-bases` 创建知识库，
   上传文档（`POST /api/v1/knowledge-bases/{code}/documents/file`）。

4. **业务方调用研究接口**
   通过 `POST /api/v1/research/run` 触发一次完整研究链路，
   获取检索 + 联网 + 工具 + 模型生成的综合结论。

5. **业务方查询任务状态（异步场景）**
   通过 `GET /api/v1/research/jobs/{jobId}` 查询异步任务状态与结果。

### 3.2 端到端接入示例（curl）

#### 步骤 1：调用 /research/run 短链路研究

```bash
# 短链路研究接口：同步返回结果，最长等待 15s
# knowledgeBaseIds 需替换为真实知识库 ID（通过 GET /api/v1/knowledge-bases 查询）
curl -X POST 'http://localhost:8000/api/v1/research/run' \
  -H 'Authorization: Bearer ikh_live_REPLACE_WITH_REAL_KEY' \
  -H 'Content-Type: application/json' \
  -H 'X-Project-Code: ai-fund' \
  -H 'X-Request-Id: req_20260730120000' \
  -d '{
    "question": "新能源基金近一年表现如何？",
    "outputType": "narrative",
    "strategy": "full",
    "knowledgeBaseIds": ["REPLACE_WITH_REAL_KB_ID"],
    "toolCodes": ["fund_market"],
    "toolInputs": {"fund_market": {"fund_codes": ["000001"]}}
  }'
```

#### 步骤 2：提交异步研究任务（长链路）

```bash
# 异步研究任务：立即返回 jobId，后续轮询结果
curl -X POST 'http://localhost:8000/api/v1/research/jobs' \
  -H 'Authorization: Bearer ikh_live_REPLACE_WITH_REAL_KEY' \
  -H 'Content-Type: application/json' \
  -H 'X-Project-Code: ai-fund' \
  -H 'Idempotency-Key: biz-fund-research-20260730' \
  -d '{
    "question": "分析沪深300指数近三年波动率与最大回撤",
    "outputType": "json",
    "strategy": "full",
    "knowledgeBaseIds": ["REPLACE_WITH_REAL_KB_ID"],
    "toolCodes": ["index_market"],
    "toolInputs": {"index_market": {"index_codes": ["000300"]}}
  }'
```

#### 步骤 3：查询异步任务状态

```bash
# 查询任务状态与结果
curl -X GET 'http://localhost:8000/api/v1/research/jobs/{jobId}' \
  -H 'Authorization: Bearer ikh_live_REPLACE_WITH_REAL_KEY' \
  -H 'X-Project-Code: ai-fund'
```

### 3.3 预期响应结构

#### /research/run 成功响应

```json
{
  "success": true,
  "requestId": "req_20260730120000",
  "data": {
    "taskId": "task-xxx",
    "requestId": "req_20260730120000",
    "answer": "近一年来新能源基金整体表现...",
    "conclusions": ["新能源基金 2025 年度收益率为 15.6%"],
    "suggestedActions": ["关注光伏与储能板块的回调机会"],
    "evidence": [
      {
        "type": "knowledge",
        "title": "新能源基金 2025 年度报告",
        "snippet": "新能源基金近一年净值增长...",
        "score": 0.92
      },
      {
        "type": "web",
        "title": "财经新闻 - 新能源板块分析",
        "snippet": "据财经新闻报道...",
        "sourceUrl": "https://eastmoney.com/...",
        "publishedAt": "2026-07-29T10:00:00Z"
      },
      {
        "type": "tool",
        "title": "fund_market 返回",
        "snippet": "fund_code=000001, nav=1.2345, return_ytd=0.156",
        "dataAsOf": "2026-07-30T00:00:00Z"
      }
    ],
    "confidence": 0.85,
    "uncertainties": ["市场短期波动可能影响净值表现"],
    "riskNotice": "基金投资有风险，过往业绩不预示未来表现",
    "timing": {
      "internalRetrievalMs": 320,
      "webSearchMs": 1200,
      "toolCallsMs": 800,
      "modelGenerationMs": 5800,
      "totalMs": 8234
    },
    "degraded": false,
    "degradedReasons": []
  },
  "meta": {
    "projectCode": "ai-fund",
    "apiVersion": "v1",
    "generatedAt": "2026-07-30T12:00:08.234Z"
  }
}
```

#### 降级响应（degraded=true）

```json
{
  "success": true,
  "requestId": "req_20260730120001",
  "data": {
    "taskId": "task-yyy",
    "requestId": "req_20260730120001",
    "answer": "基于内部知识库与工具数据的分析...",
    "conclusions": ["新能源基金近一年表现稳健"],
    "suggestedActions": [],
    "evidence": [
      {
        "type": "knowledge",
        "title": "...",
        "snippet": "...",
        "score": 0.88
      },
      {
        "type": "tool",
        "title": "fund_market 返回",
        "snippet": "...",
        "dataAsOf": "2026-07-30T00:00:00Z"
      }
    ],
    "confidence": 0.7,
    "uncertainties": ["联网搜索不可用，未获取最新市场动态"],
    "riskNotice": null,
    "timing": {
      "internalRetrievalMs": 300,
      "webSearchMs": 5000,
      "toolCallsMs": 800,
      "modelGenerationMs": 6200,
      "totalMs": 12345
    },
    "degraded": true,
    "degradedReasons": ["external_source_timeout"]
  },
  "meta": {
    "projectCode": "ai-fund",
    "apiVersion": "v1",
    "generatedAt": "2026-07-30T12:00:12.345Z"
  }
}
```

#### 错误响应（限流 429）

```json
{
  "success": false,
  "requestId": "req_20260730120002",
  "error": {
    "code": "RATE_LIMITED",
    "message": "请求过于频繁，请稍后重试",
    "retryable": true,
    "details": {
      "limit": 60,
      "window": "60s",
      "retryAfter": 12
    }
  }
}
```

响应头中包含限流信息：

```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1785417720
Retry-After: 12
```

### 3.4 业务接入验收 Checklist

业务方接入完成后，应通过以下 Checklist 验收：

- [ ] **鉴权**：API Key 通过 Authorization 头传入，响应 200
- [ ] **项目一致性**：X-Project-Code 与 Key 所属项目一致，响应 200
- [ ] **知识库隔离**：仅能查询本项目知识库，跨项目返回 404
- [ ] **检索接口**：`POST /retrieval/search` 返回相关文档片段
- [ ] **研究接口**：`POST /research/run` 返回 conclusion + evidence
- [ ] **降级标志**：模拟外部依赖超时，验证 degraded=true
- [ ] **限流**：连续调用 61 次，第 61 次返回 429
- [ ] **幂等性**：相同 Idempotency-Key 重放，返回原响应
- [ ] **异步任务**：`POST /research/jobs` 返回 jobId，可轮询状态
- [ ] **反馈**：`POST /research/{requestId}/feedback` 提交反馈成功

### 3.5 常见接入问题

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| 401 INVALID_API_KEY | Key 错误或已吊销 | 检查 Authorization 头格式与 Key 有效性 |
| 403 PROJECT_CODE_MISMATCH | X-Project-Code 与 Key 不一致 | 确认 X-Project-Code 与 Key 所属项目一致 |
| 403 SCOPE_NOT_ALLOWED | Key 缺少所需 Scope | 联系平台方补授 Scope（如 research:run） |
| 404 KNOWLEDGE_BASE_NOT_FOUND | 知识库 code 不存在或不属于本项目 | 检查 knowledgeBaseCodes 拼写与项目归属 |
| 429 RATE_LIMITED | 触发项目级限流 | 等待 Retry-After 秒后重试，或申请提升限流 |
| 504 EXTERNAL_SOURCE_TIMEOUT | 联网/工具/模型超时 | 检查外部依赖可用性，或降级处理 |

---

## 四、测试执行说明

### 4.1 运行环境要求

- **数据库**：PostgreSQL 16 + pgvector，端口 5433
- **Redis**：6.x+，用于限流与幂等
- **外部依赖**：模型 API / Web 搜索 API / 工具 API（降级测试可 mock）
- **Python**：3.12+

### 4.2 运行性能测试

```bash
cd backend

# 1. 启动应用服务
uvicorn app.main:app --host 0.0.0.0 --port 8000 &

# 2. seed 演示数据（生成 API Key）
python -m tests.seed_demo_projects

# 3. 替换 locustfile 中的 API Key
# 编辑 tests/locustfile_research.py，填入 seed 输出的明文 Key

# 4. 启动 locust 压测
locust -f tests/locustfile_research.py --host=http://localhost:8000

# 5. 在浏览器打开 http://localhost:8089 配置并发数开始压测
```

### 4.3 运行降级测试

降级测试需 mock 外部依赖，可通过 pytest 执行：

```bash
cd backend

# 运行所有降级相关测试（需自行编写完整测试用例）
pytest tests/ -k "degraded or timeout" -v

# 运行 SSRF 防护测试（纯 mock，不依赖网络）
pytest tests/test_ssrf_guard.py -v

# 运行端到端隔离测试（需数据库）
pytest tests/test_e2e_isolation.py -v

# 跳过所有数据库相关测试
SKIP_E2E_TESTS=1 SKIP_SCHEDULE_TESTS=1 pytest tests/ -v
```

### 4.4 跳过测试的环境变量

| 环境变量 | 说明 |
|---------|------|
| `SKIP_E2E_TESTS=1` | 跳过端到端隔离测试 |
| `SKIP_SCHEDULE_TESTS=1` | 跳过调度幂等测试 |
| `SKIP_ISOLATION_TESTS=1` | 跳过跨项目隔离单元测试 |
| `SKIP_RETRIEVAL_TESTS=1` | 跳过检索隔离测试 |
| `SKIP_TOOL_TESTS=1` | 跳过工具隔离测试 |
