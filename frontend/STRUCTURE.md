# 智能知识中台 - 前端结构说明

## 技术栈总览

| 分类 | 技术 | 版本 | 用途 |
| --- | --- | --- | --- |
| 框架 | React | 19 | UI 视图层 |
| 语言 | TypeScript | 5.6 | 类型安全 |
| 构建工具 | Vite | 5.4 | 开发服务器 / 打包 |
| UI 组件库 | Ant Design | 5.21 | 企业级组件 |
| 原子样式 | Tailwind CSS | 3.4 | 工具类样式 |
| 路由 | React Router DOM | 6.27 | 客户端路由 |
| 状态管理 | Zustand | 4.5 | 轻量全局状态 |
| 数据请求 | TanStack Query | 5.59 | 服务端状态缓存 |
| HTTP 客户端 | Axios | 1.7 | 请求与拦截器 |
| 日期库 | Day.js | 1.11 | 时间格式化 |
| 代码规范 | ESLint + Prettier | - | 代码风格 |
| 容器化 | Docker | - | 镜像构建 |

---

## 目录结构

### `src/api/` — API 请求层

- **作用**：封装所有与后端对接的 HTTP 接口，统一通过 axios 实例发起请求。
- **对应技术**：Axios、TypeScript、Ant Design（`message` 错误提示）。
- **关键文件**：
  - `request.ts` — 创建 axios 实例，配置 `baseURL=/api`、超时、请求/响应拦截器；实现双 Token 鉴权与 `X-Project-Code` 注入；剥离 `ApiResponse` 外层结构。
  - `projects.ts` — 项目管理（管理密钥保护接口）。
  - `knowledge-bases.ts` — 知识库 CRUD。
  - `documents.ts` — 文档上传与管理。
  - `research.ts` — 智能研究台对话/检索。
  - `retrieval.ts` — 检索测试。
  - `schedules.ts` — 定时任务。
  - `crawl-sources.ts` — 采集源与采集记录。
  - `tools.ts` — 工具配置。
  - `prompts.ts` — 提示词管理。
  - `api-keys.ts` — 项目 API Key 管理。
  - `execution-logs.ts` — 执行记录查询。
  - `project-settings.ts` — 项目设置。
  - `stats.ts` — 概览统计数据。

### `src/components/` — 全局通用组件

- **作用**：跨页面复用的通用组件，不绑定具体业务页面。
- **对应技术**：React 19、TypeScript、Ant Design。
- **关键文件**：
  - `ApiKeySetup.tsx` — 管理密钥初始化配置组件。
  - `ProjectApiKeyInputModal.tsx` — 项目 API Key 录入弹窗。

### `src/hooks/` — 全局通用 Hooks

- **作用**：存放跨页面复用的自定义 Hooks（与具体页面无关的逻辑）。
- **对应技术**：React Hooks、TanStack Query。

### `src/layouts/` — 布局组件

- **作用**：承载路由嵌套的骨架页面，提供导航与内容容器。
- **对应技术**：React Router DOM（`<Outlet />`）、Ant Design Layout/Menu。
- **关键文件**：
  - `MainLayout.tsx` — 主布局，顶部全局导航；作为 `/` 路由根布局，承载全局概览与项目列表。
  - `ProjectLayout.tsx` — 项目内布局，左侧菜单树；作为 `/projects/:projectId` 二级布局，承载项目内所有子页面。

### `src/pages/` — 页面目录

- **作用**：按业务模块拆分的页面集合，共 18 个业务模块。
- **对应技术**：React 19、TypeScript、Ant Design、Tailwind CSS、TanStack Query。
- **页面清单**：
  - `projects/` — 项目列表与创建（管理密钥保护）。
  - `overview/` — 全局概览（聚合多项目统计）。
  - `project-overview/` — 项目概览（项目内首页）。
  - `knowledge-bases/` — 知识库管理。
  - `documents/` — 文档管理。
  - `research/` — 智能研究台。
  - `retrieval-test/` — 检索测试。
  - `schedules/` — 定时任务。
  - `schedule-runs/` — 定时运行记录。
  - `crawl-sources/` — 采集源。
  - `crawl-runs/` — 采集记录。
  - `web-materials/` — 网络资料池。
  - `tools/` — 工具配置。
  - `prompts/` — 提示词管理。
  - `api-keys/` — API Key 管理。
  - `execution-logs/` — 执行记录。
  - `project-settings/` — 项目设置。

### `src/router/` — 路由配置

- **作用**：集中声明应用路由树，定义布局嵌套关系。
- **对应技术**：React Router DOM 6（`createBrowserRouter`）。
- **关键文件**：
  - `index.tsx` — 路由配置入口；`/` 使用 `MainLayout`，默认重定向到 `/overview`；`/projects/:projectId` 使用 `ProjectLayout`，默认重定向到项目概览。

### `src/stores/` — 全局状态

- **作用**：保存跨组件共享的全局状态。
- **对应技术**：Zustand、localStorage（持久化）。
- **关键文件**：
  - `project.ts` — 当前项目上下文 store；保存 `currentProject` 与 `apiKey`，刷新页面后从 `localStorage` 自动恢复；提供 `setCurrentProject` / `clearCurrentProject` 与 `useCurrentProject` selector。

