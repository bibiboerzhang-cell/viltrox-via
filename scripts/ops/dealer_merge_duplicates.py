#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Soft-merge exact duplicate dealers (identical normalized name+city).

2026-07-18 dedup audit found variant-name duplicates that the name+address
exact upsert cannot catch (four Adorama rows, doubled Berger Bros, canon
inserts shadowing nikon rows, ...).  This tool handles only the SAFE tier:
rows whose normalized ``name`` AND ``city`` are byte-identical after
stripping punctuation.  Variant-name pairs stay untouched — they go to the
human-confirm list.

Merge semantics (reversible, FK-safe):
  * keeper = contract row (review_contract_version desc) then lowest id;
  * brand relationships copied to keeper (ON CONFLICT DO NOTHING);
  * web-verification receipts repointed to keeper;
  * duplicate row is NOT deleted — ``publication_status='draft'``(白名单仅 draft|published)
    plus a note naming the keeper, so map/rankings drop it but history and
    foreign keys stay intact.

Dry-run by default; ``--apply`` writes.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from stdout_utils import out as stdout_out  # noqa: E402


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write merges (default: dry-run)")
    args = parser.parse_args(argv)

    from app.db.connection import get_conn, is_postgres_runtime

    if not is_postgres_runtime():
        stdout_out("error: merge requires the PostgreSQL runtime", file=sys.stderr)
        return 2
    conn = get_conn()
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT id, name, city, COALESCE(review_contract_version,0) AS v "
            "FROM vkpi_dealers WHERE publication_status='published'"
        ).fetchall()
    ]
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        groups.setdefault((_norm(row["name"]), _norm(row["city"])), []).append(row)

    merged = 0
    for _key, group in sorted(groups.items()):
        if len(group) < 2:
            continue
        group.sort(key=lambda r: (-int(r["v"]), int(r["id"])))
        keeper, dupes = group[0], group[1:]
        for dupe in dupes:
            stdout_out(
                f"{'merge' if args.apply else 'would merge'} "
                f"#{dupe['id']} {dupe['name'][:40]} -> keeper #{keeper['id']}"
            )
            merged += 1
            if not args.apply:
                continue
            for raw in conn.execute(
                "SELECT brand_key, evidence_url, source_checked_at, reviewer_id "
                "FROM vkpi_dealer_brand_relationships WHERE dealer_id=?",
                (dupe["id"],),
            ).fetchall():
                brand = dict(raw)
                conn.execute(
                    """
                    INSERT INTO vkpi_dealer_brand_relationships(
                      dealer_id,brand_key,relationship_status,authorization_status,
                      evidence_url,source_checked_at,reviewer_id,updated_at
                    ) VALUES (?,?, 'official_directory_listed','unverified',?,?,?,NOW())
                    ON CONFLICT (dealer_id,brand_key) DO NOTHING
                    """,
                    (
                        keeper["id"],
                        brand["brand_key"],
                        brand["evidence_url"],
                        brand["source_checked_at"],
                        brand["reviewer_id"],
                    ),
                )
            conn.execute(
                "UPDATE vkpi_dealer_web_verification SET dealer_id=? WHERE dealer_id=?",
                (keeper["id"], dupe["id"]),
            )
            # 2026-07-18 bug 猎杀修:host 活动关联也要改挂 keeper,否则副本置
            # draft 后从地图消失,活动卡的 host dealer 变成悬挂引用。
            # keeper 已有同 opportunity 关联时跳过(避免主键冲突)。
            conn.execute(
                """
                UPDATE vkpi_event_opportunity_dealers od
                SET dealer_id=?
                WHERE od.dealer_id=?
                  AND NOT EXISTS (
                    SELECT 1 FROM vkpi_event_opportunity_dealers k
                    WHERE k.opportunity_id=od.opportunity_id
                      AND k.organization_id=od.organization_id
                      AND k.dealer_id=?
                  )
                """,
                (keeper["id"], dupe["id"], keeper["id"]),
            )
            conn.execute(
                "UPDATE vkpi_dealers SET publication_status='draft', verification_note=? WHERE id=?",
                (
                    f"merged into dealer #{keeper['id']} (exact name+city duplicate, dedup audit 2026-07-18)",
                    dupe["id"],
                ),
            )
    if args.apply:
        conn.commit()
    stdout_out(f"{'merged' if args.apply else 'would merge'}={merged}")
    remaining = conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_dealers WHERE publication_status='published'"
    ).fetchone()
    stdout_out(f"published_now={dict(remaining)['n'] if hasattr(remaining, 'keys') else remaining[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
