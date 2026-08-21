#!/usr/bin/env python3
"""Mark final_v1 video-analysis cache rows that were produced with project SKU
context injected into the Gemini prompt (commit 8a5545ccc) as ``stale``.

Why: the final_v1 cache row is *global* per video (``vkpi_analysis_cache``
keyed by target_type/target_id/derive_method).  While 8a5545ccc was live the
prompt carried the current project's SKU, project name and other employees'
manual product links, so one project's commercial context leaked into a row
that every project reads.  Rows produced by the restored project-free contract
carry ``provenance.prompt_contract == final_v1_pure_video_evidence_v2`` and
are never touched.

Selection (all must hold):
  * target_type='video' AND derive_method='video_analysis_final_v1'
  * status='ready'                     (already-stale rows are skipped -> idempotent)
  * updated_at in [--since, --until)   (default since = 8a5545ccc commit time, UTC)
  * result.provenance.prompt_contract != current pure contract

Default is a dry run that only logs what would change.  ``--apply`` performs
``UPDATE ... SET status='stale'`` and records ``stale_reason`` inside the
result JSON so the row explains itself.  Re-running after apply finds zero
rows.  Production never deployed 8a5545ccc, so there this script is insurance
and is expected to report zero candidates.

Usage:
  .venv/bin/python scripts/ops/mark_stale_final_v1_sku_context_cache.py            # dry run
  .venv/bin/python scripts/ops/mark_stale_final_v1_sku_context_cache.py --apply    # write
  ... --since 2026-08-21T22:38:35Z --until 2026-08-22T00:00:00Z --database-url-key DATABASE_URL
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.workers.apify_jobs_video_context import FINAL_V1_PROMPT_CONTRACT  # noqa: E402

logger = logging.getLogger("vkpi.ops.final_v1_sku_context_cache")

# 8a5545ccc "ground Gemini video analysis in SKU context" author date, in UTC.
POLLUTION_WINDOW_START = "2026-08-21T22:38:35Z"
STALE_REASON = "final_v1_prompt_carried_project_sku_context_8a5545ccc"
DERIVE_METHOD = "video_analysis_final_v1"
BATCH_LIMIT = 5000

SELECT_CANDIDATES_SQL = """
SELECT id, target_id, status, updated_at, result::text AS result_text
FROM vkpi_analysis_cache
WHERE target_type = 'video'
  AND derive_method = %s
  AND status = 'ready'
  AND updated_at >= %s
  AND updated_at < %s
ORDER BY id
LIMIT %s
"""

MARK_STALE_SQL = """
UPDATE vkpi_analysis_cache
SET status = 'stale',
    result = COALESCE(result, '{}'::jsonb) || %s::jsonb,
    updated_at = NOW()
WHERE id = %s AND status = 'ready'
"""


def parse_utc(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp required")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _result_dict(result_text: Any) -> dict[str, Any]:
    if isinstance(result_text, dict):
        return result_text
    try:
        parsed = json.loads(result_text or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def is_polluted_row(row: dict[str, Any], *, contract: str = FINAL_V1_PROMPT_CONTRACT) -> bool:
    """True when a ready final_v1 row inside the window lacks the pure contract marker."""
    if str(row.get("status") or "").strip().lower() != "ready":
        return False
    result = _result_dict(row.get("result_text", row.get("result")))
    provenance = result.get("provenance") if isinstance(result.get("provenance"), dict) else {}
    if str(provenance.get("prompt_contract") or "").strip() == contract:
        return False
    if str(result.get("stale_reason") or "").strip() == STALE_REASON:
        return False
    return True


def classify(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    polluted: list[dict[str, Any]] = []
    clean = 0
    for row in rows:
        if is_polluted_row(row):
            polluted.append(row)
        else:
            clean += 1
    return polluted, clean


def stale_patch() -> str:
    return json.dumps(
        {
            "stale_reason": STALE_REASON,
            "stale_marked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
        ensure_ascii=False,
    )


def run(conn: Any, *, since: datetime, until: datetime, apply: bool) -> dict[str, Any]:
    """Select, classify and (optionally) mark rows.  ``conn`` is a psycopg connection."""
    with conn.cursor() as cur:
        cur.execute(SELECT_CANDIDATES_SQL, (DERIVE_METHOD, since, until, BATCH_LIMIT))
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, values)) for values in cur.fetchall()]
    polluted, clean = classify(rows)
    summary = {
        "mode": "apply" if apply else "dry_run",
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "candidates_in_window": len(rows),
        "already_pure_or_marked": clean,
        "polluted": len(polluted),
        "marked_stale": 0,
        "polluted_ids": [int(row["id"]) for row in polluted][:200],
    }
    for row in polluted:
        logger.info(
            "polluted final_v1 cache row | id=%s target_id=%s updated_at=%s",
            row.get("id"),
            row.get("target_id"),
            row.get("updated_at"),
        )
    if not apply or not polluted:
        if polluted:
            logger.info("dry run: %s row(s) would be marked stale; re-run with --apply", len(polluted))
        return summary
    patch = stale_patch()
    marked = 0
    with conn.cursor() as cur:
        for row in polluted:
            cur.execute(MARK_STALE_SQL, (patch, int(row["id"])))
            marked += int(cur.rowcount or 0)
    conn.commit()
    summary["marked_stale"] = marked
    logger.info("marked %s final_v1 cache row(s) stale", marked)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write status='stale'; default is dry run")
    parser.add_argument("--since", default=POLLUTION_WINDOW_START, help="UTC ISO start of the pollution window")
    parser.add_argument("--until", default=None, help="UTC ISO end (exclusive); default now")
    parser.add_argument("--database-url-key", default="DATABASE_URL")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO), format="%(levelname)s %(name)s: %(message)s")

    dsn = os.environ.get(args.database_url_key, "").strip()
    if not dsn:
        logger.error("%s is not set; refusing to guess a database", args.database_url_key)
        return 2
    since = parse_utc(args.since)
    until = parse_utc(args.until) if args.until else datetime.now(timezone.utc)
    if since < parse_utc(POLLUTION_WINDOW_START):
        logger.warning(
            "--since %s is earlier than the 8a5545ccc window start %s: rows from before the "
            "pollution carry no prompt_contract marker either and WILL be selected",
            since.isoformat(),
            POLLUTION_WINDOW_START,
        )
    if until <= since:
        logger.error("--until must be after --since")
        return 2

    import psycopg

    with psycopg.connect(dsn, connect_timeout=5) as conn:
        summary = run(conn, since=since, until=until, apply=bool(args.apply))
    logger.info("summary %s", json.dumps(summary, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