### `src/types/` — 类型定义

- **作用**：集中存放与后端契约对应的 TypeScript 类型。
- **对应技术**：TypeScript。
- **关键文件**：
  - `api.ts` — 统一响应结构 `ApiResponse<T>`、各业务实体类型定义。

### `src/utils/` — 工具函数

- **作用**：存放纯函数工具方法（格式化、转换、常量等）。
- **对应技术**：TypeScript、Day.js。

### `src/` 根文件

- `App.tsx` — 应用根组件，挂载 `RouterProvider` 与 `QueryClientProvider`、Ant Design `ConfigProvider`。
- `main.tsx` — 应用入口，挂载到 `#root`。
- `index.css` — 全局样式入口（Tailwind 指令 + Ant Design 主题覆盖）。
- `vite-env.d.ts` — Vite 环境类型声明。

### `public/` — 静态资源

- **作用**：存放不经过打包处理的静态资源（如 `favicon`、图标）。
- **对应技术**：Vite 静态资源机制。

### 工程配置文件

- `.eslintrc.cjs` — ESLint 规则（含 React Hooks、React Refresh 插件）。
- `.prettierrc` — Prettier 格式化规则。
- `.gitignore` — Git 忽略清单。
- `.dockerignore` — Docker 构建忽略清单。
- `Dockerfile` — 前端镜像构建脚本。
- `index.html` — HTML 入口模板。
- `package.json` — 依赖与脚本声明。
- `postcss.config.js` — PostCSS 配置（Tailwind / Autoprefixer）。
- `tailwind.config.js` — Tailwind 主题、断点、插件配置。
- `tsconfig.json` / `tsconfig.app.json` / `tsconfig.node.json` — TypeScript 编译配置（应用 / Node 分离）。
- `vite.config.ts` — Vite 配置（别名 `@`、代理、插件）。

---

## 补充说明

### 页面三层结构模式

每个业务页面统一采用三层结构，职责清晰、便于维护：

```
pages/<module>/
├── index.tsx        # 页面入口组件：组装布局、接入路由、协调子组件
├── components/      # 页面内组件：CreateModal、StatusTag、DetailDrawer 等局部 UI
└── hooks/           # 页面专属 hooks：useXxx.ts 封装 TanStack Query 的查询/变更逻辑
```

- `index.tsx` 负责页面级编排，调用 hooks 获取数据，将数据下发给 `components/`。
- `components/` 只负责视图呈现与交互，不直接发起请求。
- `hooks/useXxx.ts` 集中管理 TanStack Query 的 `useQuery` / `useMutation`，封装缓存键与请求函数，便于复用与测试。

### 双 Token 鉴权机制

系统采用「管理密钥 + 项目 API Key」双 Token 鉴权，由 `src/api/request.ts` 拦截器自动注入：

1. **管理密钥（management_api_key）**
   - 存储于 `localStorage` 键 `management_api_key`。
   - 用于项目管理类接口（路径包含 `/v1/projects`）。
   - 拦截器以 `Authorization: Bearer <managementKey>` 注入。

2. **项目 API Key（current_api_key）**
   - 存储于 `localStorage` 键 `current_api_key`，由 `stores/project.ts` 在切换项目时写入。
   - 用于项目内业务接口（知识库、文档、检索等）。
   - 拦截器以 `Authorization: Bearer <apiKey>` 注入。

3. **项目编码头 `X-Project-Code`**
   - 从 `localStorage` 的 `current_project` 中读取 `code` 字段。
   - 业务接口请求自动附加 `X-Project-Code: <projectCode>`，后端据此路由到对应项目数据空间。

4. **拦截器分流逻辑**
   - 请求 URL 含 `/v1/projects` → 走管理密钥。
   - 其余请求 → 走项目 API Key + `X-Project-Code`。

5. **响应处理**
   - 统一剥离 `ApiResponse` 外层，依据 `success` 字段判断成败。
   - 失败时通过 Ant Design `message.error` 全局提示。

### 路由结构

路由采用两级嵌套布局：

```
/ （MainLayout 主布局，顶部导航）
├── index           → 重定向到 /overview
├── overview        → 全局概览
├── projects        → 项目列表（管理密钥保护）
└── projects/:projectId  （ProjectLayout 项目内布局，左侧菜单）
    ├── index       → 重定向到 overview
    ├── overview              → 项目概览
    ├── knowledge-bases       → 知识库管理
    ├── research              → 智能研究台
    ├── documents             → 文档管理
    ├── retrieval-test        → 检索测试
    ├── schedules             → 定时任务
    ├── schedule-runs         → 运行记录
    ├── crawl-sources         → 采集源
    ├── crawl-runs            → 采集记录
    ├── web-materials         → 网络资料池
    ├── tools                 → 工具配置
    ├── prompts               → 提示词管理
    ├── api-keys              → API Key 管理
    ├── execution-logs        → 执行记录
    └── project-settings      → 项目设置
```

- 主布局 `/` 默认进入全局概览，与项目列表同级。
- 进入具体项目 `/projects/:projectId` 后切换为项目内布局，默认跳转项目概览作为首页。
