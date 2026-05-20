#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

SYNC_STATUS_SCRIPT="${SYNC_STATUS_SCRIPT:-${SCRIPT_DIR}/check_vkpi_daily_sync_status.sh}"
PYTHON_BIN="${PYTHON_BIN:-${PROJECT_ROOT}/.venv/bin/python}"
COMPETITOR_SCRIPT="${COMPETITOR_SCRIPT:-${PROJECT_ROOT}/scripts/vkpi_kol_competitor_dry_run.py}"
LIMIT="${LIMIT:-1200}"
SOURCE_TYPE="${SOURCE_TYPE:-legacy_excel_p2d}"
BRAND="${BRAND:-}"

status_json="$("${SYNC_STATUS_SCRIPT}")"
echo "${status_json}"

eval "$(
  STATUS_JSON="${status_json}" python3 - <<'PY'
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
  echo "{\"skipped\":true,\"reason\":\"vkpi-sync-daily.service is ${service_state}; competitor relation backfill deferred\"}"
  exit 0
fi

if [ "${failure_count}" != "0" ]; then
  echo "Refusing competitor relation backfill: sync failure_tail has ${failure_count} entries" >&2
  exit 2
fi

if [ -n "${result}" ] && [ "${result}" != "success" ]; then
  echo "Refusing competitor relation backfill: sync result=${result}" >&2
  exit 2
fi

if [ -n "${exec_main_status}" ] && [ "${exec_main_status}" != "0" ]; then
  echo "Refusing competitor relation backfill: sync exec_main_status=${exec_main_status}" >&2
  exit 2
fi

args=( "${COMPETITOR_SCRIPT}" "--limit" "${LIMIT}" "--source-type" "${SOURCE_TYPE}" "--write-db" )
if [ -n "${BRAND}" ]; then
  args+=( "--brand" "${BRAND}" )
fi

"${PYTHON_BIN}" "${args[@]}"
