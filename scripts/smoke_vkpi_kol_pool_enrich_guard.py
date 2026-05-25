"""P3.6A smoke: KOL Pool 单条补齐入口不 500,状态可解释,数据可清理。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from _smoke_seed import cleanup_admin, seed_admin
except ImportError:  # pragma: no cover
    seed_admin = None
    cleanup_admin = None

from app.db.connection import get_conn
from app.domains.kol import pool as kol_pool

MARKER = f"vkpi-pool-enrich-{int(time.time())}"


def main() -> None:
    conn = get_conn()
    user_id = 0
    staff_id = 0
    if not seed_admin:
        print("_smoke_seed.py missing")
        sys.exit(1)
    user_id, staff_id = seed_admin(conn, marker=MARKER)
    failures: list[str] = []
    try:
        imported = kol_pool.import_items(
            [
                {
                    "platform": "other",
                    "handle": MARKER,
                    "display_name": "P36A Enrich Guard",
                    "followers": 0,
                }
            ],
            source_type="smoke",
            source_ref=MARKER,
            staff={"id": staff_id, "is_owner": True, "role": "admin"},
        )
        rows = imported.get("items") or []
        if len(rows) != 1:
            failures.append(f"导入测试候选失败: {imported}")
        else:
            result = kol_pool.enrich_item(int(rows[0]["id"]), max_posts=1, staff={"id": staff_id, "role": "admin"})
            status = str(result.get("sync_status") or "")
            if status not in {"unsupported", "not_configured", "synced", "blocked", "failed", "error"}:
                failures.append(f"补齐返回状态不可解释: {result}")
            if not result.get("item") or int(result["item"].get("id") or 0) != int(rows[0]["id"]):
                failures.append(f"补齐返回 item 错误: {result}")
            updated = conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(rows[0]["id"]),)).fetchone()
            if not updated:
                failures.append("补齐后测试候选丢失")
            print(f"[enrich] status={status}")
    finally:
        conn.execute("DELETE FROM vkpi_kol_pool WHERE source_ref=? OR handle=?", (MARKER, MARKER))
        conn.commit()
        if cleanup_admin:
            cleanup_admin(conn, user_id=user_id, staff_id=staff_id)

    if failures:
        print("=== FAIL ===")
        for failure in failures:
            print(f"- {failure}")
        sys.exit(1)
    print("VKPI_KOL_POOL_ENRICH_GUARD_SMOKE_OK")


if __name__ == "__main__":
    main()
