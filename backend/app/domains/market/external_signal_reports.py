"""Report rendering for read-only external market signal smokes."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def render_external_signal_smoke_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Market External Signal Smoke v0",
        "",
        f"- generated_at: `{report.get('generated_at')}`",
        f"- passed: `{report.get('passed')}`",
        f"- provider_calls: `{report.get('provider_calls')}`",
        f"- external_http_calls: `{report.get('external_http_calls')}`",
        f"- write_db: `{report.get('write_db')}`",
        f"- items_loaded: `{summary.get('items_loaded')}`",
        f"- business_signal_items: `{summary.get('business_signal_items')}`",
        f"- tier1_mentions: `{summary.get('tier1_mentions')}`",
        f"- viltrox_product_mentions: `{summary.get('viltrox_product_mentions')}`",
        "",
        "## Source Status",
        "",
        "| Source | Provider | Type | Status | Allowlisted |",
        "|---|---|---|---|---|",
    ]
    for source in report.get("source_statuses") or []:
        if not isinstance(source, dict):
            continue
        lines.append(
            f"| {source.get('source_key')} | `{source.get('provider')}` | `{source.get('source_type')}` | `{source.get('status')}` | `{source.get('allowlisted')}` |"
        )
    lines.extend(["", "## Top Candidates", ""])
    for item in report.get("top_candidates") or []:
        if not isinstance(item, dict):
            continue
        lines.append(
            "- `{provider}` `{score}` {title} ({url})".format(
                provider=item.get("provider"),
                score=item.get("score"),
                title=item.get("title"),
                url=item.get("source_url"),
            )
        )
    lines.extend(["", "## Checks", ""])
    for name, value in (report.get("checks") or {}).items():
        lines.append(f"- `{name}`: `{value}`")
    return "\n".join(lines) + "\n"


def render_external_daily_candidate_plan_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Market External Daily Candidate Plan v0",
        "",
        f"- generated_at: `{report.get('generated_at')}`",
        f"- passed: `{report.get('passed')}`",
        f"- provider_calls: `{report.get('provider_calls')}`",
        f"- external_http_calls: `{report.get('external_http_calls')}`",
        f"- write_db: `{report.get('write_db')}`",
        f"- planned_http_calls: `{summary.get('planned_http_calls')}` / `{summary.get('max_http_calls')}`",
        f"- planned_item_limit: `{summary.get('planned_item_limit')}`",
        f"- estimated_cost_usd: `{summary.get('estimated_cost_usd')}`",
        f"- blocked_auto_run: `{summary.get('blocked_auto_run')}`",
        "",
        "## Candidate Groups",
        "",
        "| Group | Priority | HTTP | Item Limit | Reason |",
        "|---|---|---:|---:|---|",
    ]
    for group in report.get("groups") or []:
        if not isinstance(group, dict):
            continue
        lines.append(
            "| {label} | `{priority}` | {calls} | {limit} | {reason} |".format(
                label=group.get("label"),
                priority=group.get("priority"),
                calls=group.get("planned_http_calls"),
                limit=group.get("planned_item_limit"),
                reason=group.get("reason"),
            )
        )
    lines.extend(["", "## Planned Sources", "", "| Source | Group | Provider | Type | Query / URL |", "|---|---|---|---|---|"])
    for source in report.get("planned_sources") or []:
        if not isinstance(source, dict):
            continue
        descriptor = source.get("query") or source.get("url")
        lines.append(
            f"| {source.get('source_key')} | `{source.get('source_group')}` | `{source.get('provider')}` | `{source.get('source_type')}` | {descriptor} |"
        )
    lines.extend(["", "## Checks", ""])
    for name, value in (report.get("checks") or {}).items():
        lines.append(f"- `{name}`: `{value}`")
    return "\n".join(lines) + "\n"


def write_external_daily_candidate_plan(report: dict[str, Any], *, out_dir: str | Path = "runtime/ops") -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out / f"{stamp}-market-external-daily-plan-v0.json"
    md_path = out / f"{stamp}-market-external-daily-plan-v0.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_external_daily_candidate_plan_markdown(report), encoding="utf-8")
    return {"json_path": str(json_path.resolve()), "md_path": str(md_path.resolve())}


def write_external_signal_smoke(report: dict[str, Any], *, out_dir: str | Path = "runtime/ops") -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out / f"{stamp}-market-external-signal-smoke-v0.json"
    md_path = out / f"{stamp}-market-external-signal-smoke-v0.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_external_signal_smoke_markdown(report), encoding="utf-8")
    return {"json_path": str(json_path.resolve()), "md_path": str(md_path.resolve())}
