#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/runtime_env.sh"
PYTHON_BIN="${PYTHON_BIN:-python3}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8103}"

cd "$ROOT"

export APP_ROLE="${APP_ROLE:-worker}"
export ENVIRONMENT="${ENVIRONMENT:-production}"
export DB_RUNTIME_BACKEND="${DB_RUNTIME_BACKEND:-postgres}"
export ENABLE_SCHEDULER="${ENABLE_SCHEDULER:-1}"
export ENABLE_LOCAL_ORCHESTRATOR="${ENABLE_LOCAL_ORCHESTRATOR:-0}"
export ENABLE_BROWSER="${ENABLE_BROWSER:-0}"
export ENABLE_UPLOAD_CLEANUP="${ENABLE_UPLOAD_CLEANUP:-1}"
export DATABASE_URL="${DATABASE_URL:-$LOCAL_DATABASE_URL}"
export REDIS_URL="${REDIS_URL:-$LOCAL_REDIS_URL}"

# Single process - scheduler must only have one active instance.
export WORKERS=1
export BIND="$HOST:$PORT"

exec "$PYTHON_BIN" -m gunicorn app.main:app \
  -c "$ROOT/deploy/gunicorn_config.py" \
  --pythonpath "$ROOT/backend" \
  --access-logfile - \
  --error-logfile -
