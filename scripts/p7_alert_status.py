#!/usr/bin/env python3
"""Read-only P7 alert status report."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime, get_conn  # noqa: E402
from app.services.vkpi.alerts import apply_alert_triage_suggestions, build_alert_triage_suggestions  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print a read-only P7 alert status report.")
    parser.add_argument("--limit", type=int, default=20, help="Open alert detail limit")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    parser.add_argument("--md-out", default="", help="Write Markdown report to this path")
    parser.add_argument("--triage-suggestions", action="store_true", help="Print deterministic alert triage suggestions without writing")
    parser.add_argument("--apply-suggestions", action="store_true", help="Apply alert triage suggestions; dry-run unless --confirm")
    parser.add_argument("--suggested-action", default="resolve", help="Suggested action filter for --apply-suggestions")
    parser.add_argument("--status", default="open", help="Alert status filter for suggestions")
    parser.add_argument("--confirm", action="store_true", help="Write --apply-suggestions decisions")
    return parser.parse_args()


def _safe_limit(value: int) -> int:
    try:
        parsed = int(value or 20)
    except (TypeError, ValueError):
        parsed = 20
    return max(1, min(200, parsed))


def _rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in get_conn().execute(query, params).fetchall()]


def build_status(*, limit: int = 20) -> dict[str, Any]:
    safe_limit = _safe_limit(limit)
    by_rule = _rows(
        """
        SELECT COALESCE(NULLIF(rule_key, ''), 'manual') AS rule_key,
               status,
               severity,
               COUNT(*) AS count
        FROM vkpi_alerts
        GROUP BY COALESCE(NULLIF(rule_key, ''), 'manual'), status, severity
        ORDER BY rule_key, status, severity
        """
    )
    open_alerts = _rows(
        """
        SELECT id, alert_key, rule_key, severity, target_type, target_id,
               title, created_at, updated_at
        FROM vkpi_alerts
        WHERE status='open'
        ORDER BY
          CASE severity WHEN 'danger' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
          updated_at DESC,
          id DESC
        LIMIT ?
        """,
        (safe_limit,),
    )
    open_total = int(
        get_conn().execute("SELECT COUNT(*) AS count FROM vkpi_alerts WHERE status='open'").fetchone()["count"] or 0
    )
    cost_row = get_conn().execute(
        "SELECT COUNT(*) AS calls, COALESCE(SUM(cost_usd), 0) AS spend FROM vkpi_ai_cost_ledger"
    ).fetchone()
    budget_row = get_conn().execute(
        """
        SELECT COUNT(*) AS total_scopes,
               SUM(CASE WHEN cap_usd > 0 AND current_spend >= cap_usd * warning_at THEN 1 ELSE 0 END) AS warning_scopes,
               SUM(CASE WHEN cap_usd > 0 AND current_spend >= cap_usd * hard_stop_at THEN 1 ELSE 0 END) AS hard_stop_scopes
        FROM vkpi_provider_budget_caps
        """
    ).fetchone()
    p7_rule_keys = {
        "budget_guard.warning_or_hard_stop",
        "content_brain.analysis_backlog",
        "recommendation.review_gap",
    }
    p7_open = sum(int(row.get("count") or 0) for row in by_rule if row.get("rule_key") in p7_rule_keys and row.get("status") == "open")
    return {
        "open_total": open_total,
        "p7_open_total": p7_open,
        "by_rule": by_rule,
        "open_alerts": open_alerts,
        "ai_cost": {
            "calls": int(cost_row["calls"] or 0) if cost_row else 0,
            "spend": float(cost_row["spend"] or 0) if cost_row else 0.0,
        },
        "budget_caps": {
            "total_scopes": int(budget_row["total_scopes"] or 0) if budget_row else 0,
            "warning_scopes": int(budget_row["warning_scopes"] or 0) if budget_row else 0,
            "hard_stop_scopes": int(budget_row["hard_stop_scopes"] or 0) if budget_row else 0,
        },
    }


def format_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# P7 Alert Status",
        "",
        "```text",
        f"open_total={int(payload.get('open_total') or 0)}",
        f"p7_open_total={int(payload.get('p7_open_total') or 0)}",
        f"ai_cost_calls={int((payload.get('ai_cost') or {}).get('calls') or 0)}",
        f"ai_cost_spend={float((payload.get('ai_cost') or {}).get('spend') or 0.0):.4f}",
        f"budget_warning_scopes={int((payload.get('budget_caps') or {}).get('warning_scopes') or 0)}",
        f"budget_hard_stop_scopes={int((payload.get('budget_caps') or {}).get('hard_stop_scopes') or 0)}",
        "```",
        "",
        "## By Rule",
        "",
    ]
    for row in payload.get("by_rule") or []:
        lines.append(
            f"- {row.get('rule_key', '')}: status={row.get('status', '')} "
            f"severity={row.get('severity', '')} count={int(row.get('count') or 0)}"
        )
    lines.extend(["", "## Open Alerts", ""])
    for row in payload.get("open_alerts") or []:
        lines.append(
            f"- [{row.get('severity', '')}] {row.get('rule_key', '')} "
            f"#{row.get('id')}: {row.get('title', '')}"
        )
    return "\n".join(lines).rstrip() + "\n"


def format_triage_suggestions(payload: dict[str, Any]) -> str:
    lines = [
        "# P7 Alert Triage Suggestions",
        "",
        "```text",
        f"scenario={payload.get('scenario', '')}",
        f"provider_calls={str(bool(payload.get('provider_calls'))).lower()}",
        f"write_db={str(bool(payload.get('write_db'))).lower()}",
        f"count={int(payload.get('count') or 0)}",
    ]
    for action, count in sorted((payload.get("suggested_actions") or {}).items()):
        lines.append(f"suggested.{action}={int(count or 0)}")
    lines.extend(["```", "", "## Suggestions", ""])
    for item in payload.get("suggestions") or []:
        lines.append(
            f"- alert_id={item.get('alert_id')} action={item.get('suggested_action')} "
            f"confidence={float(item.get('confidence') or 0):.2f} "
            f"rule={item.get('rule_key')} reasons={','.join(item.get('reasons') or [])}"
        )
    if not payload.get("suggestions"):
        lines.append("- none")
    return "\n".join(lines).rstrip() + "\n"


def format_apply_suggestions(payload: dict[str, Any]) -> str:
    lines = [
        "# P7 Alert Apply Suggestions",
        "",
        "```text",
        f"scenario={payload.get('scenario', '')}",
        f"provider_calls={str(bool(payload.get('provider_calls'))).lower()}",
        f"write_db={str(bool(payload.get('write_db'))).lower()}",
        f"dry_run={str(bool(payload.get('dry_run'))).lower()}",
        f"candidate_count={int(payload.get('candidate_count') or 0)}",
        f"applied_count={int(payload.get('applied_count') or 0)}",
        f"error_count={int(payload.get('error_count') or 0)}",
        "```",
        "",
        "## Applied",
        "",
    ]
    for item in payload.get("applied") or []:
        lines.append(
            f"- alert_id={item.get('alert_id')} action={item.get('suggested_action')} "
            f"rule={item.get('rule_key')} dry_run={str(bool(item.get('dry_run'))).lower()}"
        )
    if not payload.get("applied"):
        lines.append("- none")
    if payload.get("errors"):
        lines.extend(["", "## Errors", ""])
        for item in payload.get("errors") or []:
            lines.append(f"- alert_id={item.get('alert_id')} error={item.get('error')}")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    try:
        if args.triage_suggestions:
            payload = build_alert_triage_suggestions(status=args.status, limit=args.limit)
            markdown = format_triage_suggestions(payload)
            if args.md_out:
                Path(args.md_out).write_text(markdown, encoding="utf-8")
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
            else:
                print(markdown)
            return 0
        if args.apply_suggestions:
            payload = apply_alert_triage_suggestions(
                status=args.status,
                suggested_action=args.suggested_action,
                limit=args.limit,
                actor="cli",
                dry_run=not args.confirm,
            )
            markdown = format_apply_suggestions(payload)
            if args.md_out:
                Path(args.md_out).write_text(markdown, encoding="utf-8")
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
            else:
                print(markdown)
                if payload.get("dry_run"):
                    print("Add --confirm to write these decisions.")
            return 0
        payload = build_status(limit=args.limit)
        markdown = format_markdown(payload)
        if args.md_out:
            Path(args.md_out).write_text(markdown, encoding="utf-8")
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        else:
            print(markdown)
        return 0
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
