#!/usr/bin/env python3
"""Run the V-KPI daily incremental sync job.

Default behavior:
- refresh official channels with recent public data only;
- refresh legacy KOL pool rows with max 1 latest post sample;
- do not call LLM or deep-scan pipelines.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime  # noqa: E402
from app.services.vkpi.cron import run_job  # noqa: E402


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def emit_event(event: str, **payload: object) -> None:
    print(json.dumps({"event": event, "at": utcnow(), **payload}, ensure_ascii=False, default=str), flush=True)


def result_summary(result: dict[str, object]) -> dict[str, object]:
    inner = result.get("result") if isinstance(result, dict) else {}
    if not isinstance(inner, dict):
        return {}
    official = inner.get("official") if isinstance(inner.get("official"), dict) else {}
    kol = inner.get("kol_pool_light") if isinstance(inner.get("kol_pool_light"), dict) else {}
    return {
        "official_requested": official.get("requested"),
        "official_synced": official.get("synced"),
        "official_failed": official.get("failed"),
        "kol_requested": kol.get("requested"),
        "kol_refreshed": kol.get("refreshed"),
        "kol_partial": kol.get("partial"),
        "kol_errors": kol.get("errors"),
        "started_at": inner.get("started_at"),
        "finished_at": inner.get("finished_at"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run V-KPI daily incremental sync")
    parser.add_argument("--dry-run", action="store_true", help="Plan the run without provider calls or DB writes")
    parser.add_argument("--official-max-posts", type=int, default=50, help="Recent posts per official account")
    parser.add_argument("--official-platforms", default="", help="Comma-separated official platforms to run")
    parser.add_argument("--skip-official", action="store_true", help="Skip 18 official-account refresh")
    parser.add_argument("--kol-limit", type=int, default=1200, help="Max KOL pool rows to refresh")
    parser.add_argument("--kol-offset", type=int, default=0, help="Skip the first N selected KOL rows for bounded retries")
    parser.add_argument("--kol-max-posts", type=int, default=1, help="Latest post sample per KOL pool row")
    parser.add_argument("--kol-platforms", default="", help="Comma-separated KOL platforms to run")
    parser.add_argument("--kol-source-type", default="legacy_excel_p2d", help="KOL pool source_type scope")
    parser.add_argument("--skip-kol", action="store_true", help="Skip KOL pool lightweight refresh")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    payload = {
        "dry_run": bool(args.dry_run),
        "official_max_posts": max(1, min(100, int(args.official_max_posts or 50))),
        "official_platforms": args.official_platforms,
        "skip_official": bool(args.skip_official),
        "kol_limit": max(1, min(1200, int(args.kol_limit or 1200))),
        "kol_offset": max(0, min(5000, int(args.kol_offset or 0))),
        "kol_max_posts": max(1, min(3, int(args.kol_max_posts or 1))),
        "kol_platforms": args.kol_platforms,
        "kol_source_type": args.kol_source_type,
        "skip_kol": bool(args.skip_kol),
        "staff": {"id": 0, "staff_id": 0, "user_id": 0, "role": "admin", "is_owner": 1},
    }
    try:
        emit_event(
            "cron_daily_sync_started",
            dry_run=payload["dry_run"],
            official_max_posts=payload["official_max_posts"],
            skip_official=payload["skip_official"],
            kol_limit=payload["kol_limit"],
            kol_offset=payload["kol_offset"],
            kol_max_posts=payload["kol_max_posts"],
            skip_kol=payload["skip_kol"],
            kol_source_type=payload["kol_source_type"],
        )
        result = await run_job("daily_incremental_sync", payload)
        emit_event("cron_daily_sync_finished", summary=result_summary(result))
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        inner = result.get("result") if isinstance(result, dict) else {}
        if isinstance(inner, dict):
            official = inner.get("official") if isinstance(inner.get("official"), dict) else {}
            kol = inner.get("kol_pool_light") if isinstance(inner.get("kol_pool_light"), dict) else {}
            if int(official.get("failed") or 0) or int(kol.get("errors") or 0):
                return 2
        return 0
    except Exception as exc:
        emit_event("cron_daily_sync_failed", error=f"{type(exc).__name__}: {str(exc)[:500]}")
        raise
    finally:
        await close_db_runtime()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
