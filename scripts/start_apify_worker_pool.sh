#!/usr/bin/env bash
# Start one reserved interactive lane plus two or more batch lanes.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ENVIRONMENT="${ENVIRONMENT:-local}"
export APP_ROLE="${APP_ROLE:-worker}"
export RUNTIME_ENV_QUIET=1
source "$ROOT/scripts/runtime_env.sh"

if [[ -z "${PYTHON_BIN:-}" ]]; then
  PYTHON_BIN="$ROOT/.venv/bin/python"
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

BULK_COUNT="${APIFY_WORKER_POOL_BULK_COUNT:-2}"
if [[ ! "$BULK_COUNT" =~ ^[1-4]$ ]]; then
  echo "APIFY_WORKER_POOL_BULK_COUNT must be within 1..4" >&2
  exit 2
fi

legacy_pidfiles=("$ROOT/runtime/worker.pid" "$ROOT/runtime/worker-2.pid" "$ROOT/runtime/worker-3.pid")
for pidfile in "${legacy_pidfiles[@]}"; do
  [[ -f "$pidfile" ]] || continue
  logfile="$ROOT/runtime/logs/$(basename "$pidfile" .pid).log"
  if "$PYTHON_BIN" "$ROOT/scripts/worker_pid_guard.py" \
      --pidfile "$pidfile" --root "$ROOT" --logfile "$logfile" >/dev/null 2>&1; then
    echo "refusing mixed topology: verified legacy worker is still running at $pidfile" >&2
    exit 3
  fi
done

lanes=(interactive)
for ((index=1; index<=BULK_COUNT; index++)); do
  lanes+=("bulk${index}")
done

# Tier 1 keeps every paid/heavy family at its existing global concurrency.
# Tier 2 is an explicit canary only; Gemini remains one-wide until its actual
# request boundary is fully governed.
BURST_TIER="${APIFY_WORKER_BURST_TIER:-1}"
if [[ "$BURST_TIER" == "1" ]]; then
  export APIFY_WORKER_PROFILE_MEDIA_CONCURRENCY=1
  export APIFY_WORKER_COMMENTS_CONCURRENCY=1
  export APIFY_WORKER_GEMINI_VIDEO_CONCURRENCY=1
elif [[ "$BURST_TIER" == "2" ]]; then
  export APIFY_WORKER_PROFILE_MEDIA_CONCURRENCY=2
  export APIFY_WORKER_COMMENTS_CONCURRENCY=2
  export APIFY_WORKER_GEMINI_VIDEO_CONCURRENCY=1
else
  echo "APIFY_WORKER_BURST_TIER must be 1 or 2" >&2
  exit 2
fi

started=()
rollback() {
  for ((index=${#started[@]}-1; index>=0; index--)); do
    lane="${started[$index]}"
    PIDFILE="$ROOT/runtime/worker-${lane}.pid" \
    LOGFILE="$ROOT/runtime/logs/worker-${lane}.log" \
      bash "$ROOT/scripts/stop_worker.sh" >/dev/null 2>&1 || true
  done
}
trap rollback ERR

for lane in "${lanes[@]}"; do
  bash "$ROOT/scripts/start_worker_lane.sh" "$lane"
  started+=("$lane")
done
sleep 3
PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON_BIN" "$ROOT/scripts/verify_apify_worker_pool.py" --lanes "${lanes[@]}"
trap - ERR
echo "apify worker pool ready: ${lanes[*]} (burst tier $BURST_TIER)"
