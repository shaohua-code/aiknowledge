# =============================================================================
# 智能知识中台 - 前端镜像（多阶段构建）
# 阶段 1：node:20-alpine 构建静态资源
# 阶段 2：nginx:stable-alpine 运行静态文件服务
# 多阶段构建目的：最终镜像仅含 nginx + dist，不含 node_modules / 源码，
# 镜像体积从 ~1GB 降至 ~30MB，且避免源码泄露到生产环境
# =============================================================================

# -----------------------------------------------------------------------------
# 阶段 1：构建阶段（builder）
# 使用 node:20-alpine：Alpine Linux 精简版，体积仅 ~50MB
# -----------------------------------------------------------------------------
FROM node:20-alpine AS builder

# 维护者标签
LABEL maintainer="Knowledge Hub Team"
LABEL stage="builder"

# 设置工作目录
WORKDIR /build

# 先复制 package.json 与 lock 文件，利用 Docker 层缓存加速依赖安装
# 为什么先复制这两个文件？
#   npm ci 基于 package-lock.json 安装，要求两者版本一致。
#   单独复制可在源码改动时复用依赖层缓存，避免重复 npm ci（耗时较长）。
COPY package.json package-lock.json* ./

# 安装依赖（npm ci 比 npm install 更严格，按 lock 文件精确安装）
# --no-audit --no-fund：跳过安全审计与资助提示，加速安装
RUN npm ci --no-audit --no-fund

# 复制全部源码
COPY . .

# 构建生产产物：执行 tsc 类型检查 + vite build 打包
# 产物输出到 /build/dist 目录（由 vite.config.ts 的 outDir: 'dist' 决定）
# 构建时通过 VITE_* 环境变量注入配置（如 VITE_API_BASE_URL）
# 此处使用默认值 /api，生产部署时可通过 --build-arg 或 docker-compose 环境变量覆盖
ARG VITE_API_BASE_URL=/api
ARG VITE_APP_TITLE=智能知识中台
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_APP_TITLE=$VITE_APP_TITLE

RUN npm run build

# -----------------------------------------------------------------------------
# 阶段 2：运行阶段（runner）
# 使用 nginx:stable-alpine：稳定版 nginx + Alpine，体积 ~8MB
# -----------------------------------------------------------------------------
FROM nginx:stable-alpine AS runner

# 维护者标签
LABEL maintainer="Knowledge Hub Team"
LABEL description="智能知识中台前端 Nginx 静态服务"

# 将前端构建产物复制到 nginx 默认静态文件目录
COPY --from=builder /build/dist /usr/share/nginx/html

# 注意：nginx 配置由 docker-compose.prod.yml 通过 volumes 挂载提供
# 挂载路径：./docker/nginx/nginx.conf -> /etc/nginx/conf.d/default.conf
# 此处不复制 nginx.conf，因为构建上下文（frontend/）无法访问上级目录

# 暴露 80 端口（HTTP）
EXPOSE 80

# 健康检查：每 30 秒访问首页
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost/ || exit 1

# nginx 默认前台运行（daemon off），容器不会立即退出
CMD ["nginx", "-g", "daemon off;"]
