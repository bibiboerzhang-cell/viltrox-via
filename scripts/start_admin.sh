#!/usr/bin/env bash
# R58E: 启动校验 + 默认 ENVIRONMENT=local + 启动时打印
#
# 防止"server 用错库 + smoke 用对库"的错配 (RBAC 反复 403 的根因)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# systemd's EnvironmentFile overrides Environment= declarations.  Capture this
# marker before runtime_env.sh loads any file so a stale production .env cannot
# silently turn the reviewed two-process/single-scheduler contract back off.
SYSTEMD_ADMIN_WEB_CONTRACT="${VKPI_SYSTEMD_ADMIN_WEB_CONTRACT:-0}"
SYSTEMD_DATABASE_URL="${DATABASE_URL:-}"
SYSTEMD_REDIS_URL="${REDIS_URL:-}"
if [[ "$SYSTEMD_ADMIN_WEB_CONTRACT" == "1" ]]; then
  export ENVIRONMENT=production
  export RUNTIME_ENV_QUIET=1
fi
source "$ROOT/scripts/runtime_env.sh"

if [[ "$SYSTEMD_ADMIN_WEB_CONTRACT" == "1" ]]; then
  # Restore the database endpoints supplied by systemd's reviewed EnvironmentFile
  # and force only process-model/safety values.  Provider and business settings
  # remain owned by the production environment file.
  export DATABASE_URL="$SYSTEMD_DATABASE_URL"
  export REDIS_URL="$SYSTEMD_REDIS_URL"
  export APP_ROLE=admin-web
  export ENVIRONMENT=production
  export DB_RUNTIME_BACKEND=postgres
  export LOCAL_RUNTIME_FORCE_STACK=0
  export HOST=127.0.0.1
  export PORT=8001
  export WEB_CONCURRENCY=2
  export ADMIN_DAEMON=0
  export ENABLE_SCHEDULER=1
  export ENABLE_BROWSER=0
  export ENABLE_UPLOAD_CLEANUP=0
  # Bound the two-process web fleet even when a stale shared .env still carries
  # the historical 64-connection default.  Together with seven 16-connection
  # Apify workers and one 16-connection Redis worker this caps the reviewed
  # cloud topology at 160 connections, below PostgreSQL max_connections=200.
  export POSTGRES_POOL_MIN_SIZE=2
  export POSTGRES_POOL_MAX_SIZE=16
  export POSTGRES_POOL_TIMEOUT_SEC=30
  export RUNTIME_ENV_QUIET=1
fi

