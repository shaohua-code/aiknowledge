# API 接入说明

完整机器契约见 `frontend/packages/contracts/openapi.json`，可在 `frontend` 目录执行 `pnpm contracts:export` 重新生成。

平台模型配置使用管理员会话访问：

- `GET /control/v1/platform/model-configuration`：读取 Provider、模型和密钥配置状态。
- `PUT /control/v1/platform/model-configuration`：更新模型配置；API Key 留空会保留已有值。
- `POST /control/v1/platform/model-configuration/test`：实际调用一次对话模型检查连接。

响应只返回 `chatApiKeyConfigured` 等布尔状态，不返回 API Key 明文。

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

## 5. 标准成功结构

```json
{
  "success": true,
  "requestId": "req_...",
  "data": {},
  "meta": {}
}
```

## 6. 标准错误结构

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

## 7. Scope

| Scope | 能力 |
| --- | --- |
| `knowledge:read` | 调用检索 |
| `answer:run` | 调用回答 |
| `feedback:write` | 提交回答反馈 |
| `ingestion:write` | 为后续运行面入库预留 |
