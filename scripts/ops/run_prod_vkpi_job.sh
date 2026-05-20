#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

SSH_TARGET="${SSH_TARGET:-viltrox}"
REMOTE_ROOT="${REMOTE_ROOT:-/opt/viltrox-2.0}"
JOB_ARG="${1:-}"
PAYLOAD_ARG="${2:-}"
JOB_NAME="${JOB_NAME:-${JOB_ARG}}"
PAYLOAD_JSON="${PAYLOAD_JSON:-${PAYLOAD_ARG}}"
if [ -z "${PAYLOAD_JSON}" ]; then
  PAYLOAD_JSON="{}"
fi
STAMP="${STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"
PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"

if [ -z "${JOB_NAME}" ]; then
  echo "Usage: JOB_NAME=official_full_baseline PAYLOAD_JSON='{}' $0" >&2
  exit 1
fi

python3 -m json.tool >/dev/null <<<"${PAYLOAD_JSON}"

if [ "${REQUIRE_BACKUP:-1}" = "1" ]; then
  STAMP="${STAMP}" "${SCRIPT_DIR}/backup_prod_vkpi.sh"
fi

REMOTE_PAYLOAD="runtime/ops/${STAMP}-${JOB_NAME}.payload.json"
REMOTE_LOG="runtime/ops/${STAMP}-${JOB_NAME}.log"

printf '%s' "${PAYLOAD_JSON}" | ssh "${SSH_TARGET}" "cd '${REMOTE_ROOT}' && mkdir -p runtime/ops && cat > '${REMOTE_PAYLOAD}'"

ssh "${SSH_TARGET}" "cd '${REMOTE_ROOT}' && JOB_NAME='${JOB_NAME}' PAYLOAD_FILE='${REMOTE_PAYLOAD}' LOG_FILE='${REMOTE_LOG}' PYTHONPATH=backend '${PYTHON_BIN}' - <<'PY'
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from app.services.vkpi.cron import run_job


_log_fp = open(os.environ['LOG_FILE'], 'a', encoding='utf-8')


def emit(payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False, default=str, indent=2)
    print(text, flush=True)
    _log_fp.write(text + '\n')
    _log_fp.flush()


async def main() -> None:
    job_name = os.environ['JOB_NAME']
    payload_raw = Path(os.environ['PAYLOAD_FILE']).read_text(encoding='utf-8')
    payload = json.loads(payload_raw or '{}')
    emit({
        'event': 'job_start',
        'job': job_name,
        'payload': payload,
        'started_at': datetime.now(timezone.utc).isoformat(),
    })
    try:
        result = await run_job(job_name, payload)
    except Exception as exc:
        emit({
            'event': 'job_failed',
            'job': job_name,
            'finished_at': datetime.now(timezone.utc).isoformat(),
            'error': repr(exc),
        })
        raise
    else:
        emit({
            'event': 'job_done',
            'job': job_name,
            'finished_at': datetime.now(timezone.utc).isoformat(),
            'result': result,
        })


try:
    asyncio.run(main())
finally:
    _log_fp.close()
PY"

echo "remote job log: ${REMOTE_ROOT}/${REMOTE_LOG}"
