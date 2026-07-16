#!/usr/bin/env bash
# Drain-aware shutdown for the apify_jobs worker pool.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ENVIRONMENT="${ENVIRONMENT:-local}"
export APP_ROLE="${APP_ROLE:-worker}"
export RUNTIME_ENV_QUIET=1
source "$ROOT/scripts/runtime_env.sh"

BULK_COUNT="${APIFY_WORKER_POOL_BULK_COUNT:-2}"
WAIT_SECONDS="${APIFY_WORKER_DRAIN_WAIT_SECONDS:-0}"
if [[ ! "$BULK_COUNT" =~ ^[1-4]$ ]] || [[ ! "$WAIT_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "invalid pool or drain configuration" >&2
  exit 2
fi

lanes=()
for ((index=BULK_COUNT; index>=1; index--)); do
  lanes+=("bulk${index}")
done
lanes+=(interactive)
host="$(hostname -s)"

for lane in "${lanes[@]}"; do
  pidfile="$ROOT/runtime/worker-${lane}.pid"
  logfile="$ROOT/runtime/logs/worker-${lane}.log"
  [[ -f "$pidfile" ]] || continue
  pid="$(tr -d '[:space:]' < "$pidfile")"
  worker_name="apify-worker-${lane}-${host}"
  waited=0
  while true; do
    busy="$(psql "$DATABASE_URL" -Atc \
      "SELECT COUNT(*) FROM apify_jobs WHERE status='running' AND lease_owner='${worker_name}:${pid}'")"
    if [[ "$busy" == "0" ]]; then
      break
    fi
    if (( waited >= WAIT_SECONDS )); then
      echo "refusing to stop busy lane $lane ($busy running job); set APIFY_WORKER_DRAIN_WAIT_SECONDS" >&2
      exit 4
    fi
    sleep 2
    waited=$((waited + 2))
  done
  PIDFILE="$pidfile" LOGFILE="$logfile" bash "$ROOT/scripts/stop_worker.sh"
done
echo "apify worker pool stopped"
