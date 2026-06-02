#!/usr/bin/env python3
"""Plan or enqueue video_analysis_final_v1 jobs.

Default mode is dry-run. Use --commit to insert apify_jobs rows.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from app.core.config import DB_RUNTIME_URL  # noqa: E402


DERIVE_METHOD = "video_analysis_final_v1"
DEFAULT_RECENT_CUTOFF = "2026-03-01"
SUPPORTED_PLATFORMS = {"youtube", "instagram", "tiktok"}


def _platform_from_url(url: str) -> str:
    text = str(url or "").lower()
    if "youtube.com" in text or "youtu.be" in text:
        return "youtube"
    if "instagram.com" in text:
        return "instagram"
    if "tiktok.com" in text:
        return "tiktok"
    return "unsupported"


def _explicit_video_url(url: str, platform: str) -> bool:
    text = str(url or "").lower()
    if platform == "youtube":
        return "watch?v=" in text or "/shorts/" in text or "youtu.be/" in text
    if platform == "instagram":
        return "/reel/" in text
    if platform == "tiktok":
        return "/video/" in text
    return False


def _has_video_signal(row: dict[str, Any], platform: str) -> bool:
    view_count = row.get("view_count")
    duration_seconds = row.get("duration_seconds")
    if view_count is not None:
        return True
    try:
        if duration_seconds is not None and int(duration_seconds) > 0:
            return True
    except (TypeError, ValueError):
        pass
    return _explicit_video_url(str(row.get("content_url") or ""), platform)


def _target_rows(conn: psycopg.Connection[Any], *, batch: str, cutoff: str) -> list[dict[str, Any]]:
    if batch == "recent":
        where = "e.publish_date >= %(cutoff)s::timestamptz"
    else:
        where = "(e.publish_date < %(cutoff)s::timestamptz OR e.publish_date IS NULL)"
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            WITH jobs AS (
              SELECT
                payload->>'target_id' AS target_id,
                array_agg(status ORDER BY updated_at DESC, id DESC) AS statuses
              FROM apify_jobs
              WHERE payload->>'target_type'='video'
                AND payload->>'derive_method'=%(derive_method)s
              GROUP BY payload->>'target_id'
            )
            SELECT
              e.id AS target_id,
              e.content_url,
              e.platform AS evidence_platform,
              e.publish_date,
              e.view_count,
              e.duration_seconds,
              COALESCE(kp.handle, kp.display_name, e.channel_name, '') AS kol,
              COALESCE(e.title, e.video_title, '') AS title,
              c.id AS cache_id,
              jobs.statuses AS existing_job_statuses
            FROM vkpi_kol_video_evidence e
            LEFT JOIN vkpi_kol_pool kp ON kp.id=e.kol_pool_id
            LEFT JOIN vkpi_analysis_cache c
              ON c.target_type='video'
             AND c.target_id=e.id::text
             AND c.derive_method=%(derive_method)s
             AND c.status='ready'
            LEFT JOIN jobs ON jobs.target_id=e.id::text
            WHERE e.content_url IS NOT NULL
              AND e.content_url <> ''
              AND {where}
            ORDER BY e.publish_date DESC NULLS LAST, e.id ASC
            """,
            {"derive_method": DERIVE_METHOD, "cutoff": cutoff},
        )
        rows = [dict(row) for row in cur.fetchall()]
    for row in rows:
        row["platform_by_host"] = _platform_from_url(str(row.get("content_url") or ""))
    return rows


def _classify(rows: list[dict[str, Any]], *, batch: str, triggered_by_user_id: int | None, limit: int | None) -> dict[str, Any]:
    planned: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    eligible_video: list[dict[str, Any]] = []
    filtered_non_video: list[dict[str, Any]] = []
    for row in rows:
        platform = str(row.get("platform_by_host") or "unsupported")
        reason = ""
        if platform not in SUPPORTED_PLATFORMS:
            reason = "unsupported_platform"
        elif not _has_video_signal(row, platform):
            reason = "non_video_post_no_video_signal"
        if reason:
            skipped_item = {**row, "skip_reason": reason}
            skipped.append(skipped_item)
            if reason == "non_video_post_no_video_signal":
                filtered_non_video.append(skipped_item)
            continue
        eligible_video.append(row)
        if row.get("cache_id"):
            reason = "existing_ready_cache"
        elif row.get("existing_job_statuses"):
            reason = "existing_final_v1_job"
        if reason:
            skipped.append({**row, "skip_reason": reason})
            continue
        payload = {
            "target_type": "video",
            "target_id": str(row["target_id"]),
            "derive_method": DERIVE_METHOD,
            "platform": platform,
            "platform_by_host": platform,
            "batch": batch,
            "triggered_by_user_id": triggered_by_user_id,
            "prompt": f"final_v1 {batch} video:{row['target_id']} {platform}",
        }
        planned.append({**row, "payload": payload})
    if limit is not None:
        planned = planned[: max(0, int(limit))]
    return {"planned": planned, "skipped": skipped, "eligible_video": eligible_video, "filtered_non_video": filtered_non_video}


