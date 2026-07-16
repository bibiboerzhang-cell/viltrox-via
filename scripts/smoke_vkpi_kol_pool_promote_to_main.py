"""P3.6F smoke: KOL Pool candidate can be promoted to the main kols table.

This is intentionally marker-scoped and cleans up both vkpi_kol_pool and kols.
"""
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
    cleanup_admin = None
    seed_admin = None

from app.db.connection import get_conn
from app.domains.kol import pool as kol_pool


MARKER = f"vkpi-pool-promote-{int(time.time())}"


def main() -> None:
    if not seed_admin:
        out("missing _smoke_seed.py")
        sys.exit(1)

    conn = get_conn()
    user_id, staff_id = seed_admin(conn, marker=MARKER)
    failures: list[str] = []
    main_kol_id = 0

    try:
        handle = f"{MARKER}-creator"
        imported = kol_pool.import_items(
            [
                {
                    "platform": "instagram",
                    "username": handle,
                    "fullName": "P36F Promote Creator",
                    "profileUrl": f"https://www.instagram.com/{handle}/",
                    "profilePicUrl": "https://example.com/avatar.jpg",
                    "followersCount": 123456,
                    "averageViews": 7890,
                    "engagementRate": 4.2,
                    "publicEmail": f"{handle}@example.com",
                    "biography": "Camera creator smoke row",
                }
            ],
            source_type="smoke",
            source_ref=MARKER,
            platform="instagram",
            staff={"id": staff_id, "is_owner": True},
        )
        item = (imported.get("items") or [None])[0]
        if not item:
            failures.append("import did not return a pool item")
            raise AssertionError("pool import failed")

        pool_id = int(item["id"])
        candidates_before = kol_pool.main_candidates(pool_id, limit=5)
        if candidates_before.get("candidates"):
            failures.append("fresh smoke row unexpectedly matched existing main kols row")

        promoted = kol_pool.promote_to_main(pool_id, staff={"id": staff_id, "is_owner": True})
        main_kol_id = int(promoted.get("main_kol_id") or 0)
        if not promoted.get("linked") or promoted.get("mode") != "created" or not main_kol_id:
            failures.append(f"promote expected created+linked, got {promoted}")

        pool_row = conn.execute("SELECT linked_main_kol_id FROM vkpi_kol_pool WHERE id=?", (pool_id,)).fetchone()
        if not pool_row or int(pool_row["linked_main_kol_id"] or 0) != main_kol_id:
            failures.append("pool row linked_main_kol_id was not updated")

        main_row = conn.execute("SELECT * FROM kols WHERE id=?", (main_kol_id,)).fetchone()
        if not main_row:
            failures.append("created main kols row not found")
        else:
            main = dict(main_row)
            if main.get("platform") != "instagram":
                failures.append(f"main row platform wrong: {main.get('platform')}")
            if int(main.get("follower_count") or 0) != 123456:
                failures.append(f"main row follower_count wrong: {main.get('follower_count')}")

        promoted_again = kol_pool.promote_to_main(pool_id, staff={"id": staff_id, "is_owner": True})
        if promoted_again.get("mode") != "already_linked" or int(promoted_again.get("main_kol_id") or 0) != main_kol_id:
            failures.append(f"second promote should be idempotent, got {promoted_again}")

        duplicate_count = conn.execute(
            "SELECT COUNT(*) AS n FROM kols WHERE notes LIKE ?",
            (f"%{MARKER}%",),
        ).fetchone()
        if int(duplicate_count["n"] if duplicate_count else 0) != 1:
            failures.append("second promote created duplicate main rows")
    finally:
        cleanup(conn, marker=MARKER, main_kol_id=main_kol_id)
        if cleanup_admin:
            cleanup_admin(conn, user_id=user_id, staff_id=staff_id)

    if failures:
        out("=== FAIL ===")
        for failure in failures:
            out(f"- {failure}")
        sys.exit(1)

    out("VKPI_KOL_POOL_PROMOTE_TO_MAIN_SMOKE_OK")


def cleanup(conn, *, marker: str, main_kol_id: int) -> None:
    if main_kol_id:
        conn.execute("DELETE FROM kols WHERE id=?", (int(main_kol_id),))
    conn.execute("DELETE FROM kols WHERE notes LIKE ?", (f"%{marker}%",))
    conn.execute("DELETE FROM vkpi_kol_pool WHERE source_ref=?", (marker,))
    conn.commit()


if __name__ == "__main__":
    main()
