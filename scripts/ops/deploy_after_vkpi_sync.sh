#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

SYNC_STATUS_SCRIPT="${SYNC_STATUS_SCRIPT:-${SCRIPT_DIR}/check_vkpi_daily_sync_status.sh}"
DEPLOY_SCRIPT="${DEPLOY_SCRIPT:-${SCRIPT_DIR}/deploy_local_to_cloud.sh}"
POST_SYNC_AUDIT_SCRIPT="${POST_SYNC_AUDIT_SCRIPT:-${SCRIPT_DIR}/audit_vkpi_post_sync_state.py}"
PLAN_STATUS_SCRIPT="${PLAN_STATUS_SCRIPT:-${SCRIPT_DIR}/report_vkpi_plan_status.py}"
BENCHMARK_SCRIPT="${BENCHMARK_SCRIPT:-${SCRIPT_DIR}/benchmark_vkpi_perf.sh}"
RUN_BENCHMARK="${RUN_BENCHMARK:-0}"
PLAN_STATUS_OUT="${PLAN_STATUS_OUT:-runtime/vkpi-plan-status/latest.md}"

status_json="$("${SYNC_STATUS_SCRIPT}")"
echo "${status_json}"

eval "$(
  STATUS_JSON="${status_json}" python3 - <<'PY'
import json
import os
import shlex

payload = json.loads(os.environ["STATUS_JSON"])
failure_tail = payload.get("failure_tail") or []
values = {
    "service_state": str(payload.get("service_state") or ""),
    "active_state": str(payload.get("active_state") or ""),
    "sub_state": str(payload.get("sub_state") or ""),
    "result": str(payload.get("result") or ""),
    "exec_main_status": str(payload.get("exec_main_status") or ""),
    "failure_count": str(len(failure_tail)),
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
  echo "{\"skipped\":true,\"reason\":\"vkpi-sync-daily.service is ${service_state}; deploy deferred\"}"
  exit 0
fi

if [ "${post_sync_safe}" != "1" ]; then
  echo "Refusing deploy: daily sync has no verified provider completion (orchestration=${orchestration_status}, scope=${completion_scope}, provider=${provider_completion})" >&2
  exit 2
fi

if [ "${failure_count}" != "0" ]; then
  echo "Refusing deploy: sync failure_tail has ${failure_count} entries" >&2
  exit 2
fi

if [ -n "${result}" ] && [ "${result}" != "success" ]; then
  echo "Refusing deploy: sync result=${result}" >&2
  exit 2
fi

if [ -n "${exec_main_status}" ] && [ "${exec_main_status}" != "0" ]; then
  echo "Refusing deploy: sync exec_main_status=${exec_main_status}" >&2
  exit 2
fi

"${DEPLOY_SCRIPT}"
"${POST_SYNC_AUDIT_SCRIPT}"

if [ "${RUN_BENCHMARK}" = "1" ]; then
  "${BENCHMARK_SCRIPT}"
fi

"${PLAN_STATUS_SCRIPT}" --out "${PLAN_STATUS_OUT}"