def _insert_jobs(conn: psycopg.Connection[Any], planned: list[dict[str, Any]]) -> list[dict[str, Any]]:
    inserted: list[dict[str, Any]] = []
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            for item in planned:
                cur.execute(
                    """
                    INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
                    VALUES ('video', %s::jsonb, 'queued', NOW(), NOW())
                    RETURNING id, status, created_at
                    """,
                    (json.dumps(item["payload"], ensure_ascii=False),),
                )
                row = dict(cur.fetchone() or {})
                inserted.append({"target_id": item["target_id"], "job_id": row.get("id"), "status": row.get("status")})
    return inserted


def _print_report(*, batch: str, commit: bool, classified: dict[str, Any], inserted: list[dict[str, Any]] | None = None) -> None:
    planned = classified["planned"]
    skipped = classified["skipped"]
    eligible_video = classified["eligible_video"]
    filtered_non_video = classified["filtered_non_video"]
    planned_platforms = Counter(str(item.get("platform_by_host") or "unknown") for item in planned)
    eligible_platforms = Counter(str(item.get("platform_by_host") or "unknown") for item in eligible_video)
    skipped_reasons = Counter(str(item.get("skip_reason") or "unknown") for item in skipped)
    print(f"mode: {'commit' if commit else 'dry-run'}")
    print(f"batch: {batch}")
    print(f"derive_method: {DERIVE_METHOD}")
    print(f"eligible_video_count: {len(eligible_video)}")
    print("eligible_video_platforms:", dict(sorted(eligible_platforms.items())))
    print(f"filtered_non_video_count: {len(filtered_non_video)}")
    print("filtered_non_video_target_ids:", [str(item["target_id"]) for item in filtered_non_video])
    print(f"planned_count: {len(planned)}")
    print("planned_platforms:", dict(sorted(planned_platforms.items())))
    print("skipped_reasons:", dict(sorted(skipped_reasons.items())))
    if inserted is not None:
        print(f"inserted_jobs: {len(inserted)}")
    print("\nplanned:")
    for item in planned:
        print(
            f"{item['target_id']}\t{item['platform_by_host']}\t{item.get('kol') or '-'}\t"
            f"{item.get('publish_date') or '-'}\t{str(item.get('title') or '')[:120]}"
        )
    print("\nskipped:")
    for item in skipped:
        print(
            f"{item['target_id']}\t{item['platform_by_host']}\t{item.get('skip_reason')}\t"
            f"{item.get('kol') or '-'}\t{item.get('publish_date') or '-'}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run or enqueue final_v1 video analysis jobs.")
    parser.add_argument("--batch", choices=["recent", "remaining"], required=True)
    parser.add_argument("--cutoff", default=DEFAULT_RECENT_CUTOFF)
    parser.add_argument("--triggered-by-user-id", type=int, default=1)
    parser.add_argument("--limit", type=int, default=None, help="Limit planned inserts, useful for smoke validation.")
    parser.add_argument("--commit", action="store_true", help="Insert jobs. Omit for dry-run.")
    args = parser.parse_args()
    if not DB_RUNTIME_URL:
        raise SystemExit("DATABASE_URL is required")
    with psycopg.connect(DB_RUNTIME_URL) as conn:
        rows = _target_rows(conn, batch=args.batch, cutoff=args.cutoff)
        classified = _classify(rows, batch=args.batch, triggered_by_user_id=args.triggered_by_user_id, limit=args.limit)
        inserted = _insert_jobs(conn, classified["planned"]) if args.commit else None
    _print_report(batch=args.batch, commit=bool(args.commit), classified=classified, inserted=inserted)


if __name__ == "__main__":
    main()
