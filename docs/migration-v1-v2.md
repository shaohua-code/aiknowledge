# V1 到 V2 迁移与回滚

V2 使用新数据库 `aiknowledge_v2`。迁移工具不会修改 V1 数据库，也不会复制旧 API Key、运行日志、明文配置或旧模型凭证。

## 1. 迁移映射

| V1 | V2 |
| --- | --- |
| `projects` | `applications` |
| 项目默认数据 | `development` 环境 |
| `knowledge_bases` | `knowledge_collections` |
| `documents` | `sources` + `documents` + `document_revisions` |
| `document_chunks` | `document_chunks`，不复制旧向量 |
| `api_keys` | 不迁移，必须重新签发 |
| 项目模型配置 | 不迁移，必须按环境重新配置 |
| 旧运行、研究、抓取日志 | 不迁移 |

向量不直接复制，原因是 V2 固定 1536 维并要求重新验证 Embedding Provider。迁移后文本检索可用，生产切流前必须重建向量并完成质量评测。

## 2. 前置条件

1. 备份 V1 PostgreSQL 和原始对象存储。
2. 创建独立 V2 数据库并执行 `alembic upgrade head`。
3. 确认 V1 在迁移窗口内只读。
4. 分别准备只读 V1 连接串和 V2 写入连接串，禁止写入仓库。

## 3. 只读审计

```powershell
$env:V1_DATABASE_URL = "postgresql+asyncpg://readonly:***@host/v1"
services\api\.venv\Scripts\python.exe scripts\migrate_v1_to_v2.py
```

默认只输出项目、知识集合、文档和有效片段数量，不连接 V2，也不写入任何数据。

## 4. 执行迁移

```powershell
$env:V1_DATABASE_URL = "postgresql+asyncpg://readonly:***@host/v1"
$env:DATABASE_URL = "postgresql+asyncpg://writer:***@host/aiknowledge_v2"
services\api\.venv\Scripts\python.exe scripts\migrate_v1_to_v2.py --execute
```

脚本使用确定性 ID 和 `ON CONFLICT DO NOTHING`，可在验证失败后安全重跑。执行后应重新签发 API Key、创建检索/回答策略，并重新生成 Embedding。

## 5. 验证

- V1/V2 应用、集合、文档和片段数量逐项核对。
- 抽样核对文档标题、内容、页码和应用归属。
- 运行跨应用、跨环境隔离测试。
- 为 AI 简历执行固定问题集，核对回答模式与引用。
- 检查 `MODEL_ONLY` 声明和 `INSUFFICIENT_EVIDENCE` 拒答。
- 验证上传、重试、归档、反馈和运行轨迹。

## 6. 切流与回滚

先让 AI 简历测试环境调用 V2，再进行影子请求对比；通过后更换生产服务端 API Key 和地址。不要让浏览器持有底座密钥。

出现跨应用数据、知识完整率不足、引用丢失、模型兜底未声明、错误率或延迟超标时立即回滚：业务调用地址切回 V1，V2 保留现场只读排查。由于 V1 数据库未被迁移工具修改，回滚不需要执行向下数据库迁移。
