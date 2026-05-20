#!/usr/bin/env bash
set -euo pipefail

if [ "${CONFIRM:-}" != "RESET_OFFICIAL_BASELINE_DELTAS" ]; then
  echo "Refusing to reset deltas without CONFIRM=RESET_OFFICIAL_BASELINE_DELTAS" >&2
  exit 1
fi

SSH_TARGET="${SSH_TARGET:-viltrox}"
REMOTE_ROOT="${REMOTE_ROOT:-/opt/viltrox-2.0}"
PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
STAMP="${STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"
SNAPSHOT_DATE="${SNAPSHOT_DATE:-$(date -u +%F)}"
LOCAL_DIR="${LOCAL_DIR:-runtime/prod-sync/${STAMP}-baseline-reset}"

mkdir -p "${LOCAL_DIR}"

ssh "${SSH_TARGET}" "cd '${REMOTE_ROOT}' && STAMP='${STAMP}' SNAPSHOT_DATE='${SNAPSHOT_DATE}' PYTHONPATH=backend '${PYTHON_BIN}' - <<'PY'
import json
import os
from datetime import datetime
from pathlib import Path

from app.db.connection import get_conn

stamp = os.environ['STAMP']
snapshot_date = os.environ['SNAPSHOT_DATE']
backup_path = Path('runtime/ops') / f'{stamp}-official-baseline-delta-reset-before.json'
backup_path.parent.mkdir(parents=True, exist_ok=True)

conn = get_conn()
rows = conn.execute(
    '''
    SELECT
      c.id AS channel_id,
      c.platform,
      c.account_handle,
      m.id AS metric_id,
      m.snapshot_date,
      m.followers_delta,
      m.posts_delta,
      m.views_delta_24h,
      m.likes_delta_24h,
      m.followers,
      m.posts_count,
      m.total_views,
      m.total_likes,
      m.captured_at
    FROM vkpi_employee_channels c
    JOIN vkpi_channel_metrics m ON m.channel_id = c.id
    WHERE c.deleted_at IS NULL
      AND c.status = 'active'
      AND m.snapshot_date = ?
    ORDER BY c.platform, c.account_handle
    ''',
    (snapshot_date,),
).fetchall()

payload = [dict(row) for row in rows]
backup_path.write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2), encoding='utf-8')

before = {
    'rows': len(payload),
    'posts_delta': sum(int(item.get('posts_delta') or 0) for item in payload),
    'views_delta_24h': sum(int(item.get('views_delta_24h') or 0) for item in payload),
    'likes_delta_24h': sum(int(item.get('likes_delta_24h') or 0) for item in payload),
}

metric_ids = [int(item['metric_id']) for item in payload if item.get('metric_id')]
if metric_ids:
    placeholders = ','.join('?' for _ in metric_ids)
    conn.execute(
        f'''
        UPDATE vkpi_channel_metrics
        SET posts_delta = 0,
            views_delta_24h = 0,
            likes_delta_24h = 0
        WHERE id IN ({placeholders})
        ''',
        tuple(metric_ids),
    )
    now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    detail = f'official_full_baseline delta baseline reset for {snapshot_date}; backup={backup_path}'
    for item in payload:
        conn.execute(
            'INSERT INTO vkpi_channel_audit (channel_id, staff_id, action, detail, occurred_at) VALUES (?,?,?,?,?)',
            (int(item['channel_id']), None, 'baseline_delta_reset', detail, now),
        )
    conn.commit()

after_row = conn.execute(
    '''
    SELECT COUNT(*) AS rows,
           SUM(posts_delta) AS posts_delta,
           SUM(views_delta_24h) AS views_delta_24h,
           SUM(likes_delta_24h) AS likes_delta_24h
    FROM vkpi_channel_metrics
    WHERE snapshot_date = ?
      AND channel_id IN (SELECT id FROM vkpi_employee_channels WHERE deleted_at IS NULL AND status='active')
    ''',
    (snapshot_date,),
).fetchone()

print(json.dumps({
    'snapshot_date': snapshot_date,
    'backup_path': str(backup_path),
    'before': before,
    'after': dict(after_row),
}, ensure_ascii=False, default=str, indent=2))
PY"

scp -q "${SSH_TARGET}:${REMOTE_ROOT}/runtime/ops/${STAMP}-official-baseline-delta-reset-before.json" "${LOCAL_DIR}/"
echo "baseline delta reset backup downloaded: ${LOCAL_DIR}/${STAMP}-official-baseline-delta-reset-before.json"
