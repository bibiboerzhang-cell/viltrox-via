#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

SYNC_STATUS_SCRIPT="${SYNC_STATUS_SCRIPT:-${SCRIPT_DIR}/check_vkpi_daily_sync_status.sh}"
PYTHON_BIN="${PYTHON_BIN:-${PROJECT_ROOT}/.venv/bin/python}"
REMOTE_PYTHON_BIN="${REMOTE_PYTHON_BIN:-.venv/bin/python}"
BRAND_SIGNAL_SCRIPT="${BRAND_SIGNAL_SCRIPT:-${PROJECT_ROOT}/scripts/vkpi_brand_signal_scan.py}"
SSH_TARGET="${SSH_TARGET:-viltrox}"
REMOTE_ROOT="${REMOTE_ROOT:-/opt/viltrox-2.0}"
RUN_REMOTE="${RUN_REMOTE:-1}"
SOURCE="${SOURCE:-all}"
LIMIT="${LIMIT:-2000}"
SINCE="${SINCE:-}"

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
}
for key, value in values.items():
    print(f"{key}={shlex.quote(value)}")
PY
)"

if [ "${service_state}" = "active" ] || [ "${service_state}" = "activating" ]; then
  echo "{\"skipped\":true,\"reason\":\"vkpi-sync-daily.service is ${service_state}; brand signal scan deferred\"}"
  exit 0
fi

if [ "${failure_count}" != "0" ]; then
  echo "Refusing brand signal scan: sync failure_tail has ${failure_count} entries" >&2
  exit 2
fi

if [ -n "${result}" ] && [ "${result}" != "success" ]; then
  echo "Refusing brand signal scan: sync result=${result}" >&2
  exit 2
fi

if [ -n "${exec_main_status}" ] && [ "${exec_main_status}" != "0" ]; then
  echo "Refusing brand signal scan: sync exec_main_status=${exec_main_status}" >&2
  exit 2
fi

args=( "${BRAND_SIGNAL_SCRIPT}" "--source" "${SOURCE}" "--limit" "${LIMIT}" "--write-db" )
if [ -n "${SINCE}" ]; then
  args+=( "--since" "${SINCE}" )
fi

if [ "${RUN_REMOTE}" = "1" ]; then
  quote() { printf "%q" "$1"; }
  ssh "${SSH_TARGET}" "cd $(quote "${REMOTE_ROOT}") && SOURCE=$(quote "${SOURCE}") LIMIT=$(quote "${LIMIT}") SINCE=$(quote "${SINCE}") PYTHON_BIN=$(quote "${REMOTE_PYTHON_BIN}") bash -s" <<'SH'
set -euo pipefail
args=( "scripts/vkpi_brand_signal_scan.py" "--source" "${SOURCE}" "--limit" "${LIMIT}" "--write-db" )
if [ -n "${SINCE}" ]; then
  args+=( "--since" "${SINCE}" )
fi
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend "${PYTHON_BIN}" -B "${args[@]}"
SH
else
  PYTHONDONTWRITEBYTECODE=1 "${PYTHON_BIN}" -B "${args[@]}"
fi
