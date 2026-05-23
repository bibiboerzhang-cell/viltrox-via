#!/usr/bin/env python3
"""Run the V-KPI daily incremental sync job.

Default behavior:
- refresh official channels with recent public data only;
- skip legacy KOL pool rows unless an operator explicitly opts in;
- do not call LLM or deep-scan pipelines.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime  # noqa: E402
from app.services.vkpi.cron import run_job  # noqa: E402
from app.services.vkpi.daily_sync import SyncFailFast, SyncGuardBlocked  # noqa: E402


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_kol_stale_before(raw_value: str = "", stale_days: int = 0, *, now: datetime | None = None) -> str:
    """Return the KOL stale cutoff for periodic qualified refreshes.

    A blank cutoff means qualified catch-up mode, which only selects rows that
    have never been refreshed through vkpi_kol_refresh_tier.
    """
    raw = str(raw_value or "").strip()
    if raw:
        return raw
    days = max(0, int(stale_days or 0))
    if days <= 0:
        return ""
    anchor = now or datetime.now(timezone.utc)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    return (anchor.astimezone(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    parser.add_argument("--kol-stale-before", default="", help="Only refresh selected KOL rows refreshed before this UTC timestamp")
    parser.add_argument("--kol-stale-days", type=int, default=0, help="Compute --kol-stale-before as now minus N days. Use 1 for daily hot refresh.")
    parser.add_argument("--kol-max-posts", type=int, default=1, help="Latest post sample per KOL pool row")
    parser.add_argument("--kol-error-stop-threshold", type=int, default=3, help="Stop KOL refresh when provider errors reach this count")
    parser.add_argument("--kol-platforms", default="", help="Comma-separated KOL platforms to run")
    parser.add_argument("--kol-refresh-selector", default="qualified", choices=["qualified", "legacy"], help="KOL refresh selector to use when KOL refresh is explicitly included")
    parser.add_argument("--kol-tiers", default="hot", help="Comma-separated refresh tiers for qualified selector")
    parser.add_argument("--kol-source-type", default="legacy_excel_p2d", help="KOL pool source_type scope")
    parser.add_argument("--skip-kol", action="store_true", help="Skip KOL pool lightweight refresh")
    parser.add_argument(
        "--include-legacy-kol",
        action="store_true",
        help="Explicitly run the legacy KOL pool lightweight refresh. Use only for bounded retries until the tier selector replaces it.",
    )
    parser.add_argument(
        "--include-qualified-kol",
        action="store_true",
        help="Explicitly run qualified KOL refresh from vkpi_kol_refresh_tier. Does not enable legacy full-pool refresh.",
    )
    parser.set_defaults(skip_kol=True)
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    kol_selector = "legacy" if args.include_legacy_kol else args.kol_refresh_selector
    payload = {
        "dry_run": bool(args.dry_run),
        "official_max_posts": max(1, min(100, int(args.official_max_posts or 50))),
        "official_platforms": args.official_platforms,
        "skip_official": bool(args.skip_official),
        "kol_limit": max(1, min(1200, int(args.kol_limit or 1200))),
        "kol_offset": max(0, min(5000, int(args.kol_offset or 0))),
        "kol_stale_before": compute_kol_stale_before(args.kol_stale_before, args.kol_stale_days),
        "kol_max_posts": max(1, min(3, int(args.kol_max_posts or 1))),
        "kol_error_stop_threshold": max(0, min(100, int(args.kol_error_stop_threshold or 0))),
        "kol_platforms": args.kol_platforms,
        "kol_refresh_selector": kol_selector,
        "kol_tiers": args.kol_tiers,
        "kol_source_type": args.kol_source_type,
        "skip_kol": bool(args.skip_kol) and not (bool(args.include_legacy_kol) or bool(args.include_qualified_kol)),
        "allow_legacy_kol_full_refresh": bool(args.include_legacy_kol),
        "allow_qualified_kol_refresh": bool(args.include_qualified_kol),
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
            kol_stale_before=payload["kol_stale_before"],
            kol_max_posts=payload["kol_max_posts"],
            kol_error_stop_threshold=payload["kol_error_stop_threshold"],
            skip_kol=payload["skip_kol"],
            kol_refresh_selector=payload["kol_refresh_selector"],
            kol_tiers=payload["kol_tiers"],
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
    except SyncFailFast as exc:
        emit_event(
            "cron_daily_sync_interrupted",
            exit_code=exc.exit_code,
            run_id=exc.run_id,
            stage=exc.stage,
            summary=exc.summary,
            error=f"{type(exc).__name__}: {str(exc)[:500]}",
        )
        print(json.dumps({
            "job": "daily_incremental_sync",
            "status": "interrupted",
            "exit_code": exc.exit_code,
            "run_id": exc.run_id,
            "stage": exc.stage,
            "summary": exc.summary,
            "error": str(exc),
        }, ensure_ascii=False, default=str, indent=2))
        return exc.exit_code
    except SyncGuardBlocked as exc:
        emit_event(
            "cron_daily_sync_blocked",
            exit_code=exc.exit_code,
            blocking_run_id=exc.blocking_run_id,
            summary=exc.summary,
            error=f"{type(exc).__name__}: {str(exc)[:500]}",
        )
        print(json.dumps({
            "job": "daily_incremental_sync",
            "status": "blocked",
            "exit_code": exc.exit_code,
            "blocking_run_id": exc.blocking_run_id,
            "summary": exc.summary,
            "error": str(exc),
        }, ensure_ascii=False, default=str, indent=2))
        return exc.exit_code
    except Exception as exc:
        emit_event("cron_daily_sync_failed", error=f"{type(exc).__name__}: {str(exc)[:500]}")
        raise
    finally:
        await close_db_runtime()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
