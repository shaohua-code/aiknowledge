# 前端工程

本目录只包含管理控制台及其共享前端包，不包含后端代码。

## CMD 启动

```bat
cd /d D:\project\aiknowledge\frontend
pnpm install
pnpm dev
```

浏览器打开 `http://localhost:5173`。开发服务器会把 `/control`、`/runtime`、`/health` 和 `/ready` 请求代理到 `http://localhost:8000`，因此请先确保后端已经启动。

## 常用命令

```bat
REM 类型检查并构建
pnpm build

REM 代码检查
pnpm lint

REM 前端测试
pnpm test
```

