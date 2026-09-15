# API 接入说明

完整机器契约见 `frontend/packages/contracts/openapi.json`，可在 `frontend` 目录执行 `pnpm contracts:export` 重新生成。

平台模型配置使用管理员会话访问：

- `GET /control/v1/platform/model-configuration`：读取 Provider、模型和密钥配置状态。
- `PUT /control/v1/platform/model-configuration`：更新模型配置；API Key 留空会保留已有值。
- `POST /control/v1/platform/model-configuration/test`：实际调用一次对话模型检查连接。

响应只返回 `chatApiKeyConfigured` 等布尔状态，不返回 API Key 明文。

## 模型配置中心

模型配置中心用于统一登记可用模型并按应用绑定，同样使用管理员会话访问：

- `GET /control/v1/model-catalog`：列出模型目录条目，可按 `capability`（chat/embedding/web_search）过滤。
- `POST /control/v1/model-catalog`：登记目录条目；`apiKeyEnv` 指向 `.env` 中的密钥变量名，不接收密钥明文。
- `PUT /control/v1/model-catalog/{code}`：更新条目；`DELETE /control/v1/model-catalog/{code}`：删除条目（仍被应用绑定时返回 409）。
- `POST /control/v1/model-catalog/{code}/test`：对条目发起一次真实最小调用，返回连通性与耗时。
- `GET /control/v1/applications/{applicationId}/model-binding`：读取应用按能力绑定的目录条目编码。
- `PUT /control/v1/applications/{applicationId}/model-binding`：全量更新绑定，字段传 `null` 表示解除绑定。
- `GET/PUT /control/v1/applications/{applicationId}/environments/{environmentId}/model-binding`：读取或全量更新指定环境覆盖，优先于应用默认绑定。
- `GET/PUT /control/v1/platform/model-defaults`：读取或更新平台级目录默认绑定，仅在应用和环境均未绑定时生效。

模型解析优先级：环境覆盖 → 应用默认 → 平台默认 → 旧平台模型配置兼容回退 → `.env` 默认值。目录条目的密钥仅从 `.env` 中 `apiKeyEnv` 指向的变量读取；回答、检索向量化、入库 Worker 和联网搜索都按应用环境走这条链。密钥只保存在 `.env`（约定 `MODEL_KEY_<用途大写名>`，如 `MODEL_KEY_ALIYUN_CHAT`），接口只返回 `apiKeyConfigured` 布尔状态，永不回显明文。

## 1. 两类 API

- 控制面 `/control/v1`：供管理控制台使用，登录后由 HttpOnly Cookie 鉴权。
- 运行面 `/runtime/v1`：供 AI 简历、AI 基金等业务后端调用，使用应用环境 API Key。

不要从浏览器前端直接调用运行面，也不要把 API Key 放入 `VITE_` 变量、localStorage 或前端代码。

## 2. AI 简历接入

先在控制台创建“AI 简历”应用，在开发环境创建知识集合、检索策略和回答策略，再签发至少包含 `answer:run` 的 API Key。

```http
POST /runtime/v1/answer
Authorization: Bearer aik_test_xxx
Content-Type: application/json

{
  "profile": "resume_job_match",
  "query": "这份简历与高级前端岗位是否匹配？",
  "inputs": {
    "resumeText": "本次请求中的临时简历内容",
    "jobDescription": "岗位说明"
  },
  "options": {
    "includeCitations": true,
    "includeEvidence": false
  }
}
```

`inputs` 是临时上下文，不会自动进入长期知识库。回答至少包含 `answerMode`、`answer`、`structuredOutput`、`warnings`、知识引用、联网引用、降级原因、Token 和耗时。

当知识未命中且策略允许模型兜底时，返回 `MODEL_ONLY`，同时 `warnings` 明确声明没有使用知识库；策略要求知识时返回 `INSUFFICIENT_EVIDENCE`。

## 3. 检索接口

```http
POST /runtime/v1/retrieve
Authorization: Bearer aik_test_xxx
Content-Type: application/json

{
  "profile": "resume_job_knowledge",
  "query": "高级前端需要哪些工程能力？",
  "topK": 8
}
```

需要 Scope `knowledge:read`。响应返回合并分数、向量分数、文本分数和可定位引用。

## 4. 用户反馈

```http
POST /runtime/v1/feedback
Authorization: Bearer aik_test_xxx
Content-Type: application/json

{
  "requestId": "上一次回答返回的请求 ID",
  "rating": -1,
  "reasonCode": "MISSING_EVIDENCE",
  "comment": "缺少岗位年限要求"
}
```

需要 Scope `feedback:write`，且只能反馈同一应用环境中的请求。

## 5. 管理员诊断与知识版本

管理员控制面可在应用环境范围内读取运行定位数据：

- `GET /control/v1/applications/{applicationId}/environments/{environmentId}/operations/traces`：按状态、错误码和最小耗时筛选回答轨迹。
- `GET /control/v1/applications/{applicationId}/environments/{environmentId}/operations/ingestion-attempts`：读取入库 Worker 每次尝试，可按 `runId`、`status` 筛选；仅返回请求 ID、阶段和脱敏错误摘要。
- `GET /control/v1/applications/{applicationId}/environments/{environmentId}/operations/feedback`：读取当前环境反馈并回跳请求轨迹。
- `POST /control/v1/applications/{applicationId}/environments/{environmentId}/documents/{documentId}/versions/{revisionId}/rollback`：将活动知识版本切换到已完成且具备向量片段的版本；历史版本和对象不会删除。

## 6. 异步写入幂等

以下控制面异步写接口支持可选 `Idempotency-Key` 请求头：文档上传、文本/远程资料创建、入库重试、文档文本/文件新版本和远程刷新。

```http
POST /control/v1/applications/{applicationId}/environments/{environmentId}/collections/{collectionId}/documents/text
Idempotency-Key: resume-import-20260914-001
Content-Type: application/json
```

同一应用、环境、操作和请求内容使用相同键时，服务端回放第一次成功响应；相同键提交不同内容返回冲突；第一次请求尚在处理时不会重复创建文档、版本或 Outbox。已完成结果保留 24 小时。未携带该请求头的旧调用继续兼容，但建议所有可重试的异步写入都生成稳定键。

## 7. 标准成功结构

```json
{
  "success": true,
  "requestId": "req_...",
  "data": {},
  "meta": {}
}
```

## 8. 标准错误结构

```json
{
  "success": false,
  "requestId": "req_...",
  "error": {
    "code": "PROVIDER_UNAVAILABLE",
    "title": "AI 服务暂不可用",
    "message": "Chat Provider 调用失败",
    "retryable": true,
    "suggestion": "请稍后重试",
    "details": {}
  }
}
```

调用方应记录 `requestId`，只对 `retryable=true` 的错误执行有上限的退避重试。不要把 `details` 直接展示给不受信任的最终用户。

## 9. Scope

| Scope | 能力 |
| --- | --- |
| `knowledge:read` | 调用检索 |
| `answer:run` | 调用回答 |
| `feedback:write` | 提交回答反馈 |
| `ingestion:write` | 为后续运行面入库预留 |
