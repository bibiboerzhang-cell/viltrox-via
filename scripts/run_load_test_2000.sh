#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/runtime_env.sh"

export LOAD_TEST_PUBLIC_BASE="${LOAD_TEST_PUBLIC_BASE:-http://127.0.0.1:8101}"
export LOAD_TEST_ADMIN_BASE="${LOAD_TEST_ADMIN_BASE:-http://127.0.0.1:8102}"
export LOAD_TEST_CONCURRENCY="${LOAD_TEST_CONCURRENCY:-2200}"
export LOAD_TEST_TOTAL_REQUESTS="${LOAD_TEST_TOTAL_REQUESTS:-2400}"
export LOAD_TEST_TIMEOUT_SEC="${LOAD_TEST_TIMEOUT_SEC:-25}"

python3 "$ROOT/scripts/load_test_2000.py"
