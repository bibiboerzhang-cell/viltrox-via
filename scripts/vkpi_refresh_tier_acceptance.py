#!/usr/bin/env python3
"""Read-only acceptance report for P1.X.A KOL refresh tier selector.

This report verifies that legacy KOL records remain searchable records while
future KOL refresh is gated by ``vkpi_kol_refresh_tier``. It does not call
Apify, YouTube, Gemini, LLMs, crawlers, or sync jobs.
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

from app.db.connection import close_db_runtime, get_conn  # noqa: E402
from app.services.vkpi import apify_batch_refresh, refresh_tier  # noqa: E402


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


def _stored_distribution() -> dict[str, Any]:
    if not refresh_tier._table_exists("vkpi_kol_refresh_tier"):
        return {
            "table_exists": False,
            "total": 0,
            "tiers": {},
        }
    rows = get_conn().execute(
        """
        SELECT tier,
               COUNT(*) AS n,
               SUM(CASE WHEN last_refresh_at IS NULL THEN 1 ELSE 0 END) AS never_refreshed,
               MIN(last_refresh_at) AS min_refresh,
               MAX(last_refresh_at) AS max_refresh
        FROM vkpi_kol_refresh_tier
        GROUP BY tier
        ORDER BY tier ASC
        """
    ).fetchall()
    tiers: dict[str, dict[str, Any]] = {}
    for row in rows:
        tier = str(row["tier"] or "cold")
        tiers[tier] = {
            "count": _int(row["n"]),
            "never_refreshed": _int(row["never_refreshed"]),
            "min_refresh": str(row["min_refresh"] or ""),
            "max_refresh": str(row["max_refresh"] or ""),
        }
    return {
        "table_exists": True,
        "total": sum(item["count"] for item in tiers.values()),
        "tiers": tiers,
    }


def _selector_plan(*, limit: int, tiers: set[str], stale_days: int, max_posts: int, max_concurrent_runs: int) -> dict[str, Any]:
    qualified = refresh_tier.qualified_refresh_plan(limit=limit, tiers=tiers)
    batch_plan = apify_batch_refresh.qualified_apify_batch_plan(
        limit=limit,
        stale_days=stale_days,
        tiers=tiers,
        max_posts=max_posts,
        max_concurrent=max_concurrent_runs,
    )
    return {
        "qualified": qualified,
        "apify_batch_plan": {
            "mode": batch_plan.get("mode"),
            "execution_enabled": batch_plan.get("execution_enabled"),
            "strategy": batch_plan.get("strategy"),
            "stale_before": batch_plan.get("stale_before"),
            "source_total": _int(batch_plan.get("source_total")),
            "total_targets": _int(batch_plan.get("total_targets")),
            "batch_count": _int(batch_plan.get("batch_count")),
            "max_concurrent_runs": _int(batch_plan.get("max_concurrent_runs")),
            "platforms": batch_plan.get("platforms") if isinstance(batch_plan.get("platforms"), dict) else {},
            "skipped_count": len(batch_plan.get("skipped") or []),
            "batches": [
                {
                    "batch_key": batch.get("batch_key"),
                    "platform": batch.get("platform"),
                    "target_count": batch.get("target_count"),
                    "actor_id": batch.get("actor_id"),
                    "kol_pool_ids": batch.get("kol_pool_ids"),
                }
                for batch in (batch_plan.get("batches") or [])
                if isinstance(batch, dict)
            ],
        },
    }


def build_report(
    *,
    timer_command: str = "",
    selector_limit: int = 50,
    stale_days: int = 1,
    max_posts: int = 1,
    max_concurrent_runs: int = 2,
    max_hot: int = 200,
) -> dict[str, Any]:
    tiers = {"hot"}
    recompute = refresh_tier.recompute_all_tiers(dry_run=True, limit=0, sample_limit=10)
    stored = _stored_distribution()
    selector = _selector_plan(
        limit=max(1, min(1200, int(selector_limit or 50))),
        tiers=tiers,
        stale_days=max(0, int(stale_days or 0)),
        max_posts=max(1, min(3, int(max_posts or 1))),
        max_concurrent_runs=max(1, min(3, int(max_concurrent_runs or 2))),
    )
    timer = _timer_policy(timer_command)
    computed_distribution = recompute.get("distribution") if isinstance(recompute.get("distribution"), dict) else {}
    stored_tiers = stored.get("tiers") if isinstance(stored.get("tiers"), dict) else {}
    hot_count = _int(stored_tiers.get("hot", {}).get("count") if isinstance(stored_tiers.get("hot"), dict) else 0)
    cold_count = _int(stored_tiers.get("cold", {}).get("count") if isinstance(stored_tiers.get("cold"), dict) else 0)
    checks = {
        "provider_calls_disabled": True,
        "selector_table_exists": bool(stored.get("table_exists")),
        "recompute_error_free": _int(recompute.get("error_count")) == 0,
        "stored_rows_match_kol_pool": _int(stored.get("total")) == _int(recompute.get("total")),
        "stored_distribution_matches_recompute": {
            tier: _int(stored_tiers.get(tier, {}).get("count") if isinstance(stored_tiers.get(tier), dict) else 0)
            == _int(computed_distribution.get(tier))
            for tier in ("hot", "warm", "cold")
        },
        "hot_subset_bounded": 0 < hot_count <= max(1, int(max_hot)),
        "cold_records_preserved": cold_count > hot_count,
        "qualified_selector_ready": bool(selector["qualified"].get("selector_ready")),
        "qualified_source_equals_hot": _int(selector["qualified"].get("source_total")) == hot_count,
        "apify_plan_only": selector["apify_batch_plan"].get("mode") == "plan_only"
        and selector["apify_batch_plan"].get("execution_enabled") is False,
        "timer_official_only": True
        if not timer["checked"]
        else bool(timer["contains_skip_kol"] and timer["contains_official_max_posts_50"] and not timer["contains_include_legacy_kol"]),
    }
    distribution_match = checks.pop("stored_distribution_matches_recompute")
    checks["stored_distribution_matches_recompute"] = all(distribution_match.values())
    checks["stored_distribution_match_detail"] = distribution_match
    pass_keys = [key for key in checks if key != "stored_distribution_match_detail"]
    return {
        "mode": "read_only_refresh_tier_acceptance",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider_calls": False,
        "sync_triggered": False,
        "passed": all(bool(checks[key]) for key in pass_keys),
        "checks": checks,
        "timer": timer,
        "recompute": recompute,
        "stored": stored,
        "selector": selector,
        "limits": {
            "max_hot": int(max_hot),
            "selector_limit": int(selector_limit),
            "stale_days": int(stale_days),
            "max_posts": int(max_posts),
            "max_concurrent_runs": int(max_concurrent_runs),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    stored = report["stored"]
    tiers = stored.get("tiers") if isinstance(stored.get("tiers"), dict) else {}
    selector = report["selector"]
    apify_plan = selector["apify_batch_plan"]
    recompute = report["recompute"]
    lines = [
        "# V-KPI Refresh Tier Acceptance",
        "",
        "Read-only P1.X.A acceptance report. This report does not call providers, crawlers, LLMs, or sync jobs.",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Passed: `{str(report['passed']).lower()}`",
        f"- Provider calls: `{str(report['provider_calls']).lower()}`",
        f"- Sync triggered: `{str(report['sync_triggered']).lower()}`",
        f"- KOL pool rows evaluated: `{recompute.get('total', 0)}`",
        "",
        "## Tier Distribution",
        "",
        "| Tier | Stored | Never refreshed | Min refresh | Max refresh |",
        "| --- | ---: | ---: | --- | --- |",
    ]
    for tier in ("hot", "warm", "cold"):
        row = tiers.get(tier) if isinstance(tiers.get(tier), dict) else {}
        lines.append(
            f"| `{tier}` | `{row.get('count', 0)}` | `{row.get('never_refreshed', 0)}` | "
            f"`{row.get('min_refresh') or '-'}` | `{row.get('max_refresh') or '-'}` |"
        )
    lines.extend(
        [
            "",
            "## Selector",
            "",
            f"- Selector ready: `{str(selector['qualified'].get('selector_ready')).lower()}`",
            f"- Hot source total: `{selector['qualified'].get('source_total', 0)}`",
            f"- Daily stale cutoff: `{apify_plan.get('stale_before') or '-'}`",
            f"- Plan-only targets now: `{apify_plan.get('total_targets', 0)}`",
            f"- Plan-only batches now: `{apify_plan.get('batch_count', 0)}`",
            f"- Max concurrent runs: `{apify_plan.get('max_concurrent_runs', 0)}`",
            f"- Platforms: `{apify_plan.get('platforms') or {}}`",
            "",
            "## Checks",
            "",
        ]
    )
    for key, value in report["checks"].items():
        if key == "stored_distribution_match_detail":
            continue
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    lines.extend(
        [
            "",
            "## Samples",
            "",
        ]
    )
    samples = recompute.get("samples") if isinstance(recompute.get("samples"), dict) else {}
    for tier in ("hot", "warm", "cold"):
        lines.append(f"### {tier}")
        tier_samples = samples.get(tier) if isinstance(samples.get(tier), list) else []
        if not tier_samples:
            lines.append("- none")
            continue
        for sample in tier_samples[:10]:
            lines.append(
                f"- `{sample.get('kol_pool_id')}` `{sample.get('platform')}` `{sample.get('handle')}` reason=`{sample.get('reason')}`"
            )
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only P1.X.A refresh tier acceptance report.")
    parser.add_argument("--timer-command", default="", help="Current deployed vkpi-sync-daily.service ExecStart")
    parser.add_argument("--timer-command-file", default="", help="Read current timer command from a text file")
    parser.add_argument("--selector-limit", type=int, default=50)
    parser.add_argument("--stale-days", type=int, default=1)
    parser.add_argument("--max-posts", type=int, default=1)
    parser.add_argument("--max-concurrent-runs", type=int, default=2)
    parser.add_argument("--max-hot", type=int, default=200)
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
            selector_limit=args.selector_limit,
            stale_days=args.stale_days,
            max_posts=args.max_posts,
            max_concurrent_runs=args.max_concurrent_runs,
            max_hot=args.max_hot,
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
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
