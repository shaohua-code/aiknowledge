FROM node:22-alpine AS build
WORKDIR /workspace
RUN corepack enable
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/console apps/console
COPY packages/contracts packages/contracts
COPY packages/ui packages/ui
# 必须在复制完整工作区后安装：否则后续 COPY 会覆盖 pnpm 的 workspace 链接，
# 容器内执行 tsc 时会找不到 console 的 TypeScript 二进制文件。
RUN pnpm install --frozen-lockfile
RUN pnpm --filter @aik/console build

FROM nginx:1.27-alpine
COPY infrastructure/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /workspace/apps/console/dist /usr/share/nginx/html
EXPOSE 80
