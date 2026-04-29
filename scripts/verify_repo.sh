#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="${PYTHON_BIN_FALLBACK:-python3}"
fi

cd "$ROOT"

"$PYTHON_BIN" scripts/generate_frontend_contracts.py
"$PYTHON_BIN" scripts/check_silent_exception_baseline.py --baseline scripts/silent_exception_baseline.json
"$PYTHON_BIN" scripts/check_repo_hardening.py --strict --warning-baseline scripts/hardening_warning_baseline.json
"$PYTHON_BIN" -m alembic -c "$ROOT/alembic.ini" heads >/dev/null
"$PYTHON_BIN" -m compileall backend/app scripts tests >/dev/null
"$PYTHON_BIN" -m pytest -q

if [ ! -d "$ROOT/frontend/node_modules" ]; then
  npm --prefix "$ROOT/frontend" ci
fi

npm --prefix "$ROOT/frontend" run build
