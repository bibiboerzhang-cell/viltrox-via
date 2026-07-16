#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/runtime_env.sh"
if [[ -z "${PYTHON_BIN:-}" && -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT/.venv/bin/python"
fi
PYTHON_BIN="${PYTHON_BIN:-python3}"

WORKER_CLUSTER_TIER="${WORKER_CLUSTER_TIER:-60}"
case "$WORKER_CLUSTER_TIER" in
  60)
    PROFILE_PROCESSES=4
    PROFILE_CONSUMERS=2
    ;;
  120)
    PROFILE_PROCESSES=8
    PROFILE_CONSUMERS=2
    ;;
  300)
    PROFILE_PROCESSES=20
    PROFILE_CONSUMERS=2
    ;;
  custom)
    PROFILE_PROCESSES="${WORKER_SERVICE_PROCESSES:-2}"
    PROFILE_CONSUMERS="${WORKER_ASYNC_CONSUMERS:-2}"
    ;;
  *)
    echo "Unsupported WORKER_CLUSTER_TIER=$WORKER_CLUSTER_TIER. Use 60, 120, 300, or custom." >&2
    exit 2
    ;;
esac

if [[ "$WORKER_CLUSTER_TIER" == "custom" ]]; then
  WORKER_SERVICE_PROCESSES="${WORKER_SERVICE_PROCESSES:-$PROFILE_PROCESSES}"
  WORKER_ASYNC_CONSUMERS="${WORKER_ASYNC_CONSUMERS:-$PROFILE_CONSUMERS}"
else
  WORKER_SERVICE_PROCESSES="$PROFILE_PROCESSES"
  WORKER_ASYNC_CONSUMERS="$PROFILE_CONSUMERS"
fi

# Keep the launcher and worker runtime on the same reviewed concurrency
# contract.  The worker deliberately rejects the historical 15-consumer
# setting, so fail before spawning detached processes instead of leaving a
# stale pidfile and a briefly green heartbeat from the previous worker.
REDIS_CONSUMER_HARD_MAX="${VKPI_REDIS_WORKER_MAX_CONSUMERS:-2}"
if [[ ! "$WORKER_SERVICE_PROCESSES" =~ ^[1-9][0-9]*$ ]] \
    || [[ ! "$WORKER_ASYNC_CONSUMERS" =~ ^[1-9][0-9]*$ ]] \
    || [[ ! "$REDIS_CONSUMER_HARD_MAX" =~ ^[1-4]$ ]]; then
  echo "invalid Redis worker process/concurrency configuration" >&2
  exit 2
fi
if (( WORKER_ASYNC_CONSUMERS > REDIS_CONSUMER_HARD_MAX )); then
  echo "WORKER_ASYNC_CONSUMERS=$WORKER_ASYNC_CONSUMERS exceeds reviewed hard max $REDIS_CONSUMER_HARD_MAX" >&2
  exit 2
fi

mkdir -p "$RUNTIME_LOGS"

STARTED_PIDS=()

for index in $(seq 1 "$WORKER_SERVICE_PROCESSES"); do
  log_file="$RUNTIME_LOGS/worker-${index}.log"
  pid_file="$RUNTIME_LOGS/worker-${index}.pid"
  if [[ "$WORKER_SERVICE_PROCESSES" == "1" ]]; then
    heartbeat_name="${VKPI_REDIS_WORKER_HEARTBEAT_NAME:-redis-worker-main}"
  else
    heartbeat_name="${VKPI_REDIS_WORKER_HEARTBEAT_NAME:-redis-worker-local-${index}}"
  fi
  if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    echo "worker-$index already running with pid $(cat "$pid_file")"
    continue
  fi
  (
    cd "$ROOT/backend"
    export APP_ROLE="worker"
    export ENVIRONMENT="${ENVIRONMENT:-production}"
    export DB_RUNTIME_BACKEND="${DB_RUNTIME_BACKEND:-postgres}"
    export ENABLE_LOCAL_ORCHESTRATOR="${ENABLE_LOCAL_ORCHESTRATOR:-0}"
    export ENABLE_SCHEDULER="${ENABLE_SCHEDULER:-0}"
    export ENABLE_BROWSER="${ENABLE_BROWSER:-0}"
    export ENABLE_UPLOAD_CLEANUP="${ENABLE_UPLOAD_CLEANUP:-0}"
    export DATABASE_URL="${DATABASE_URL:-$LOCAL_DATABASE_URL}"
    export REDIS_URL="${REDIS_URL:-$LOCAL_REDIS_URL}"
    export WORKER_INSTANCE="$index"
    export WORKER_CLUSTER_TIER="$WORKER_CLUSTER_TIER"
    export WORKER_SERVICE_PROCESSES="$WORKER_SERVICE_PROCESSES"
    export WORKER_ASYNC_CONSUMERS="$WORKER_ASYNC_CONSUMERS"
    export VKPI_REDIS_WORKER_HEARTBEAT_NAME="$heartbeat_name"
    # ``nohup`` only ignores SIGHUP.  PTY/task runners may still reap the
    # launch process group after this script returns, so give every worker its
    # own session before replacing the wrapper with the real runtime.  This is
    # the same lifecycle contract used by start_worker.sh for Apify lanes.
    exec nohup "$PYTHON_BIN" -c \
      'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' \
      "$PYTHON_BIN" -m app.workers.worker_main
  ) >>"$log_file" 2>&1 &
  pid="$!"
  echo "$pid" >"$pid_file"
  echo "started worker-$index pid $pid"
  STARTED_PIDS+=("$pid")
done

TOTAL_CONCURRENCY=$((WORKER_SERVICE_PROCESSES * WORKER_ASYNC_CONSUMERS))
echo "worker cluster ready: tier ${WORKER_CLUSTER_TIER}, ${WORKER_SERVICE_PROCESSES} processes x ${WORKER_ASYNC_CONSUMERS} async consumers = ${TOTAL_CONCURRENCY}"
echo "postgres pool per process: min ${POSTGRES_POOL_MIN_SIZE:-default}, max ${POSTGRES_POOL_MAX_SIZE:-default}, timeout ${POSTGRES_POOL_TIMEOUT_SEC:-default}s"

if [[ "${WORKER_CLUSTER_FOREGROUND:-0}" == "1" && "${#STARTED_PIDS[@]}" -gt 0 ]]; then
  shutdown_workers() {
    for pid in "${STARTED_PIDS[@]}"; do
      kill "$pid" 2>/dev/null || true
    done
  }
  trap shutdown_workers INT TERM EXIT
  wait "${STARTED_PIDS[@]}"
fi
