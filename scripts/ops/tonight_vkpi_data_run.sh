#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

STAMP="${STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"

echo "== preflight state =="
"${SCRIPT_DIR}/audit_prod_vkpi_state.sh"

echo "== company official-account full baseline =="
STAMP="${STAMP}" \
JOB_NAME=official_full_baseline \
PAYLOAD_JSON='{"confirm":"RUN official_full_baseline","staff":{"id":0,"staff_id":0,"user_id":0,"role":"admin","is_owner":1}}' \
REQUIRE_BACKUP=1 \
  "${SCRIPT_DIR}/run_prod_vkpi_job.sh"

echo "== post-run state =="
"${SCRIPT_DIR}/audit_prod_vkpi_state.sh"

cat <<'NOTE'
1012 KOL note:
- This script intentionally does not start an unverified 1012 provider/deep-scan job.
- The current production-safe 1012 surface is vkpi_kol_pool state verification
  plus search/history matching against existing cooperation evidence.
- Start a 1012 provider run only after a concrete, tested job entrypoint exists and budget/provider gates are explicit.
NOTE
