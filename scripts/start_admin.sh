#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/runtime_env.sh"
PYTHON_BIN="${PYTHON_BIN:-python3}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8102}"
WEB_CONCURRENCY="${WEB_CONCURRENCY:-2}"
WORKER_CONNECTIONS="${WORKER_CONNECTIONS:-2000}"
BACKLOG="${BACKLOG:-4096}"

cd "$ROOT"

export APP_ROLE="${APP_ROLE:-admin-web}"
export ENVIRONMENT="${ENVIRONMENT:-production}"
export DB_RUNTIME_BACKEND="${DB_RUNTIME_BACKEND:-postgres}"
export ENABLE_SCHEDULER="${ENABLE_SCHEDULER:-0}"
export ENABLE_BROWSER="${ENABLE_BROWSER:-0}"
export ENABLE_UPLOAD_CLEANUP="${ENABLE_UPLOAD_CLEANUP:-0}"
if [[ "$ENVIRONMENT" == "local" && "${LOCAL_RUNTIME_FORCE_STACK:-1}" == "1" ]]; then
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

exec "$PYTHON_BIN" -m gunicorn app.main:app \
  -c "$ROOT/deploy/gunicorn_config.py" \
  --pythonpath "$ROOT/backend" \
  --access-logfile - \
  --error-logfile -
