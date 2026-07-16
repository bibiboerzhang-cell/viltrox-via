"""P3.6C smoke: KOL Pool batch enrich is bounded and explainable."""
from __future__ import annotations

from stdout_utils import out

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

MARKER = f"vkpi-pool-batch-{int(time.time())}"


def main() -> None:
    conn = get_conn()
    if not seed_admin:
        out("_smoke_seed.py missing")
        sys.exit(1)
    user_id, staff_id = seed_admin(conn, marker=MARKER)
    failures: list[str] = []
    try:
        imported = kol_pool.import_items(
            [
                {
                    "platform": "other",
                    "handle": f"{MARKER}-{idx}",
                    "display_name": f"P36C Batch Guard {idx}",
                    "followers": idx,
                }
                for idx in range(7)
            ],
            source_type="smoke",
            source_ref=MARKER,
            staff={"id": staff_id, "is_owner": True, "role": "admin"},
        )
        rows = imported.get("items") or []
        if len(rows) != 7:
            failures.append(f"expected 7 imported rows, got {len(rows)}: {imported}")
        ids = [int(row["id"]) for row in rows]
        result = kol_pool.batch_enrich_items(
            ids=ids,
            limit=7,
            max_posts=2,
            staff={"id": staff_id, "is_owner": True, "role": "admin"},
        )
        if result.get("attempted") != 5:
            failures.append(f"batch cap failed, expected attempted=5: {result}")
        if not result.get("capped"):
            failures.append(f"batch should report capped=true: {result}")
        skipped = result.get("skipped") or []
        if len(skipped) != 5:
            failures.append(f"unsupported rows should be skipped, got {len(skipped)}: {result}")
        if result.get("errors"):
            failures.append(f"unsupported rows should not raise errors: {result}")
        out(f"[batch] attempted={result.get('attempted')} skipped={len(skipped)} capped={result.get('capped')}")
    finally:
        conn.execute("DELETE FROM vkpi_kol_pool WHERE source_ref=? OR handle LIKE ?", (MARKER, f"{MARKER}%"))
        conn.commit()
        if cleanup_admin:
            cleanup_admin(conn, user_id=user_id, staff_id=staff_id)

    if failures:
        out("=== FAIL ===")
        for failure in failures:
            out(f"- {failure}")
        sys.exit(1)
    out("VKPI_KOL_POOL_BATCH_ENRICH_GUARD_SMOKE_OK")


if __name__ == "__main__":
    main()
