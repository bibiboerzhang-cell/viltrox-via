#!/usr/bin/env python3
"""Read-only acceptance report for P1.X.C KOL on-demand refresh safety.

This report verifies that stale-while-revalidate can expose freshness and search
interest without accidentally enqueueing provider work. It does not call Apify,
YouTube, Gemini, LLMs, crawlers, sync jobs, or API endpoints.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import asyncio
import json
import os
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

from app.db.connection import close_db_runtime, get_conn, is_postgres_runtime  # noqa: E402
import app.domains.sync.refresh_tier as refresh_tier  # noqa: E402
import app.domains.tasks.enqueue as task_enqueue  # noqa: E402


ACTIVE_STATUSES = ("queued", "retrying", "processing", "running")
TASK_TYPE = task_enqueue.VKPI_KOL_POOL_ON_DEMAND_REFRESH


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "")))
    except (TypeError, ValueError):
        return int(default or 0)


def _timer_policy(timer_command: str) -> dict[str, Any]:
    text = str(timer_command or "")
    return {
        "command": text,
        "checked": bool(text.strip()),
        "contains_skip_kol": "--skip-kol" in text,
        "contains_official_max_posts_50": "--official-max-posts 50" in text,
        "contains_include_legacy_kol": "--include-legacy-kol" in text,
        "contains_include_qualified_kol": "--include-qualified-kol" in text,
    }


def _on_demand_refresh_enabled() -> bool:
    for name in ("VKPI_KOL_ON_DEMAND_REFRESH_ENABLED", "VKPI_ENABLE_KOL_ON_DEMAND_REFRESH"):
        value = os.getenv(name, "").strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
    return False


def _table_columns(table_name: str) -> set[str]:
    if not refresh_tier._table_exists(table_name):
        return set()
    if is_postgres_runtime():
        rows = get_conn().execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = ?
            """,
            (table_name,),
        ).fetchall()
        return {str(row["column_name"]) for row in rows}
    rows = get_conn().execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"] if "name" in row.keys() else row[1]) for row in rows}


def _kol_pool_total() -> int:
    if not refresh_tier._table_exists("vkpi_kol_pool"):
        return 0
    row = get_conn().execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool").fetchone()
    return _int(row["n"] if row else 0)


def _tier_snapshot() -> dict[str, Any]:
    if not refresh_tier._table_exists("vkpi_kol_refresh_tier"):
        return {"table_exists": False, "total": 0, "tiers": {}, "recent_search_count": 0}
    rows = get_conn().execute(
        """
        SELECT tier,
               COUNT(*) AS n,
               SUM(CASE WHEN last_refresh_at IS NULL THEN 1 ELSE 0 END) AS never_refreshed,
               SUM(CASE WHEN COALESCE(search_count_30d, 0) > 0 THEN 1 ELSE 0 END) AS searched_rows,
               SUM(COALESCE(search_count_30d, 0)) AS search_count_30d,
               MIN(last_refresh_at) AS min_refresh,
               MAX(last_refresh_at) AS max_refresh
        FROM vkpi_kol_refresh_tier
        GROUP BY tier
        ORDER BY tier ASC
        """
    ).fetchall()
    tiers: dict[str, dict[str, Any]] = {}
    searched_total = 0
    search_count_total = 0
    for row in rows:
        tier = str(row["tier"] or "cold")
        searched_rows = _int(row["searched_rows"])
        search_count = _int(row["search_count_30d"])
        searched_total += searched_rows
        search_count_total += search_count
        tiers[tier] = {
            "count": _int(row["n"]),
            "never_refreshed": _int(row["never_refreshed"]),
            "searched_rows": searched_rows,
            "search_count_30d": search_count,
            "min_refresh": str(row["min_refresh"] or ""),
            "max_refresh": str(row["max_refresh"] or ""),
        }
    return {
        "table_exists": True,
        "total": sum(item["count"] for item in tiers.values()),
        "tiers": tiers,
        "searched_rows_total": searched_total,
        "search_count_30d_total": search_count_total,
    }


