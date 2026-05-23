#!/usr/bin/env python3
"""Read-only P1.X readiness report for V-KPI KOL refresh.

This report aggregates the P1.X.A selector, P1.X.C on-demand safety, and
P1.X.B Apify batch plan gates. It does not call providers, enqueue tasks, run
sync jobs, or change timer state.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.connection import close_db_runtime  # noqa: E402
from scripts import (  # noqa: E402
    vkpi_apify_batch_refresh,
    vkpi_on_demand_refresh_acceptance,
    vkpi_refresh_tier_acceptance,
)


def _safe_int(value: Any) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _state_checked(value: str) -> bool:
    return bool(str(value or "").strip())


def _state_allows_disabled(value: str) -> bool:
    if not _state_checked(value):
        return True
    return str(value or "").strip().lower() in {"disabled", "not-found", "inactive", "unknown"}


def _state_is_active(value: str) -> bool:
    if not _state_checked(value):
        return True
    return str(value or "").strip().lower() == "active"


def _state_is_not_active(value: str) -> bool:
    if not _state_checked(value):
        return True
    return str(value or "").strip().lower() != "active"


def _batch_args(
    *,
    limit: int,
    tiers: str,
    stale_days: int,
    max_posts: int,
    max_concurrent_runs: int,
    max_live_targets: int,
) -> argparse.Namespace:
    return vkpi_apify_batch_refresh.parse_args(
        [
            "--limit",
            str(max(1, min(1200, int(limit or 200)))),
            "--tiers",
            tiers or "hot",
            "--stale-days",
            str(max(0, int(stale_days or 1))),
            "--max-posts",
            str(max(1, min(3, int(max_posts or 1)))),
            "--max-concurrent-runs",
            str(max(1, min(3, int(max_concurrent_runs or 2)))),
            "--max-live-targets",
            str(max(1, min(100, int(max_live_targets or 25)))),
            "--compact",
            "--no-artifact",
        ]
    )


async def build_report(
    *,
    timer_command: str = "",
    daily_service_active: str = "",
    daily_timer_active: str = "",
    qualified_timer_enabled: str = "",
    selector_limit: int = 200,
    tiers: str = "hot",
    stale_days: int = 1,
    max_posts: int = 1,
    max_concurrent_runs: int = 2,
    max_live_targets: int = 25,
) -> dict[str, Any]:
    selector = vkpi_refresh_tier_acceptance.build_report(
        timer_command=timer_command,
        selector_limit=selector_limit,
        stale_days=stale_days,
        max_posts=max_posts,
        max_concurrent_runs=max_concurrent_runs,
    )
    on_demand = vkpi_on_demand_refresh_acceptance.build_report(
        timer_command=timer_command,
        allow_provider_enabled=False,
        max_active_tasks=0,
        recent_task_limit=10,
    )
    batch = await vkpi_apify_batch_refresh.run_from_args(
        _batch_args(
            limit=selector_limit,
            tiers=tiers,
            stale_days=stale_days,
            max_posts=max_posts,
            max_concurrent_runs=max_concurrent_runs,
            max_live_targets=max_live_targets,
        )
    )
    batch_summary = batch.get("operator_summary") if isinstance(batch.get("operator_summary"), dict) else {}
    batch_plan = batch.get("plan") if isinstance(batch.get("plan"), dict) else {}
    batch_execution = batch.get("execution") if isinstance(batch.get("execution"), dict) else {}
    checks = {
        "selector_acceptance_passed": bool(selector.get("passed")),
        "on_demand_acceptance_passed": bool(on_demand.get("passed")),
        "provider_calls_blocked": not bool(batch.get("provider_calls_allowed")),
        "batch_executor_not_executed": not bool(batch_execution.get("executed")),
        "batch_plan_only": str(batch_plan.get("mode") or "plan_only") == "plan_only"
        and bool(batch_plan.get("execution_enabled")) is False,
        "selector_ready": bool(batch_plan.get("selector_ready")) or bool(batch_summary.get("selector_ready")),
        "max_concurrency_bounded": _safe_int(batch_plan.get("max_concurrent_runs") or batch_summary.get("max_concurrent_runs")) <= 3,
        "daily_timer_active": _state_is_active(daily_timer_active),
        "daily_service_not_active": _state_is_not_active(daily_service_active),
        "qualified_timer_not_enabled": _state_allows_disabled(qualified_timer_enabled),
        "timer_official_only": bool(selector.get("checks", {}).get("timer_official_only", True))
        and bool(on_demand.get("checks", {}).get("timer_official_only", True)),
        "no_sync_side_effect": not bool(selector.get("sync_triggered")) and not bool(on_demand.get("sync_triggered")),
        "no_task_side_effect": not bool(on_demand.get("task_enqueued")),
    }
    return {
        "mode": "read_only_p1x_readiness_report",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider_calls": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "systemd": {
            "daily_service_active": daily_service_active,
            "daily_timer_active": daily_timer_active,
            "qualified_timer_enabled": qualified_timer_enabled,
            "timer_command_checked": bool(str(timer_command or "").strip()),
        },
        "selector_summary": {
            "passed": bool(selector.get("passed")),
            "hot_count": _safe_int((selector.get("stored", {}).get("tiers", {}).get("hot") or {}).get("count")),
            "cold_count": _safe_int((selector.get("stored", {}).get("tiers", {}).get("cold") or {}).get("count")),
            "source_total": _safe_int(selector.get("selector", {}).get("qualified", {}).get("source_total")),
        },
        "on_demand_summary": {
            "passed": bool(on_demand.get("passed")),
            "provider_gate_enabled": bool(on_demand.get("policy", {}).get("provider_gate_enabled")),
            "active_tasks": _safe_int(on_demand.get("tasks", {}).get("active_count")),
            "searched_rows": _safe_int(on_demand.get("tier", {}).get("searched_rows_total")),
            "search_count_30d": _safe_int(on_demand.get("tier", {}).get("search_count_30d_total")),
        },
        "batch_summary": {
            "readiness": str(batch_summary.get("readiness") or ""),
            "provider_gate_reason": str(batch_summary.get("provider_gate_reason") or ""),
            "provider_calls_allowed": bool(batch_summary.get("provider_calls_allowed")),
            "execution_preflight_status": str(batch_summary.get("execution_preflight_status") or ""),
            "selector_ready": bool(batch_summary.get("selector_ready")),
            "source_total": _safe_int(batch_summary.get("source_total")),
            "target_count": _safe_int(batch_summary.get("target_count")),
            "batch_count": _safe_int(batch_summary.get("batch_count")),
            "safe_window_count": _safe_int(batch_summary.get("safe_window_count")),
            "max_concurrent_runs": _safe_int(batch_plan.get("max_concurrent_runs")),
            "platforms": batch_summary.get("platforms") if isinstance(batch_summary.get("platforms"), dict) else {},
        },
        "reports": {
            "selector": selector,
            "on_demand": on_demand,
            "apify_batch": batch,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    selector = report["selector_summary"]
    on_demand = report["on_demand_summary"]
    batch = report["batch_summary"]
    lines = [
        "# V-KPI P1.X Readiness Report",
        "",
        "Read-only P1.X acceptance report. It does not call providers, enqueue tasks, run sync jobs, or change timers.",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Passed: `{str(report['passed']).lower()}`",
        f"- Provider calls: `{str(report['provider_calls']).lower()}`",
        f"- Sync triggered: `{str(report['sync_triggered']).lower()}`",
        f"- Task enqueued: `{str(report['task_enqueued']).lower()}`",
        "",
        "## Summary",
        "",
        "| Area | Status | Evidence |",
        "| --- | --- | --- |",
        (
            f"| Selector P1.X.A | `{str(selector['passed']).lower()}` | "
            f"hot={selector['hot_count']}, cold={selector['cold_count']}, source={selector['source_total']} |"
        ),
        (
            f"| On-demand P1.X.C | `{str(on_demand['passed']).lower()}` | "
            f"gate={str(on_demand['provider_gate_enabled']).lower()}, active_tasks={on_demand['active_tasks']}, "
            f"searches_30d={on_demand['search_count_30d']} |"
        ),
        (
            f"| Apify batch P1.X.B | `{batch['readiness'] or 'unknown'}` | "
            f"targets={batch['target_count']}, batches={batch['batch_count']}, "
            f"max_concurrent={batch['max_concurrent_runs']}, provider={batch['provider_gate_reason']} |"
        ),
        "",
        "## Systemd Gate",
        "",
        f"- daily service active: `{report['systemd']['daily_service_active'] or 'not_checked'}`",
        f"- daily timer active: `{report['systemd']['daily_timer_active'] or 'not_checked'}`",
        f"- qualified KOL timer enabled: `{report['systemd']['qualified_timer_enabled'] or 'not_checked'}`",
        f"- timer command checked: `{str(report['systemd']['timer_command_checked']).lower()}`",
        "",
        "## Checks",
        "",
    ]
    for key, value in report["checks"].items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    lines.extend(
        [
            "",
            "## Batch Platforms",
            "",
            f"`{batch['platforms'] or {}}`",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only V-KPI P1.X readiness report.")
    parser.add_argument("--timer-command", default="", help="Current deployed vkpi-sync-daily.service ExecStart")
    parser.add_argument("--timer-command-file", default="", help="Read current timer command from a text file")
    parser.add_argument("--daily-service-active", default="", help="Optional systemctl is-active vkpi-sync-daily.service output")
    parser.add_argument("--daily-timer-active", default="", help="Optional systemctl is-active vkpi-sync-daily.timer output")
    parser.add_argument("--qualified-timer-enabled", default="", help="Optional systemctl is-enabled vkpi-qualified-kol-refresh.timer output")
    parser.add_argument("--selector-limit", type=int, default=200)
    parser.add_argument("--tiers", default="hot")
    parser.add_argument("--stale-days", type=int, default=1)
    parser.add_argument("--max-posts", type=int, default=1)
    parser.add_argument("--max-concurrent-runs", type=int, default=2)
    parser.add_argument("--max-live-targets", type=int, default=25)
    parser.add_argument("--json-out", default="", help="Write JSON report to this path")
    parser.add_argument("--md-out", default="", help="Write Markdown report to this path")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown")
    return parser.parse_args(argv)


def _write(path_value: str, content: str) -> None:
    if not path_value:
        return
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


async def async_main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        timer_command = str(args.timer_command or "")
        if args.timer_command_file:
            timer_command = Path(args.timer_command_file).read_text(encoding="utf-8")
        report = await build_report(
            timer_command=timer_command,
            daily_service_active=str(args.daily_service_active or ""),
            daily_timer_active=str(args.daily_timer_active or ""),
            qualified_timer_enabled=str(args.qualified_timer_enabled or ""),
            selector_limit=max(1, min(1200, int(args.selector_limit or 200))),
            tiers=str(args.tiers or "hot"),
            stale_days=max(0, int(args.stale_days or 1)),
            max_posts=max(1, min(3, int(args.max_posts or 1))),
            max_concurrent_runs=max(1, min(3, int(args.max_concurrent_runs or 2))),
            max_live_targets=max(1, min(100, int(args.max_live_targets or 25))),
        )
        markdown = render_markdown(report)
        if args.json_out:
            _write(args.json_out, json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
        if args.md_out:
            _write(args.md_out, markdown)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        else:
            print(markdown)
        return 0 if report.get("passed") else 3
    finally:
        await close_db_runtime()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
