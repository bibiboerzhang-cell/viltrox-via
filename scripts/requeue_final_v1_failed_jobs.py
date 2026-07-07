#!/usr/bin/env python3
"""Requeue retryable failed final_v1 jobs.

Default mode is dry-run. Use --commit to update jobs back to queued.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from stdout_utils import out  # noqa: E402

from app.core.config import DB_RUNTIME_URL  # noqa: E402


DERIVE_METHOD = "video_analysis_final_v1"
NON_RETRYABLE_MARKERS = (
    "unsupported_platform",
    "unsupported_media_derive_method",
    "unsupported_llm",
    "apify_not_configured",
    "provider_not_configured",
    "budget",
    "direct video exceeds",
    "too_large",
    "too large",
    "invalid_video_url",
    "not_video",
    "bad url",
    "unsupported",
)
RETRYABLE_MARKERS = (
    "stale_running_reclaimed",
    "gemini_call_timeout",
    "media_resolve_timeout",
    "timeout",
    "timed out",
    "503",
    "502",
    "504",
    "429",
    "service unavailable",
    "temporary",
    "connection",
    "connection reset",
    "read operation timed out",
    "upload failed",
    "files.get",
    "processing failed",
)


def _retry_reason(error: str) -> tuple[bool, str]:
    text = str(error or "").lower()
    for marker in NON_RETRYABLE_MARKERS:
        if marker in text:
            return False, f"non_retryable:{marker}"
    for marker in RETRYABLE_MARKERS:
        if marker in text:
            return True, f"retryable:{marker}"
    return False, "non_retryable:unclassified"


def _failed_jobs(conn: psycopg.Connection[Any], max_attempts: int) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, status, attempts, payload, last_error, updated_at
            FROM apify_jobs
            WHERE payload->>'derive_method'=%(derive_method)s
              AND status='failed'
              AND attempts < %(max_attempts)s
            ORDER BY updated_at, id
            """,
            {"derive_method": DERIVE_METHOD, "max_attempts": max(1, int(max_attempts))},
        )
        return [dict(row) for row in cur.fetchall()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run or requeue retryable failed final_v1 jobs.")
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    if not DB_RUNTIME_URL:
        raise SystemExit("DATABASE_URL is required")
    with psycopg.connect(DB_RUNTIME_URL) as conn:
        rows = _failed_jobs(conn, max_attempts=args.max_attempts)
        candidates: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for row in rows:
            allowed, reason = _retry_reason(str(row.get("last_error") or ""))
            item = {
                "id": row["id"],
                "target_id": (row.get("payload") or {}).get("target_id") if isinstance(row.get("payload"), dict) else "",
                "platform": (row.get("payload") or {}).get("platform_by_host") if isinstance(row.get("payload"), dict) else "",
                "attempts": row.get("attempts"),
                "reason": reason,
                "last_error": str(row.get("last_error") or "")[:300],
            }
            if allowed:
                candidates.append(item)
            else:
                skipped.append(item)
        if args.commit and candidates:
            with conn.transaction():
                with conn.cursor() as cur:
                    for item in candidates:
                        cur.execute(
                            """
                            UPDATE apify_jobs
                            SET status='queued',
                                last_error=%s,
                                updated_at=NOW()
                            WHERE id=%s AND status='failed' AND attempts < %s
                            """,
                            (f"requeued_retryable_final_v1:{item['reason']}", item["id"], max(1, int(args.max_attempts))),
                        )
    out(f"mode: {'commit' if args.commit else 'dry-run'}")
    out(f"max_attempts: {max(1, int(args.max_attempts))}")
    out(f"requeue_candidates: {len(candidates)}")
    for item in candidates:
        out(f"REQUEUE\t{item['id']}\t{item['target_id']}\t{item['platform']}\tattempts={item['attempts']}\t{item['reason']}")
    out(f"skipped_failed: {len(skipped)}")
    for item in skipped:
        out(f"SKIP\t{item['id']}\t{item['target_id']}\t{item['platform']}\tattempts={item['attempts']}\t{item['reason']}")


if __name__ == "__main__":
    main()
