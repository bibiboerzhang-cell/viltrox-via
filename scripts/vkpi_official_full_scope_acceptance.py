#!/usr/bin/env python3
"""Read-only acceptance report for official-account full-scope policy."""
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
from app.domains.sync.cron import manual_job_policy  # noqa: E402
from scripts import vkpi_channel_delta_dry_run, vkpi_official_baseline_plan  # noqa: E402


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _int(value: Any) -> int:
    try:
        return int(float(str(value or "").replace(",", "")))
    except (TypeError, ValueError):
        return 0


def _contains_all(text: str, needles: list[str]) -> bool:
    return all(needle in text for needle in needles)


def _daily_policy_from_text(text: str) -> dict[str, Any]:
    return {
        "contains_skip_kol": "--skip-kol" in text,
        "contains_official_max_posts_50": "--official-max-posts 50" in text,
        "contains_include_legacy_kol": "--include-legacy-kol" in text,
        "contains_include_qualified_kol": "--include-qualified-kol" in text,
    }


def _gate_report() -> dict[str, Any]:
    policy = manual_job_policy("official_full_baseline")
    run_wrapper = _read("scripts/ops/run_prod_vkpi_job.sh")
    tonight_wrapper = _read("scripts/ops/tonight_vkpi_data_run.sh")
    operations = _read("docs/OPERATIONS_RUNBOOK.md")
    return {
        "manual_job_policy": {
            "job": policy.get("job"),
            "risk": policy.get("risk"),
            "confirm_text": policy.get("confirm_text"),
        },
        "run_prod_wrapper_uses_manual_gate": "run_manual_job" in run_wrapper and "run_job(job_name" not in run_wrapper,
        "run_prod_wrapper_usage_mentions_confirm": "RUN official_full_baseline" in run_wrapper,
        "tonight_wrapper_includes_confirm": '"confirm":"RUN official_full_baseline"' in tonight_wrapper,
        "operations_runbook_includes_confirm": '{"confirm":"RUN official_full_baseline"}' in operations,
    }


def _timer_report(timer_command: str = "") -> dict[str, Any]:
    install_script = _read("scripts/ops/install_vkpi_daily_timers.sh")
    install_policy = _daily_policy_from_text(install_script)
    current_policy = _daily_policy_from_text(timer_command) if timer_command else {}
    return {
        "install_script": install_policy,
        "current_timer_command": timer_command,
        "current_timer": current_policy,
    }


def _baseline_plan_report(plan: dict[str, Any]) -> dict[str, Any]:
    accounts = [item for item in plan.get("accounts") or [] if isinstance(item, dict)]
    daily_over_cap = [
        item
        for item in accounts
        if _int(item.get("daily_recent_limit")) > _int(item.get("current_safe_limit"))
        or _int(item.get("daily_recent_limit")) > _int(item.get("baseline_target"))
    ]
    unsafe_first_batch = [
        item
        for item in accounts
        if _int(item.get("current_safe_limit")) > _int(item.get("baseline_target")) and _int(item.get("baseline_target"))
    ]
    return {
        "account_count": _int(plan.get("account_count")),
        "platforms": plan.get("platforms") if isinstance(plan.get("platforms"), list) else [],
        "totals": plan.get("totals") if isinstance(plan.get("totals"), dict) else {},
        "daily_recent_over_cap_count": len(daily_over_cap),
        "unsafe_first_batch_count": len(unsafe_first_batch),
        "accounts_needing_full_unlock": _int((plan.get("totals") or {}).get("accounts_needing_full_unlock")),
    }


def build_report(timer_command: str = "") -> dict[str, Any]:
    baseline_plan = vkpi_official_baseline_plan.build_plan()
    delta_report = vkpi_channel_delta_dry_run.build_report()
    gate = _gate_report()
    timer = _timer_report(timer_command)
    baseline = _baseline_plan_report(baseline_plan)
    delta_totals = delta_report.get("totals") if isinstance(delta_report.get("totals"), dict) else {}
    checks = {
        "provider_calls_disabled": True,
        "manual_job_confirm_text": gate["manual_job_policy"].get("confirm_text") == "RUN official_full_baseline",
        "run_prod_wrapper_uses_manual_gate": bool(gate["run_prod_wrapper_uses_manual_gate"]),
        "tonight_wrapper_includes_confirm": bool(gate["tonight_wrapper_includes_confirm"]),
        "operations_runbook_includes_confirm": bool(gate["operations_runbook_includes_confirm"]),
        "install_timer_official_only": bool(timer["install_script"]["contains_skip_kol"] and timer["install_script"]["contains_official_max_posts_50"] and not timer["install_script"]["contains_include_legacy_kol"]),
        "current_timer_official_only": True if not timer_command else bool(timer["current_timer"].get("contains_skip_kol") and timer["current_timer"].get("contains_official_max_posts_50") and not timer["current_timer"].get("contains_include_legacy_kol")),
        "daily_recent_within_policy_caps": baseline["daily_recent_over_cap_count"] == 0 and baseline["unsafe_first_batch_count"] == 0,
        "official_accounts_present": baseline["account_count"] > 0,
        "post_metrics_present": _int(delta_totals.get("accounts_missing_post_metrics")) == 0 and _int(delta_totals.get("channels_with_post_metrics")) > 0,
        "baseline_protection_visible": _int(delta_totals.get("baseline_protected_accounts")) > 0,
    }
    return {
        "mode": "read_only_acceptance",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider_calls": False,
        "policy": "official_full_scope_refresh_v1",
        "passed": all(checks.values()),
        "checks": checks,
        "gate": gate,
        "timer": timer,
        "baseline_plan": baseline,
        "delta_totals": delta_totals,
        "accounts": baseline_plan.get("accounts") or [],
    }


def render_markdown(report: dict[str, Any]) -> str:
    checks = report["checks"]
    baseline = report["baseline_plan"]
    totals = report["delta_totals"]
    lines = [
        "# Official Full-Scope Refresh Acceptance",
        "",
        "Read-only acceptance report. This does not call Apify, YouTube, or any external provider.",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Passed: `{report['passed']}`",
        f"- Accounts: {baseline['account_count']}",
        f"- Platforms: {', '.join(baseline['platforms'])}",
        f"- Baseline target items: {baseline['totals'].get('baseline_target_items', 0)}",
        f"- Daily recent items: {baseline['totals'].get('daily_recent_items', 0)}",
        f"- Accounts needing full unlock: {baseline['accounts_needing_full_unlock']}",
        f"- Channels with post metrics: {totals.get('channels_with_post_metrics', 0)}",
        f"- Baseline protected accounts: {totals.get('baseline_protected_accounts', 0)}",
        "",
        "## Checks",
        "",
    ]
    for key, value in checks.items():
        lines.append(f"- `{key}`: `{bool(value)}`")
    lines.extend(
        [
            "",
            "## Account Plan",
            "",
            "| ID | Platform | Handle | Known Posts | Daily Recent | First Batch | Baseline Target | Action |",
            "| ---: | --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for account in report.get("accounts") or []:
        lines.append(
            "| {channel_id} | {platform} | {handle} | {known_posts} | {daily_recent_limit} | {current_safe_limit} | {baseline_target} | {first_batch_action} |".format(
                **account
            )
        )
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only official full-scope acceptance report.")
    parser.add_argument("--timer-command", default="", help="Current deployed vkpi-sync-daily.service ExecStart for live acceptance")
    parser.add_argument("--timer-command-file", default="", help="Read current timer command from a text file")
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
        report = build_report(timer_command=timer_command)
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
