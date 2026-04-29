#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/runtime_env.sh"

PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="${PYTHON_BIN_FALLBACK:-python3}"
fi

cd "$ROOT"

export PYTHONPATH="$ROOT/backend:${PYTHONPATH:-}"
export DATABASE_URL="${DATABASE_URL:-$LOCAL_DATABASE_URL}"

if [ "${DB_RUNTIME_BACKEND:-postgres}" != "postgres" ]; then
  echo "skip alembic upgrade: DB_RUNTIME_BACKEND=${DB_RUNTIME_BACKEND:-unset} (requires postgres)"
  exit 0
fi

"$PYTHON_BIN" -m alembic -c "$ROOT/alembic.ini" upgrade head
