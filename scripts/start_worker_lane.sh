#!/usr/bin/env bash
# 本地多车道 worker 启动包装(非常驻:本地长命进程必须当天手动停,见 stop 提示)。
# 用法: bash scripts/start_worker_lane.sh interactive   # 交互专用车道
#       bash scripts/start_worker_lane.sh bulk1         # 全量车道(任意 bulk* 名)
set -euo pipefail
LANE="${1:?用法: start_worker_lane.sh <interactive|bulkN>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ "$LANE" == "interactive" ]]; then
  export APIFY_WORKER_CLAIM_LANE=interactive
else
  export APIFY_WORKER_CLAIM_LANE=all
fi
export APIFY_WORKER_HEARTBEAT_NAME="apify-worker-${LANE}-$(hostname -s)"
export APIFY_WORKER_GEMINI_QPS="${APIFY_WORKER_GEMINI_QPS:-0.25}"
export APIFY_WORKER_LLM_CONCURRENCY="${APIFY_WORKER_LLM_CONCURRENCY:-4}"
export POSTGRES_POOL_MIN_SIZE="${POSTGRES_POOL_MIN_SIZE:-2}"
export POSTGRES_POOL_MAX_SIZE="${POSTGRES_POOL_MAX_SIZE:-12}"

PIDFILE="$ROOT/runtime/worker-${LANE}.pid" \
LOGFILE="$ROOT/runtime/logs/worker-${LANE}.log" \
  bash "$ROOT/scripts/start_worker.sh"

echo "车道 ${LANE} 已启动;停止: PIDFILE=$ROOT/runtime/worker-${LANE}.pid bash scripts/stop_worker.sh"
