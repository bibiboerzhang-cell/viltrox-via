#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

SYNC_STATUS_SCRIPT="${SYNC_STATUS_SCRIPT:-${SCRIPT_DIR}/check_vkpi_daily_sync_status.sh}"
R2_READINESS_SCRIPT="${R2_READINESS_SCRIPT:-${SCRIPT_DIR}/check_vkpi_r2_readiness.py}"
PYTHON_BIN="${PYTHON_BIN:-${PROJECT_ROOT}/.venv/bin/python}"
REMOTE_PYTHON_BIN="${REMOTE_PYTHON_BIN:-.venv/bin/python}"
MIGRATION_SCRIPT="${MIGRATION_SCRIPT:-${PROJECT_ROOT}/scripts/migrate_vkpi_media_cache_to_r2.py}"
SSH_TARGET="${SSH_TARGET:-viltrox}"
REMOTE_ROOT="${REMOTE_ROOT:-/opt/viltrox-2.0}"
RUN_REMOTE="${RUN_REMOTE:-1}"
LIMIT="${LIMIT:-200}"
PLATFORM="${PLATFORM:-}"
EXECUTE="${EXECUTE:-1}"

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
  echo "{\"skipped\":true,\"reason\":\"vkpi-sync-daily.service is ${service_state}; R2 migration deferred\"}"
  exit 0
fi

if [ "${failure_count}" != "0" ]; then
  echo "Refusing R2 migration: sync failure_tail has ${failure_count} entries" >&2
  exit 2
fi

if [ -n "${result}" ] && [ "${result}" != "success" ]; then
  echo "Refusing R2 migration: sync result=${result}" >&2
  exit 2
fi

if [ -n "${exec_main_status}" ] && [ "${exec_main_status}" != "0" ]; then
  echo "Refusing R2 migration: sync exec_main_status=${exec_main_status}" >&2
  exit 2
fi

r2_json="$("${R2_READINESS_SCRIPT}" --remote "${SSH_TARGET}" --remote-root "${REMOTE_ROOT}" || true)"
echo "${r2_json}"

remote_ready="$(
  R2_JSON="${r2_json}" python3 - <<'PY'
import json
import os
payload = json.loads(os.environ["R2_JSON"])
remote = payload.get("remote") if isinstance(payload.get("remote"), dict) else {}
print("1" if remote.get("ready_for_new_uploads") else "0")
PY
)"

if [ "${remote_ready}" != "1" ]; then
  echo "{\"skipped\":true,\"reason\":\"remote R2 media cache env is not ready; migration blocked\"}"
  exit 0
fi

args=( "${MIGRATION_SCRIPT}" "--limit" "${LIMIT}" )
if [ "${EXECUTE}" = "1" ]; then
  args+=( "--execute" )
fi
if [ -n "${PLATFORM}" ]; then
  args+=( "--platform" "${PLATFORM}" )
fi

if [ "${RUN_REMOTE}" = "1" ]; then
  quote() { printf "%q" "$1"; }
  ssh "${SSH_TARGET}" "cd $(quote "${REMOTE_ROOT}") && LIMIT=$(quote "${LIMIT}") PLATFORM=$(quote "${PLATFORM}") EXECUTE=$(quote "${EXECUTE}") PYTHON_BIN=$(quote "${REMOTE_PYTHON_BIN}") bash -s" <<'SH'
set -euo pipefail
args=( "scripts/migrate_vkpi_media_cache_to_r2.py" "--limit" "${LIMIT}" )
if [ "${EXECUTE}" = "1" ]; then
  args+=( "--execute" )
fi
if [ -n "${PLATFORM}" ]; then
  args+=( "--platform" "${PLATFORM}" )
fi
PYTHONPATH=backend "${PYTHON_BIN}" "${args[@]}"
SH
else
  "${PYTHON_BIN}" "${args[@]}"
fi
