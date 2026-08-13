# AI 知识能力底座

面向 AI 简历、AI 基金及后续 AI 应用的统一知识、检索、回答和运行治理平台。

当前需求基线为 [docs/PRD.md](docs/PRD.md)。前后端代码已经分离：

- `frontend/`：React 管理控制台、共享 UI、TypeScript 契约和 Nginx 配置。
- `backend/`：FastAPI、Celery Worker、数据库迁移、PostgreSQL 初始化和后端脚本。
- `docker-compose.yml`：在根目录统一启动完整项目。

独立开发说明见 [前端 README](frontend/README.md) 和 [后端 README](backend/README.md)。

管理员登录后可在“设置 → 模型与联网能力”配置对话模型、Embedding 模型和 Serper。密钥只接受写入，接口不会返回明文；未在页面配置时继续使用 `.env` 中的默认值。

项目当前结构、已完成能力与后续建设建议见 [项目梳理](docs/项目梳理.md)。互联网数据源的支持格式、兼容策略和错误码见 [互联网数据接入说明](docs/互联网数据接入.md)。

## 本地启动

1. 复制根目录 `.env.example` 为 `.env`，替换所有开发占位值。
2. 启动依赖与服务：

   ```powershell
   docker compose up -d --build
   ```

3. 打开管理控制台：`http://localhost:5173`。
4. API 文档：`http://localhost:8000/docs`。

## 本地开发

```bat
cd /d D:\project\aiknowledge\frontend
pnpm install
pnpm dev
```

后端命令在 `backend\services\api` 运行：

```bat
cd /d D:\project\aiknowledge\backend\services\api
.venv\Scripts\activate
python -m uvicorn knowledge_core.main:app --reload
pytest
```

## 安全说明

- 仓库不包含可直接使用的数据库密码、管理员密码、API Key 或模型密钥。
- 应用 API Key 明文只在创建时返回一次，数据库仅保存 Argon2id 哈希。
- 控制台使用 HttpOnly 管理会话，不在浏览器本地存储管理密钥。
- 业务数据以应用和环境作为强隔离边界。
