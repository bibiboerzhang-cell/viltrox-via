"""批C:vkpi_kol_video_evidence.project_id 回填(2026-06-12)。

背景:417 行 evidence 的 project_id 为空(总量 1147),其中一部分可经
vkpi_project_kol_assignments(同 kol_pool_id)唯一定位到项目。

口径(无歧义铁则):
  - 仅回填"该 kol_pool_id 恰好只属于一个活跃项目"的行;
  - 活跃项目 = vkpi_projects.stage <> 'cancelled' 且 stage_status <> 'deleted'
    (软删路径 delete_project 落 stage='cancelled' + stage_status='deleted');
  - kol 跨多个活跃项目(歧义)或无任何活跃项目归属的行一律不动,保持诚实 NULL;
  - 幂等:UPDATE 带 AND project_id IS NULL 守卫,重跑不覆盖已有归属。

应用路径:走 app.db.connection(sqlite-compat 层,? 占位符),非裸 psql。

用法(仓库根目录执行;config 按 cwd 读根 .env 的 DATABASE_URL,本脚本已自动 chdir):
  PYTHONPATH=backend python3 backend/scripts_local/backfill_evidence_project_id.py            # dry-run(默认)
  PYTHONPATH=backend python3 backend/scripts_local/backfill_evidence_project_id.py --execute  # 实际写入(需主控裁决)
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
# app.core.config 在 import 时按 cwd 读 .env;DATABASE_URL 在仓库根 .env,不在 backend/.env
os.chdir(_REPO_ROOT)

from app.db.connection import db_connection_sync_scope, get_conn  # noqa: E402

PLAN_SQL = """
WITH active_kol_projects AS (
    SELECT a.kol_pool_id,
           COUNT(DISTINCT a.project_id) AS active_project_count,
           MIN(a.project_id) AS only_project_id
    FROM vkpi_project_kol_assignments a
    JOIN vkpi_projects p ON p.id = a.project_id
    WHERE p.stage <> 'cancelled' AND COALESCE(p.stage_status, '') <> 'deleted'
    GROUP BY a.kol_pool_id
)
SELECT e.id AS evidence_id,
       e.kol_pool_id,
       e.content_url,
       kp.active_project_count,
       kp.only_project_id,
       p.project_name
FROM vkpi_kol_video_evidence e
LEFT JOIN active_kol_projects kp ON kp.kol_pool_id = e.kol_pool_id
LEFT JOIN vkpi_projects p ON p.id = kp.only_project_id AND kp.active_project_count = 1
WHERE e.project_id IS NULL
ORDER BY e.id
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def main(execute: bool) -> None:
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(PLAN_SQL).fetchall()]
    unambiguous = [r for r in rows if (r.get("active_project_count") or 0) == 1 and r.get("only_project_id")]
    ambiguous = [r for r in rows if (r.get("active_project_count") or 0) > 1]
    unmapped = [r for r in rows if not (r.get("active_project_count") or 0)]

    mode = "EXECUTE" if execute else "DRY-RUN"
    print(f"[{mode}] null_project_id_rows={len(rows)}")
    print(f"[{mode}] backfillable_unambiguous={len(unambiguous)} (kol 恰好只属一个活跃项目)")
    print(f"[{mode}] skipped_ambiguous={len(ambiguous)} (kol 跨多个活跃项目,保持 NULL)")
    print(f"[{mode}] skipped_no_active_assignment={len(unmapped)} (无活跃项目归属,保持 NULL)")
    print(f"[{mode}] 样本(前 10 条将回填行):")
    for r in unambiguous[:10]:
        print(
            f"  evidence_id={r['evidence_id']} kol_pool_id={r['kol_pool_id']} "
            f"-> project_id={r['only_project_id']} ({r.get('project_name') or ''}) "
            f"url={(r.get('content_url') or '')[:60]}"
        )

    if not execute:
        print("[DRY-RUN] 未写库。确认后加 --execute 执行。")
        return

    now = utcnow()
    written = 0
    for r in unambiguous:
        cur = conn.execute(
            "UPDATE vkpi_kol_video_evidence SET project_id=?, updated_at=? WHERE id=? AND project_id IS NULL",
            (int(r["only_project_id"]), now, int(r["evidence_id"])),
        )
        written += int(getattr(cur, "rowcount", 0) or 0)
    conn.commit()
    remaining = dict(
        conn.execute("SELECT COUNT(*) AS c FROM vkpi_kol_video_evidence WHERE project_id IS NULL").fetchone()
    )["c"]
    print(f"[EXECUTE] written={written} remaining_null={remaining}")


if __name__ == "__main__":
    with db_connection_sync_scope():
        main("--execute" in sys.argv)
