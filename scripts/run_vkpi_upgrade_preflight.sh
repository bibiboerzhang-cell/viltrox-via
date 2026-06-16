#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="${PY:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

source "$ROOT/scripts/runtime_env.sh"

export PYTHONPATH="${PYTHONPATH:-backend}"
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost,::1}"
export no_proxy="${no_proxy:-127.0.0.1,localhost,::1}"

echo "[vkpi-preflight] repo"
git status --short
git log --oneline -1

echo "[vkpi-preflight] frontend build"
npm --prefix frontend run build

echo "[vkpi-preflight] frontend typecheck"
(cd frontend && npx tsc --noEmit)

echo "[vkpi-preflight] frontend tests"
npm --prefix frontend test -- --run

echo "[vkpi-preflight] backend tests"
"$PY" -m pytest -q

echo "[vkpi-preflight] local runtime health"
"$PY" scripts/audit_vkpi_runtime_state.py
"$PY" scripts/smoke_vkpi_p4_25_runtime_health_preflight.py

echo "[vkpi-preflight] ok"
