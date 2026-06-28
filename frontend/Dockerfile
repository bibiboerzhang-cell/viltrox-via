# V-KPI frontend — vite dev server on 5173
# 最小化:node 基线 + npm ci 锁 package-lock。dev 模式跑(热更新+代理后端)。
# build context 为仓库根(见 docker-compose.yml: context: .)。
FROM node:20-slim

ENV NODE_ENV=development

WORKDIR /app/frontend

# 先装依赖(利用 layer cache)。package-lock.json 存在 → 用 npm ci 复现锁版本。
COPY frontend/package.json frontend/package-lock.json* /app/frontend/
RUN npm ci

# 拷贝前端源码
COPY frontend /app/frontend

EXPOSE 5173

# vite dev,对外暴露 0.0.0.0:5173;API 代理目标由 VITE_API_PROXY_TARGET 注入
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
