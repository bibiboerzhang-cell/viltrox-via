#!/usr/bin/env bash
set -euo pipefail

SSH_TARGET="${SSH_TARGET:-viltrox}"
REMOTE_ROOT="${REMOTE_ROOT:-/opt/viltrox-2.0}"
PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"

ssh "${SSH_TARGET}" "env REMOTE_ROOT='${REMOTE_ROOT}' PYTHON_BIN='${PYTHON_BIN}' bash -s" <<'REMOTE'
set -euo pipefail

cd "${REMOTE_ROOT}"
remote_root_path="$(pwd -P)"
current_link="${remote_root_path}/current"
releases_dir="${remote_root_path}/releases"
if [ ! -L "${current_link}" ]; then
  echo "production audit requires the atomic current symlink" >&2
  exit 1
fi
current_path="$(readlink -f -- "${current_link}" 2>/dev/null || true)"
if [ -z "${current_path}" ] || [ ! -d "${current_path}" ] \
  || [ "$(dirname -- "${current_path}")" != "${releases_dir}" ]; then
  echo "production audit current pointer is unresolved or escapes releases" >&2
  exit 1
fi
release_id="${current_path##*/}"
if ! [[ "${release_id}" =~ ^[A-Za-z0-9_.-]+$ ]] || [ "${release_id}" = "." ] || [ "${release_id}" = ".." ]; then
  echo "production audit current release id is invalid" >&2
  exit 1
fi
if [ ! -d "${current_path}/backend" ] || [ -L "${current_path}/backend" ] \
  || [ ! -f "${current_path}/BUILD_GIT_SHA" ] || [ -L "${current_path}/BUILD_GIT_SHA" ] \
  || [ ! -f "${current_path}/.vkpi-release.json" ] || [ -L "${current_path}/.vkpi-release.json" ]; then
  echo "production audit current release evidence is missing or unsafe" >&2
  exit 1
fi
case "${PYTHON_BIN}" in
  /*) remote_python="${PYTHON_BIN}" ;;
  *) remote_python="${remote_root_path}/${PYTHON_BIN}" ;;
esac
if [ ! -x "${remote_python}" ]; then
  echo "production audit Python runtime is unavailable" >&2
  exit 1
fi
if ! PYTHONDONTWRITEBYTECODE=1 "${remote_python}" -B - \
  "${current_path}/BUILD_GIT_SHA" \
  "${current_path}/.vkpi-release.json" \
  "${release_id}" <<'PY_VERIFY'
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

try:
    build_sha = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
    manifest = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(1) from exc
if (
    not re.fullmatch(r"[0-9a-fA-F]{40}", build_sha)
    or not isinstance(manifest, dict)
    or manifest.get("schema") != 2
    or manifest.get("release_id") != sys.argv[3]
    or manifest.get("git_sha") != build_sha
):
    raise SystemExit(1)
PY_VERIFY
then
  echo "production audit current release build evidence is invalid" >&2
  exit 1
fi

cd "${current_path}"
LOCAL_ENV_FILE="${remote_root_path}/.env" \
RUNTIME_ROOT="${remote_root_path}/runtime" \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="${current_path}/backend" \
"${remote_python}" -B - <<'PY'
import json
from app.db.connection import get_conn

conn = get_conn()

def count(table, where='1=1'):
    try:
        return int(conn.execute(f'SELECT COUNT(*) AS n FROM {table} WHERE {where}').fetchone()['n'] or 0)
    except Exception as exc:
        return {'error': str(exc)}

payload = {
    'official_channels': {
        'total': count('vkpi_employee_channels', 'deleted_at IS NULL'),
        'active': count('vkpi_employee_channels', "deleted_at IS NULL AND status='active'"),
    },
    'kol_pool': {
        'total': count('vkpi_kol_pool'),
        'legacy_excel_p2d': count('vkpi_kol_pool', "source_type='legacy_excel_p2d'"),
        'needs_human_review': count('vkpi_kol_pool', "sync_status='needs_human_review'"),
        'imported': count('vkpi_kol_pool', "sync_status='imported'"),
    },
    'active_jobs': {
        'ledger': count('job_execution_ledger', "status IN ('queued','retrying','processing','running')"),
        'items': count('vkpi_async_task_items', "status IN ('pending','running')"),
    },
}

try:
    rows = conn.execute('''
        SELECT snapshot_date, COUNT(*) AS n, SUM(followers) AS followers,
               SUM(posts_count) AS posts, SUM(total_views) AS views,
               SUM(followers_delta) AS followers_delta,
               SUM(posts_delta) AS posts_delta,
               SUM(views_delta_24h) AS views_delta
        FROM vkpi_channel_metrics
        GROUP BY snapshot_date
        ORDER BY snapshot_date DESC
        LIMIT 5
    ''').fetchall()
    payload['latest_channel_metric_dates'] = [dict(row) for row in rows]
except Exception as exc:
    payload['latest_channel_metric_dates'] = {'error': str(exc)}

print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
PY
REMOTE
