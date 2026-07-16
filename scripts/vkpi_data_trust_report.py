#!/usr/bin/env python3
"""Read-only P1 data trust report for V-KPI.

This aggregates the existing official-channel trust audits. It does not call
Apify, YouTube, Gemini, LLMs, crawlers, or sync jobs.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

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
    check_silent_exception_baseline,
    vkpi_channel_delta_dry_run,
    vkpi_official_comment_contract_audit,
    vkpi_official_full_scope_acceptance,
    vkpi_official_media_status_audit,
    vkpi_official_post_identity_audit,
)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "")))
    except (TypeError, ValueError):
        return int(default or 0)


def _bool(value: Any) -> bool:
    return bool(value)


def _silent_exception_report() -> dict[str, Any]:
    findings = []
    for path in check_silent_exception_baseline._iter_python_files(check_silent_exception_baseline.BACKEND_APP):
        findings.extend(check_silent_exception_baseline._find_silent_exceptions(path))
    baseline_path = ROOT / "scripts" / "silent_exception_baseline.json"
    max_total = 0
    if baseline_path.exists():
        max_total = _int(json.loads(baseline_path.read_text(encoding="utf-8")).get("max_total"))
    files: dict[str, int] = {}
    for finding in findings:
        files.setdefault(finding.path, 0)
        files[finding.path] += 1
    return {
        "passed": len(findings) <= max_total,
        "total": len(findings),
        "max_total": max_total,
        "files": files,
        "findings": [finding.__dict__ for finding in findings],
    }


def _delta_check(report: dict[str, Any]) -> dict[str, Any]:
    totals = report.get("totals") if isinstance(report.get("totals"), dict) else {}
    return {
        "passed": _int(totals.get("accounts")) > 0
        and _int(totals.get("channels_with_post_metrics")) > 0
        and _int(totals.get("accounts_missing_post_metrics")) == 0,
        "accounts": _int(totals.get("accounts")),
        "channels_with_post_metrics": _int(totals.get("channels_with_post_metrics")),
        "post_metric_rows": _int(totals.get("post_metric_rows")),
        "baseline_protected_accounts": _int(totals.get("baseline_protected_accounts")),
        "views_delta": _int(totals.get("views_delta")),
        "likes_delta": _int(totals.get("likes_delta")),
        "comments_delta": _int(totals.get("comments_delta")),
        "accounts_missing_post_metrics": _int(totals.get("accounts_missing_post_metrics")),
    }


def _media_check(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "passed": _bool(report.get("passed")),
        "accounts": _int(report.get("account_count")),
        "posts": _int(report.get("post_count")),
        "status_counts": report.get("status_counts") if isinstance(report.get("status_counts"), dict) else {},
        "missing_status": len(report.get("missing_status") or []),
    }


def _comment_check(report: dict[str, Any]) -> dict[str, Any]:
    totals = report.get("totals") if isinstance(report.get("totals"), dict) else {}
    return {
        "passed": _bool(report.get("passed")),
        "accounts": _int(report.get("account_count")),
        "posts": _int(totals.get("posts")),
        "posts_with_declared_comments": _int(totals.get("posts_with_declared_comments")),
        "declared_comments": _int(totals.get("declared_comments")),
        "cached_comment_bodies_total": _int(totals.get("cached_comment_bodies_total")),
        "missing_contract": _int(totals.get("missing_contract")),
        "status_counts": report.get("status_counts") if isinstance(report.get("status_counts"), dict) else {},
    }


def _identity_check(report: dict[str, Any]) -> dict[str, Any]:
    totals = report.get("totals") if isinstance(report.get("totals"), dict) else {}
    return {
        "passed": _bool(report.get("passed")),
        "accounts": _int(report.get("account_count")),
        "posts": _int(totals.get("posts")),
        "missing_canonical_uid": _int(totals.get("missing_canonical_uid")),
        "missing_provider_post_id": _int(totals.get("missing_provider_post_id")),
        "account_duplicate_canonical_uid_count": _int(totals.get("account_duplicate_canonical_uid_count")),
        "global_duplicate_canonical_uid_count": _int(totals.get("global_duplicate_canonical_uid_count")),
        "metric_rows_matched": _int(totals.get("metric_rows_matched")),
        "metric_rows": _int(totals.get("metric_rows")),
    }


def _full_scope_check(report: dict[str, Any]) -> dict[str, Any]:
    checks = report.get("checks") if isinstance(report.get("checks"), dict) else {}
    baseline = report.get("baseline_plan") if isinstance(report.get("baseline_plan"), dict) else {}
    delta = report.get("delta_totals") if isinstance(report.get("delta_totals"), dict) else {}
    return {
        "passed": _bool(report.get("passed")),
        "checks": checks,
        "accounts": _int(baseline.get("account_count")),
        "accounts_needing_full_unlock": _int(baseline.get("accounts_needing_full_unlock")),
        "channels_with_post_metrics": _int(delta.get("channels_with_post_metrics")),
        "baseline_protected_accounts": _int(delta.get("baseline_protected_accounts")),
    }


def build_report(*, timer_command: str = "") -> dict[str, Any]:
    delta_report = vkpi_channel_delta_dry_run.build_report()
    media_report = vkpi_official_media_status_audit.build_report(limit=50)
    comment_report = vkpi_official_comment_contract_audit.build_report(limit_per_account=50, comment_cap=300)
    identity_report = vkpi_official_post_identity_audit.build_report(limit_per_account=50)
    full_scope_report = vkpi_official_full_scope_acceptance.build_report(timer_command=timer_command)
    silent_report = _silent_exception_report()
    checks = {
        "provider_calls_disabled": True,
        "delta": _delta_check(delta_report),
        "media": _media_check(media_report),
        "comments": _comment_check(comment_report),
        "identity": _identity_check(identity_report),
        "full_scope": _full_scope_check(full_scope_report),
        "silent_exceptions": silent_report,
    }
    passed = all(
        bool(checks[key].get("passed"))
        for key in ("delta", "media", "comments", "identity", "full_scope", "silent_exceptions")
    )
    return {
        "mode": "read_only_vkpi_data_trust_report",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider_calls": False,
        "crawler_calls": False,
        "sync_triggered": False,
        "passed": passed,
        "timer_command_checked": bool(str(timer_command or "").strip()),
        "checks": checks,
        "reports": {
            "delta": delta_report,
            "media": media_report,
            "comments": comment_report,
            "identity": identity_report,
            "full_scope": full_scope_report,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    checks = report["checks"]
    delta = checks["delta"]
    media = checks["media"]
    comments = checks["comments"]
    identity = checks["identity"]
    full_scope = checks["full_scope"]
    silent = checks["silent_exceptions"]
    lines = [
        "# V-KPI Data Trust Report",
        "",
        "Read-only P1 acceptance report. This report does not call providers, crawlers, LLMs, or sync jobs.",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Passed: `{str(report['passed']).lower()}`",
        f"- Provider calls: `{str(report['provider_calls']).lower()}`",
        f"- Sync triggered: `{str(report['sync_triggered']).lower()}`",
        f"- Timer command checked: `{str(report['timer_command_checked']).lower()}`",
        "",
        "## Gate Summary",
        "",
        "| Area | Passed | Key Evidence |",
        "| --- | --- | --- |",
        (
            f"| Post-level delta | `{str(delta['passed']).lower()}` | "
            f"accounts={delta['accounts']}, post_rows={delta['post_metric_rows']}, "
            f"missing_post_metrics={delta['accounts_missing_post_metrics']} |"
        ),
        (
            f"| Media status | `{str(media['passed']).lower()}` | "
            f"posts={media['posts']}, missing_status={media['missing_status']}, statuses={media['status_counts']} |"
        ),
        (
            f"| Comment contract | `{str(comments['passed']).lower()}` | "
            f"posts={comments['posts']}, declared={comments['declared_comments']}, cached={comments['cached_comment_bodies_total']} |"
        ),
        (
            f"| Canonical post identity | `{str(identity['passed']).lower()}` | "
            f"posts={identity['posts']}, missing_uid={identity['missing_canonical_uid']}, "
            f"account_dupes={identity['account_duplicate_canonical_uid_count']}, "
            f"metric_match={identity['metric_rows_matched']}/{identity['metric_rows']} |"
        ),
        (
            f"| Full-scope official policy | `{str(full_scope['passed']).lower()}` | "
            f"accounts={full_scope['accounts']}, unlock_needed={full_scope['accounts_needing_full_unlock']}, "
            f"baseline_protected={full_scope['baseline_protected_accounts']} |"
        ),
        (
            f"| Silent exceptions | `{str(silent['passed']).lower()}` | "
            f"total={silent['total']}, max={silent['max_total']} |"
        ),
        "",
        "## Remaining Notes",
        "",
        "- Global duplicate canonical post IDs are reported by the identity audit but do not fail the gate; the same public Instagram post can appear in multiple official account packages.",
        "- Comment coverage is a contract gate, not a claim that every declared comment body is cached.",
        "- Baseline protection is expected when provider samples are narrower than cumulative historical floors.",
    ]
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only V-KPI P1 data trust report.")
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
            stdout_out(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        else:
            stdout_out(markdown)
        return 0 if report.get("passed") else 3
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
