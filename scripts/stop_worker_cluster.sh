#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/runtime_env.sh"

for pid_file in "$RUNTIME_LOGS"/worker-*.pid; do
  [[ -e "$pid_file" ]] || continue
  pid="$(cat "$pid_file")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    echo "stopped $(basename "$pid_file" .pid) pid $pid"
  fi
  rm -f "$pid_file"
done
