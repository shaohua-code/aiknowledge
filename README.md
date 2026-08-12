# AI 知识能力底座

面向 AI 简历、AI 基金及后续 AI 应用的统一知识、检索、回答和运行治理平台。

当前需求基线为 [docs/PRD.md](docs/PRD.md)。V2 采用控制面与运行面分离的模块化单体架构：

- `apps/console`：React 管理控制台。
- `services/api`：FastAPI 控制面、运行面与领域服务。
- `services/worker`：Celery Worker/Beat 运行入口。
- `packages/contracts`：共享 API 契约与 TypeScript 类型。
- `packages/ui`：控制台共享 UI 组件。
- `infrastructure`：容器、数据库和反向代理配置。

## 本地启动

1. 复制根目录 `.env.example` 为 `.env`，替换所有开发占位值。
2. 启动依赖与服务：

   ```powershell
   docker compose up -d --build
   ```

3. 打开管理控制台：`http://localhost:5173`。
4. API 文档：`http://localhost:8000/docs`。

## 本地开发

```powershell
pnpm install
pnpm dev
```

后端命令在 `services/api` 运行：

```powershell
python -m uvicorn knowledge_core.main:app --reload
pytest
```

## 安全说明

- 仓库不包含可直接使用的数据库密码、管理员密码、API Key 或模型密钥。
- 应用 API Key 明文只在创建时返回一次，数据库仅保存 Argon2id 哈希。
- 控制台使用 HttpOnly 管理会话，不在浏览器本地存储管理密钥。
- 业务数据以应用和环境作为强隔离边界。

