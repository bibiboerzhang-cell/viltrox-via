# V-KPI backend — uvicorn app.main:app on 8102
# 最小化镜像:固定 python 基线 + requirements.txt 锁版本依赖。
# build context 为仓库根(见 docker-compose.yml: context: .)。
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/backend

WORKDIR /app

# 系统依赖:psycopg / 部分轮子构建可能需要 build 基础件;libpq 供 psycopg 运行期使用。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖(利用 layer cache);requirements.txt 在仓库根。
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /app/requirements.txt

# 再拷贝代码(backend 子树足以跑 app.main:app;alembic/migrations 按需挂载或另装)
COPY backend /app/backend

EXPOSE 8102

# uvicorn 以 backend 为 app-dir,匹配本地 PYTHONPATH=backend + app.main:app 口径
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8102", "--app-dir", "/app/backend"]
