#!/usr/bin/env python3
"""Run the V-KPI daily incremental sync job.

Default behavior:
- refresh official channels with recent public data only;
- skip legacy KOL pool rows unless an operator explicitly opts in;
- do not call LLM or deep-scan pipelines.
"""
from __future__ import annotations

from stdout_utils import out

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# With no pre-existing backlog, 18 official + 90 qualified KOL tasks, two
# consumers, and handlers bounded to 300 seconds give the planning upper bound
# ceil(108 / 2) * 300 = 16,200 seconds. The worker enforces that same ledger
# deadline on the live handler. 4h45m leaves 75 minutes in the primary 6h unit
# for maintenance and shutdown.

from app.db.connection import close_db_runtime  # noqa: E402
from app.domains.sync.daily_batch import (  # noqa: E402
    DEFAULT_DAILY_CHILD_TIMEOUT_SECONDS,
    DEFAULT_DAILY_KOL_LIMIT,
    DEFAULT_DAILY_WORKER_COUNT,
    DEFAULT_DAILY_CAPACITY_WINDOW_SECONDS,
)
from app.domains.sync.cron import run_job  # noqa: E402
from app.domains.sync.daily_sync import SyncFailFast, SyncGuardBlocked  # noqa: E402


DEFAULT_COMPLETION_WAIT_SECONDS = DEFAULT_DAILY_CAPACITY_WINDOW_SECONDS


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def systemd_invocation_id() -> str:
    value = str(os.environ.get("INVOCATION_ID") or "").strip().lower()
    return value if len(value) == 32 and all(char in "0123456789abcdef" for char in value) else ""


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
    # +2h 宽限:timer 04:00 开跑、上一轮 04:2x 才刷完,严格 N×24h 会让昨日刷新行今天永不到期
    # → hot 层 92/0 隔日空转 + 哨兵隔日误报断流(2026-07 实测)。宽限吃掉运行时长漂移。
    return (anchor.astimezone(timezone.utc) - timedelta(days=days) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")


def emit_event(event: str, **payload: object) -> None:
    out(json.dumps({
        "event": event,
        "at": utcnow(),
        **payload,
        "invocation_id": systemd_invocation_id(),
    }, ensure_ascii=False, default=str), flush=True)


def emit_batch_queued_receipt(receipt: dict[str, object]) -> None:
    """Persist the recoverable enqueue receipt to the service's append-only log."""
    emit_event("cron_daily_sync_enqueued", summary=receipt)


def _result_body(result: dict[str, object]) -> dict[str, object]:
    nested = result.get("result") if isinstance(result, dict) else None
    return nested if isinstance(nested, dict) else result


def _value(mapping: dict[str, object], primary: str, fallback: str = "") -> object:
    if primary in mapping:
        return mapping.get(primary)
    return mapping.get(fallback) if fallback else None


def result_summary(result: dict[str, object]) -> dict[str, object]:
    inner = _result_body(result)
    official = inner.get("official") if isinstance(inner.get("official"), dict) else {}
    kol = inner.get("kol_pool_light") if isinstance(inner.get("kol_pool_light"), dict) else {}
    completion = inner.get("completion") if isinstance(inner.get("completion"), dict) else {}
    task_ids = list(inner.get("task_ids") or []) if isinstance(inner.get("task_ids"), list) else []
    return {
        "status": inner.get("status"),
        "dry_run": bool(inner.get("dry_run")),
        "batch_id": inner.get("batch_id"),
        "task_ids": task_ids,
        "completion_scope": inner.get("completion_scope") or completion.get("completion_scope"),
        "provider_completion": inner.get("provider_completion") or completion.get("provider_completion"),
        "completion_sla_expired": completion.get("sla_expired"),
        "tasks_total": completion.get("tasks_total") if completion.get("tasks_total") is not None else len(task_ids),
        "tasks_terminal": completion.get("tasks_terminal"),
        "tasks_succeeded": completion.get("tasks_succeeded"),
        "tasks_partial": completion.get("tasks_partial"),
        "tasks_failed": completion.get("tasks_failed"),
        "tasks_skipped_known": completion.get("tasks_skipped_known"),
        "tasks_pending": completion.get("tasks_pending"),
        "official_requested": _value(official, "requested", "channels_requested"),
        "official_enqueued": _value(official, "enqueued", "channels_enqueued"),
        "official_synced": official.get("synced"),
        "official_failed": _value(official, "channels_failed_to_enqueue", "failed"),
        "kol_requested": kol.get("requested"),
        "kol_enqueued": kol.get("enqueued"),
        "kol_refreshed": kol.get("refreshed"),
        "kol_partial": kol.get("partial"),
        "kol_errors": _value(kol, "errors", "failed_to_enqueue"),
        "started_at": inner.get("started_at"),
        "finished_at": inner.get("finished_at") or inner.get("ran_at"),
    }


def post_sync_maintenance_decision(
    result: dict[str, object],
    *,
    requested_dry_run: bool,
) -> tuple[bool, str]:
    """Run write maintenance only after a completed sync, never after planning/enqueue."""
    inner = _result_body(result)
    if requested_dry_run or bool(inner.get("dry_run")):
        return False, "dry_run"
    status = str(inner.get("status") or "unknown").strip().lower()
    if status not in {"ok", "completed", "succeeded", "success"}:
        return False, f"job_not_completed:{status}"
    completion = inner.get("completion") if isinstance(inner.get("completion"), dict) else {}
    provider_completion = str(
        inner.get("provider_completion") or completion.get("provider_completion") or ""
    ).strip().lower()
    if ("completion" in inner or "provider_completion" in inner) and provider_completion != "completed":
        return False, f"provider_not_completed:{provider_completion or 'unknown'}"
    return True, "completed_sync"


def result_exit_code(result: dict[str, object]) -> int:
    """Map the honest orchestration receipt to the systemd oneshot exit code."""
    inner = _result_body(result)
    status = str(inner.get("status") or "").strip().lower()
    if status in {"failed", "partial", "interrupted", "blocked", "error"}:
        return int(inner.get("exit_code") or 2)

    official = inner.get("official") if isinstance(inner.get("official"), dict) else {}
    kol = inner.get("kol_pool_light") if isinstance(inner.get("kol_pool_light"), dict) else {}
    if status == "queued":
        enqueue_failures = int(
            _value(official, "channels_failed_to_enqueue", "failed_to_enqueue") or 0
        ) + int(kol.get("failed_to_enqueue") or 0)
        # EX_TEMPFAIL: accepted by the queue, but provider completion is still
        # unknown (or the bounded completion SLA expired).
        return 2 if enqueue_failures else 75

    if status in {"ok", "completed", "succeeded", "success"} and (
        "completion" in inner or "provider_completion" in inner
    ):
        completion = inner.get("completion") if isinstance(inner.get("completion"), dict) else {}
        provider_completion = str(
            inner.get("provider_completion") or completion.get("provider_completion") or "unknown"
        ).strip().lower()
        if provider_completion != "completed":
            scope = str(inner.get("completion_scope") or completion.get("completion_scope") or "").lower()
            if (
                provider_completion == "not_run"
                and scope == "no_work"
                and int(completion.get("tasks_total") or 0) == 0
                and "enqueue_failures" in inner
                and int(inner.get("enqueue_failures") or 0) == 0
            ):
                return 0
            return 75 if provider_completion == "unknown" else 2

    health = inner.get("health") if isinstance(inner.get("health"), dict) else {}
    if bool(health.get("blocked_next_run")):
        return 2
    rate = health.get("failure_rate")
    threshold = health.get("failure_rate_threshold")
    if isinstance(rate, (int, float)) and isinstance(threshold, (int, float)):
        return 2 if float(rate) > float(threshold) else 0
    if int(official.get("failed") or 0) or int(kol.get("errors") or 0):
        return 2
    if status in {"ok", "completed", "succeeded", "success", "planned"}:
        return 0
    return 2


def run_post_sync_maintenance(*, batch_id: str) -> None:
    """Run maintenance that depends on already-persisted sync results."""
    batch_id = str(batch_id or "").strip()[:128]
    try:
        from app.db.connection import get_conn
        from app.domains.channels.metrics_gapfill import backfill_filled_table

        gap_result = backfill_filled_table(get_conn())
        emit_event("cron_daily_sync_gapfill", batch_id=batch_id, summary=gap_result)
    except Exception as exc:
        emit_event(
            "cron_daily_sync_gapfill_failed",
            batch_id=batch_id,
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
        )

    try:
        import subprocess

        repo = str(Path(__file__).resolve().parents[1])
        for script, command in (
            ("scripts/expand_kol_profile_index.py", "write-and-validate"),
            ("scripts/classify_kol_profile_type.py", "write"),
        ):
            process = subprocess.run(
                [sys.executable, str(Path(repo) / script), command],
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=1800,
            )
            if process.returncode != 0:
                emit_event(
                    "cron_daily_sync_index_maint_failed",
                    batch_id=batch_id,
                    script=script,
                    code=process.returncode,
                    err=str(process.stderr)[-300:],
                )
        emit_event("cron_daily_sync_index_maint_done", batch_id=batch_id)
    except Exception as exc:
        emit_event(
            "cron_daily_sync_index_maint_failed",
            batch_id=batch_id,
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run V-KPI daily incremental sync")
    parser.add_argument("--dry-run", action="store_true", help="Plan the run without provider calls or DB writes")
    parser.add_argument("--official-max-posts", type=int, default=50, help="Recent posts per official account")
    parser.add_argument("--official-platforms", default="", help="Comma-separated official platforms to run")
    parser.add_argument("--skip-official", action="store_true", help="Skip 18 official-account refresh")
    parser.add_argument(
        "--kol-limit",
        type=int,
        default=DEFAULT_DAILY_KOL_LIMIT,
        help="Max KOL pool rows to refresh inside the reviewed daily capacity budget",
    )
    parser.add_argument("--kol-offset", type=int, default=0, help="Skip the first N selected KOL rows for bounded retries")
    parser.add_argument("--kol-stale-before", default="", help="Only refresh selected KOL rows refreshed before this UTC timestamp")
    parser.add_argument("--kol-stale-days", type=int, default=0, help="Compute --kol-stale-before as now minus N days. Use 1 for daily hot refresh.")
    parser.add_argument("--kol-max-posts", type=int, default=1, help="Latest post sample per KOL pool row")
    parser.add_argument("--kol-error-stop-threshold", type=int, default=3, help="Stop KOL refresh when provider errors reach this count")
    parser.add_argument("--kol-platforms", default="", help="Comma-separated KOL platforms to run")
    parser.add_argument("--kol-refresh-selector", default="qualified", choices=["qualified", "legacy"], help="KOL refresh selector to use when KOL refresh is explicitly included")
    parser.add_argument("--kol-tiers", default="hot", help="Comma-separated refresh tiers for qualified selector")
    parser.add_argument("--kol-source-type", default="legacy_excel_p2d", help="KOL pool source_type scope")
    parser.add_argument(
        "--completion-wait-seconds",
        type=float,
        default=DEFAULT_COMPLETION_WAIT_SECONDS,
        help="Bounded SLA for read-only child-ledger polling before returning queued/partial (default: 4h45m)",
    )
    parser.add_argument(
        "--completion-poll-seconds",
        type=float,
        default=10.0,
        help="Seconds between read-only child-ledger polls",
    )
    parser.add_argument(
        "--worker-count",
        type=int,
        default=DEFAULT_DAILY_WORKER_COUNT,
        help="Reviewed Redis consumer count used by the capacity admission bound",
    )
    parser.add_argument(
        "--child-timeout-seconds",
        type=int,
        default=DEFAULT_DAILY_CHILD_TIMEOUT_SECONDS,
        help="Per-child ledger and live-handler deadline used by capacity admission",
    )
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
        "kol_limit": max(1, min(1200, int(args.kol_limit or DEFAULT_DAILY_KOL_LIMIT))),
        "kol_offset": max(0, min(5000, int(args.kol_offset or 0))),
        "kol_stale_before": compute_kol_stale_before(args.kol_stale_before, args.kol_stale_days),
        "kol_max_posts": max(1, min(3, int(args.kol_max_posts or 1))),
        "kol_error_stop_threshold": max(0, min(100, int(args.kol_error_stop_threshold or 0))),
        "kol_platforms": args.kol_platforms,
        "kol_refresh_selector": kol_selector,
        "kol_tiers": args.kol_tiers,
        "kol_source_type": args.kol_source_type,
        "completion_wait_seconds": max(0.0, min(19_800.0, float(args.completion_wait_seconds or 0.0))),
        "completion_poll_seconds": max(0.05, min(60.0, float(args.completion_poll_seconds or 10.0))),
        "capacity_window_seconds": max(0.0, min(19_800.0, float(args.completion_wait_seconds or 0.0))),
        "worker_count": max(1, min(4, int(args.worker_count or DEFAULT_DAILY_WORKER_COUNT))),
        "child_timeout_seconds": max(
            1,
            min(86_400, int(args.child_timeout_seconds or DEFAULT_DAILY_CHILD_TIMEOUT_SECONDS)),
        ),
        "skip_kol": bool(args.skip_kol) and not (bool(args.include_legacy_kol) or bool(args.include_qualified_kol)),
        "allow_legacy_kol_full_refresh": bool(args.include_legacy_kol),
        "allow_qualified_kol_refresh": bool(args.include_qualified_kol),
        "staff": {"id": 0, "staff_id": 0, "user_id": 0, "role": "admin", "is_owner": 1},
    }
    if not payload["dry_run"]:
        payload["_batch_receipt_callback"] = emit_batch_queued_receipt
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
            completion_wait_seconds=payload["completion_wait_seconds"],
            completion_poll_seconds=payload["completion_poll_seconds"],
            capacity_window_seconds=payload["capacity_window_seconds"],
            worker_count=payload["worker_count"],
            child_timeout_seconds=payload["child_timeout_seconds"],
        )
        result = await run_job("daily_incremental_sync", payload)
        summary = result_summary(result)
        emit_event("cron_daily_sync_finished", summary=summary)
        run_maintenance, maintenance_reason = post_sync_maintenance_decision(
            result,
            requested_dry_run=bool(payload["dry_run"]),
        )
        if run_maintenance:
            run_post_sync_maintenance(batch_id=str(summary.get("batch_id") or ""))
        else:
            emit_event(
                "cron_daily_sync_post_maintenance_skipped",
                batch_id=str(summary.get("batch_id") or ""),
                reason=maintenance_reason,
            )
        out(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        return result_exit_code(result)
    except SyncFailFast as exc:
        emit_event(
            "cron_daily_sync_interrupted",
            exit_code=exc.exit_code,
            run_id=exc.run_id,
            stage=exc.stage,
            summary=exc.summary,
            error=f"{type(exc).__name__}: {str(exc)[:500]}",
        )
        out(json.dumps({
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
        out(json.dumps({
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