if [[ -z "${PYTHON_BIN:-}" && -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT/.venv/bin/python"
fi
PYTHON_BIN="${PYTHON_BIN:-python3}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8102}"
WEB_CONCURRENCY="${WEB_CONCURRENCY:-2}"
WORKER_CONNECTIONS="${WORKER_CONNECTIONS:-2000}"
BACKLOG="${BACKLOG:-4096}"
ADMIN_DAEMON="${ADMIN_DAEMON:-1}"

cd "$ROOT"

# ──────────────────────────────────────────────────────────
# R58E CHANGE: 默认改为 local (而非 production)
# 历史行为: ENVIRONMENT=${ENVIRONMENT:-production}  ← 危险默认值
# 新行为:   ENVIRONMENT=${ENVIRONMENT:-local}      ← 安全默认值
# 生产环境通过 docker/k8s 显式 export ENVIRONMENT=production
# ──────────────────────────────────────────────────────────
export APP_ROLE="${APP_ROLE:-admin-web}"
export ENVIRONMENT="${ENVIRONMENT:-local}"
export DB_RUNTIME_BACKEND="${DB_RUNTIME_BACKEND:-postgres}"
export ENABLE_SCHEDULER="${ENABLE_SCHEDULER:-0}"
export ENABLE_BROWSER="${ENABLE_BROWSER:-0}"
export ENABLE_UPLOAD_CLEANUP="${ENABLE_UPLOAD_CLEANUP:-0}"

if [[ -z "${APP_GIT_SHA:-}" ]]; then
  if [[ -f "$ROOT/BUILD_GIT_SHA" ]]; then
    export APP_GIT_SHA="$(tr -d '[:space:]' < "$ROOT/BUILD_GIT_SHA")"
  elif command -v git >/dev/null 2>&1 && git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    export APP_GIT_SHA="$(git -C "$ROOT" rev-parse HEAD)"
  fi
fi
if [[ -z "${APP_BUILD_TIME:-}" ]]; then
  if [[ -f "$ROOT/BUILD_TIME" ]]; then
    export APP_BUILD_TIME="$(tr -d '[:space:]' < "$ROOT/BUILD_TIME")"
  else
    export APP_BUILD_TIME="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  fi
fi

# ──────────────────────────────────────────────────────────
# R58E NEW: LOCAL_RUNTIME_FORCE_STACK 默认开启
# 历史行为: 默认 0,需要手动 export LOCAL_RUNTIME_FORCE_STACK=1
# 新行为:   默认 1,本地环境强制 LOCAL stack
# ──────────────────────────────────────────────────────────
LOCAL_RUNTIME_FORCE_STACK="${LOCAL_RUNTIME_FORCE_STACK:-1}"

if [[ "$ENVIRONMENT" == "local" && "$LOCAL_RUNTIME_FORCE_STACK" == "1" ]]; then
  export DATABASE_URL="$LOCAL_DATABASE_URL"
  export REDIS_URL="$LOCAL_REDIS_URL"
else
  export DATABASE_URL="${DATABASE_URL:-$LOCAL_DATABASE_URL}"
  export REDIS_URL="${REDIS_URL:-$LOCAL_REDIS_URL}"
fi

export WORKERS="$WEB_CONCURRENCY"
export WORKER_CONNECTIONS
export BACKLOG
export BIND="$HOST:$PORT"

# Never print connection strings. Production URLs commonly contain database
# or Redis credentials and startup stderr is collected by systemd/gunicorn.
DATABASE_URL_STATE="$([[ -n "${DATABASE_URL:-}" ]] && printf 'configured' || printf 'missing')"
REDIS_URL_STATE="$([[ -n "${REDIS_URL:-}" ]] && printf 'configured' || printf 'missing')"

# ──────────────────────────────────────────────────────────
# R58E NEW: 启动前一致性检查
# 防止 ENVIRONMENT=local 但 DATABASE_URL 不指向 LOCAL stack
# ──────────────────────────────────────────────────────────
if [[ "$ENVIRONMENT" == "local" ]]; then
  if [[ "$DATABASE_URL" != "$LOCAL_DATABASE_URL" ]]; then
    echo "═══════════════════════════════════════════════════════" >&2
    echo "❌ start_admin.sh: 配置不一致拒绝启动" >&2
    echo "─────────────────────────────────────────" >&2
    echo "  ENVIRONMENT       = $ENVIRONMENT" >&2
    echo "  DATABASE_URL       = configured but differs from LOCAL_DATABASE_URL" >&2
    echo "  LOCAL_DATABASE_URL = configured" >&2
    echo "─────────────────────────────────────────" >&2
    echo "  本地环境必须用 LOCAL stack" >&2
    echo "  如确需自定义,显式: export LOCAL_RUNTIME_FORCE_STACK=0" >&2
    echo "═══════════════════════════════════════════════════════" >&2
    exit 1
  fi
fi

# ──────────────────────────────────────────────────────────
# R58E NEW: 启动横幅 - 让任何 reader 立刻看到 server 真实环境
# ──────────────────────────────────────────────────────────
cat >&2 <<EOF
═══════════════════════════════════════════════════════
  V-KPI Admin Server Starting
─────────────────────────────────────────────────────
  ENVIRONMENT          = $ENVIRONMENT
  APP_ROLE             = $APP_ROLE
  BIND                 = $BIND
  WORKERS              = $WORKERS
  DATABASE_URL         = $DATABASE_URL_STATE
  REDIS_URL            = $REDIS_URL_STATE
  DB_RUNTIME_BACKEND   = $DB_RUNTIME_BACKEND
  APP_GIT_SHA          = ${APP_GIT_SHA:-unknown}
  APP_BUILD_TIME       = ${APP_BUILD_TIME:-unknown}
  LOCAL_FORCE_STACK    = $LOCAL_RUNTIME_FORCE_STACK
  ADMIN_DAEMON         = $ADMIN_DAEMON
═══════════════════════════════════════════════════════
EOF

GUNICORN_ARGS=(
  -m gunicorn app.main:app
  -c "$ROOT/deploy/gunicorn_config.py"
  --pythonpath "$ROOT/backend"
)

if [[ "$ADMIN_DAEMON" == "1" ]]; then
  mkdir -p "$ROOT/runtime/logs"
  GUNICORN_ARGS+=(
    --daemon
    --access-logfile "$ROOT/runtime/logs/admin-8102-access.log"
    --error-logfile "$ROOT/runtime/logs/admin-8102-error.log"
  )
else
  GUNICORN_ARGS+=(
    --access-logfile -
    --error-logfile -
  )
fi

exec "$PYTHON_BIN" "${GUNICORN_ARGS[@]}"
