"""Seed vkpi_kol_profile_deep with one row per vkpi_kol_pool entry.

Fills only kol_pool_id / kol_entity_uid / platform / handle.
dimensions_11_json stays NULL and is filled by the P5.2B backfill.

Usage:
    python -m scripts.vkpi_seed_profile_deep_from_pool
    python -m scripts.vkpi_seed_profile_deep_from_pool --all
    python -m scripts.vkpi_seed_profile_deep_from_pool --source-type legacy_excel_p2d
    python -m scripts.vkpi_seed_profile_deep_from_pool --dry-run
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime, get_conn  # noqa: E402


def _row_dict(row: Any) -> dict[str, Any]:
    try:
        return dict(row)
    except Exception:
        return {
            "id": row[0],
            "pool_uid": row[1],
            "platform": row[2],
            "handle": row[3],
            "source_type": row[4],
        }


def seed(
    source_type: str | None = "legacy_excel_p2d",
    limit: int = 2000,
    dry_run: bool = False,
) -> dict[str, Any]:
    conn = get_conn()
    safe_limit = max(1, min(int(limit or 2000), 10000))

    if source_type:
        rows = conn.execute(
            """
            SELECT id, pool_uid, platform, handle, source_type
            FROM vkpi_kol_pool
            WHERE source_type = ?
            ORDER BY id
            LIMIT ?
            """,
            (source_type, safe_limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, pool_uid, platform, handle, source_type
            FROM vkpi_kol_pool
            ORDER BY id
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()

    items = [_row_dict(row) for row in rows]
    if dry_run:
        return {
            "dry_run": True,
            "source_type": source_type or "all",
            "limit": safe_limit,
            "would_process": len(items),
            "sample": items[:3],
            "dimensions_11_json": "NULL",
        }

    inserted = 0
    skipped = 0
    errors: list[dict[str, Any]] = []

    for row in items:
        try:
            cursor = conn.execute(
                """
                INSERT INTO vkpi_kol_profile_deep
                    (kol_pool_id, kol_entity_uid, platform, handle)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (kol_pool_id) DO NOTHING
                """,
                (row["id"], row["pool_uid"], row["platform"], row["handle"]),
            )
            if int(getattr(cursor, "rowcount", 0) or 0) > 0:
                inserted += 1
            else:
                skipped += 1
        except Exception as exc:
            errors.append({"kol_pool_id": row.get("id"), "error": str(exc)})

    conn.commit()
    return {
        "dry_run": False,
        "source_type": source_type or "all",
        "limit": safe_limit,
        "total_pool_rows": len(items),
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors[:5],
        "error_count": len(errors),
        "dimensions_11_json": "NULL",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-type",
        default="legacy_excel_p2d",
        help="KOL pool source_type filter. Default: legacy_excel_p2d.",
    )
    parser.add_argument("--all", action="store_true", help="Seed all source types.")
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        source_type = None if args.all else args.source_type
        result = seed(source_type=source_type, limit=args.limit, dry_run=args.dry_run)
        stdout_out(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0 if not result.get("error_count") else 1
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
