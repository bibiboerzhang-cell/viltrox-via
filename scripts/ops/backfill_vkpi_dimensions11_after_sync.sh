#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

SYNC_STATUS_SCRIPT="${SYNC_STATUS_SCRIPT:-${SCRIPT_DIR}/check_vkpi_daily_sync_status.sh}"
PYTHON_BIN="${PYTHON_BIN:-${PROJECT_ROOT}/.venv/bin/python}"
REMOTE_PYTHON_BIN="${REMOTE_PYTHON_BIN:-.venv/bin/python}"
DIMENSIONS_SCRIPT="${DIMENSIONS_SCRIPT:-${PROJECT_ROOT}/scripts/vkpi_dimensions11_dry_run.py}"
SSH_TARGET="${SSH_TARGET:-viltrox}"
REMOTE_ROOT="${REMOTE_ROOT:-/opt/viltrox-2.0}"
RUN_REMOTE="${RUN_REMOTE:-1}"
LIMIT="${LIMIT:-1200}"
SOURCE_TYPE="${SOURCE_TYPE:-legacy_excel_p2d}"

status_json="$("${SYNC_STATUS_SCRIPT}")"
echo "${status_json}"

eval "$(
  STATUS_JSON="${status_json}" PYTHONDONTWRITEBYTECODE=1 python3 -B - <<'PY'
import json
import os
import shlex

payload = json.loads(os.environ["STATUS_JSON"])
values = {
    "service_state": str(payload.get("service_state") or ""),
    "result": str(payload.get("result") or ""),
    "exec_main_status": str(payload.get("exec_main_status") or ""),
    "failure_count": str(len(payload.get("failure_tail") or [])),
    "post_sync_safe": "1" if payload.get("post_sync_safe") is True else "0",
    "completion_scope": str(payload.get("completion_scope") or "unknown"),
    "provider_completion": str(payload.get("provider_completion") or "unknown"),
    "orchestration_status": str(payload.get("orchestration_status") or "unknown"),
}
for key, value in values.items():
    print(f"{key}={shlex.quote(value)}")
PY
)"

if [ "${service_state}" = "active" ] || [ "${service_state}" = "activating" ]; then
  echo "{\"skipped\":true,\"reason\":\"vkpi-sync-daily.service is ${service_state}; dimensions11 backfill deferred\"}"
  exit 0
fi

if [ "${post_sync_safe}" != "1" ]; then
  echo "Refusing dimensions11 backfill: daily sync has no verified provider completion (orchestration=${orchestration_status}, scope=${completion_scope}, provider=${provider_completion})" >&2
  exit 2
fi

if [ "${failure_count}" != "0" ]; then
  echo "Refusing dimensions11 backfill: sync failure_tail has ${failure_count} entries" >&2
  exit 2
fi

if [ -n "${result}" ] && [ "${result}" != "success" ]; then
  echo "Refusing dimensions11 backfill: sync result=${result}" >&2
  exit 2
fi

if [ -n "${exec_main_status}" ] && [ "${exec_main_status}" != "0" ]; then
  echo "Refusing dimensions11 backfill: sync exec_main_status=${exec_main_status}" >&2
  exit 2
fi

args=( "${DIMENSIONS_SCRIPT}" "--limit" "${LIMIT}" "--source-type" "${SOURCE_TYPE}" "--write-db" )

if [ "${RUN_REMOTE}" = "1" ]; then
  quote() { printf "%q" "$1"; }
  ssh "${SSH_TARGET}" "cd $(quote "${REMOTE_ROOT}") && LIMIT=$(quote "${LIMIT}") SOURCE_TYPE=$(quote "${SOURCE_TYPE}") PYTHON_BIN=$(quote "${REMOTE_PYTHON_BIN}") bash -s" <<'SH'
set -euo pipefail
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend "${PYTHON_BIN}" -B scripts/vkpi_dimensions11_dry_run.py \
  --limit "${LIMIT}" \
  --source-type "${SOURCE_TYPE}" \
  --write-db
SH
else
  PYTHONDONTWRITEBYTECODE=1 "${PYTHON_BIN}" -B "${args[@]}"
fi