def _task_snapshot(limit: int = 10) -> dict[str, Any]:
    if not refresh_tier._table_exists("job_execution_ledger"):
        return {"table_exists": False, "status_counts": {}, "active_count": 0, "recent": []}
    columns = _table_columns("job_execution_ledger")
    status_rows = get_conn().execute(
        """
        SELECT status, COUNT(*) AS n
        FROM job_execution_ledger
        WHERE job_type=?
        GROUP BY status
        ORDER BY status ASC
        """,
        (TASK_TYPE,),
    ).fetchall()
    status_counts = {str(row["status"] or "unknown"): _int(row["n"]) for row in status_rows}
    active_count = sum(status_counts.get(status, 0) for status in ACTIVE_STATUSES)
    base_columns = ["task_id", "job_type", "status", "created_at", "updated_at"]
    optional_columns = ["lock_key", "summary", "error_message", "stage"]
    select_columns = [column for column in [*base_columns, *optional_columns] if column in columns]
    recent: list[dict[str, Any]] = []
    if select_columns:
        rows = get_conn().execute(
            f"""
            SELECT {", ".join(select_columns)}
            FROM job_execution_ledger
            WHERE job_type=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (TASK_TYPE, max(1, min(100, int(limit or 10)))),
        ).fetchall()
        for row in rows:
            data = dict(row)
            recent.append({column: str(data.get(column) or "") for column in select_columns})
    return {
        "table_exists": True,
        "status_counts": status_counts,
        "active_count": active_count,
        "recent": recent,
    }


def build_report(
    *,
    timer_command: str = "",
    allow_provider_enabled: bool = False,
    max_active_tasks: int = 0,
    recent_task_limit: int = 10,
) -> dict[str, Any]:
    provider_gate_enabled = _on_demand_refresh_enabled()
    tier = _tier_snapshot()
    tasks = _task_snapshot(limit=recent_task_limit)
    timer = _timer_policy(timer_command)
    kol_pool_total = _kol_pool_total()
    tiers = tier.get("tiers") if isinstance(tier.get("tiers"), dict) else {}
    hot_count = _int(tiers.get("hot", {}).get("count") if isinstance(tiers.get("hot"), dict) else 0)
    cold_count = _int(tiers.get("cold", {}).get("count") if isinstance(tiers.get("cold"), dict) else 0)
    checks = {
        "provider_gate_expected_state": bool(allow_provider_enabled or not provider_gate_enabled),
        "task_type_supported": TASK_TYPE in task_enqueue.SUPPORTED_TASK_TYPES,
        "selector_table_exists": bool(tier.get("table_exists")),
        "tier_rows_match_kol_pool": _int(tier.get("total")) == kol_pool_total,
        "cold_records_preserved": cold_count > hot_count,
        "active_on_demand_tasks_bounded": _int(tasks.get("active_count")) <= max(0, int(max_active_tasks)),
        "timer_official_only": True
        if not timer["checked"]
        else bool(timer["contains_skip_kol"] and timer["contains_official_max_posts_50"] and not timer["contains_include_legacy_kol"]),
    }
    return {
        "mode": "read_only_on_demand_refresh_acceptance",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider_calls": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "policy": {
            "provider_gate_enabled": provider_gate_enabled,
            "allow_provider_enabled": bool(allow_provider_enabled),
            "max_active_tasks": int(max_active_tasks),
            "task_type": TASK_TYPE,
        },
        "timer": timer,
        "kol_pool_total": kol_pool_total,
        "tier": tier,
        "tasks": tasks,
    }


def render_markdown(report: dict[str, Any]) -> str:
    tier = report["tier"]
    tiers = tier.get("tiers") if isinstance(tier.get("tiers"), dict) else {}
    tasks = report["tasks"]
    lines = [
        "# V-KPI On-Demand Refresh Acceptance",
        "",
        "Read-only P1.X.C acceptance report. This report does not enqueue tasks or call providers.",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Passed: `{str(report['passed']).lower()}`",
        f"- Provider calls: `{str(report['provider_calls']).lower()}`",
        f"- Sync triggered: `{str(report['sync_triggered']).lower()}`",
        f"- Task enqueued: `{str(report['task_enqueued']).lower()}`",
        f"- Provider gate enabled: `{str(report['policy']['provider_gate_enabled']).lower()}`",
        f"- KOL pool rows: `{report['kol_pool_total']}`",
        "",
        "## Tier Search State",
        "",
        "| Tier | Rows | Never refreshed | Searched rows | Search count 30d | Min refresh | Max refresh |",
        "| --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for tier_name in ("hot", "warm", "cold"):
        row = tiers.get(tier_name) if isinstance(tiers.get(tier_name), dict) else {}
        lines.append(
            f"| `{tier_name}` | `{row.get('count', 0)}` | `{row.get('never_refreshed', 0)}` | "
            f"`{row.get('searched_rows', 0)}` | `{row.get('search_count_30d', 0)}` | "
            f"`{row.get('min_refresh') or '-'}` | `{row.get('max_refresh') or '-'}` |"
        )
    lines.extend(
        [
            "",
            "## On-Demand Task Ledger",
            "",
            f"- Ledger table exists: `{str(tasks.get('table_exists')).lower()}`",
            f"- Active task count: `{tasks.get('active_count', 0)}`",
            f"- Status counts: `{tasks.get('status_counts') or {}}`",
            "",
            "## Checks",
            "",
        ]
    )
    for key, value in report["checks"].items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    recent = tasks.get("recent") if isinstance(tasks.get("recent"), list) else []
    lines.extend(["", "## Recent On-Demand Tasks", ""])
    if not recent:
        lines.append("- none")
    else:
        for row in recent[:10]:
            lines.append(
                f"- `{row.get('task_id', '-')}` status=`{row.get('status', '-')}` "
                f"created=`{row.get('created_at', '-')}` lock=`{row.get('lock_key', '-')}`"
            )
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only P1.X.C on-demand refresh safety report.")
    parser.add_argument("--timer-command", default="", help="Current deployed vkpi-sync-daily.service ExecStart")
    parser.add_argument("--timer-command-file", default="", help="Read current timer command from a text file")
    parser.add_argument("--allow-provider-enabled", action="store_true", help="Do not fail if the on-demand provider gate is enabled")
    parser.add_argument("--max-active-tasks", type=int, default=0)
    parser.add_argument("--recent-task-limit", type=int, default=10)
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


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        timer_command = str(args.timer_command or "")
        if args.timer_command_file:
            timer_command = Path(args.timer_command_file).read_text(encoding="utf-8")
        report = build_report(
            timer_command=timer_command,
            allow_provider_enabled=bool(args.allow_provider_enabled),
            max_active_tasks=max(0, int(args.max_active_tasks or 0)),
            recent_task_limit=max(1, min(100, int(args.recent_task_limit or 10))),
        )
        markdown = render_markdown(report)
        if args.json_out:
            _write(args.json_out, json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
        if args.md_out:
            _write(args.md_out, markdown)
        if args.json:
            stdout_out(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        else:
            stdout_out(markdown)
        return 0 if report.get("passed") else 3
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
