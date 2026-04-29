#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/runtime_env.sh"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$ROOT"

export APP_ROLE="${APP_ROLE:-worker}"
export ENVIRONMENT="${ENVIRONMENT:-production}"
export DB_RUNTIME_BACKEND="${DB_RUNTIME_BACKEND:-postgres}"
export ENABLE_LOCAL_ORCHESTRATOR="${ENABLE_LOCAL_ORCHESTRATOR:-0}"
export ENABLE_SCHEDULER="${ENABLE_SCHEDULER:-0}"
export ENABLE_BROWSER="${ENABLE_BROWSER:-0}"
export ENABLE_UPLOAD_CLEANUP="${ENABLE_UPLOAD_CLEANUP:-0}"
export DATABASE_URL="${DATABASE_URL:-$LOCAL_DATABASE_URL}"
export REDIS_URL="${REDIS_URL:-$LOCAL_REDIS_URL}"

cd "$ROOT/backend"
exec "$PYTHON_BIN" -m app.workers.worker_main
