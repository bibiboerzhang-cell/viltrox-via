#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export WORKER_CLUSTER_TIER="${WORKER_CLUSTER_TIER:-300}"
export POSTGRES_POOL_MIN_SIZE="${POSTGRES_POOL_MIN_SIZE:-1}"
export POSTGRES_POOL_MAX_SIZE="${POSTGRES_POOL_MAX_SIZE:-3}"
export POSTGRES_POOL_TIMEOUT_SEC="${POSTGRES_POOL_TIMEOUT_SEC:-120}"

exec "$ROOT/scripts/start_worker_cluster.sh"
