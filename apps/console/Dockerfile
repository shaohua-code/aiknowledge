FROM node:22-alpine AS build
WORKDIR /workspace
RUN corepack enable
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/console/package.json apps/console/package.json
COPY packages/contracts/package.json packages/contracts/package.json
COPY packages/ui/package.json packages/ui/package.json
RUN pnpm install --frozen-lockfile
COPY apps/console apps/console
COPY packages/contracts packages/contracts
COPY packages/ui packages/ui
RUN pnpm --filter @aik/console build

FROM nginx:1.27-alpine
COPY infrastructure/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /workspace/apps/console/dist /usr/share/nginx/html
EXPOSE 80
