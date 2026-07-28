#!/usr/bin/env bash
set -euo pipefail

SSH_TARGET="${SSH_TARGET:-viltrox}"
REMOTE_ROOT="${REMOTE_ROOT:-/opt/viltrox-2.0}"
PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"

ssh "${SSH_TARGET}" "cd '${REMOTE_ROOT}' && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend '${PYTHON_BIN}' -B - <<'PY'
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
        'active': count('vkpi_employee_channels', \"deleted_at IS NULL AND status='active'\"),
    },
    'kol_pool': {
        'total': count('vkpi_kol_pool'),
        'legacy_excel_p2d': count('vkpi_kol_pool', \"source_type='legacy_excel_p2d'\"),
        'needs_human_review': count('vkpi_kol_pool', \"sync_status='needs_human_review'\"),
        'imported': count('vkpi_kol_pool', \"sync_status='imported'\"),
    },
    'active_jobs': {
        'ledger': count('job_execution_ledger', \"status IN ('queued','retrying','processing','running')\"),
        'items': count('vkpi_async_task_items', \"status IN ('pending','running')\"),
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
PY"
