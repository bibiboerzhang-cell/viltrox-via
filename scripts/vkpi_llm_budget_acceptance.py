#!/usr/bin/env python3
"""Read-only P4 LLM gateway budget acceptance report.

This report verifies the budget gates needed before any later LLM/Gemini live
test. It does not call providers, enqueue tasks, run sync jobs, or write ledger
rows.
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
from app.platform import llm_gateway  # noqa: E402
from app.domains.costs import budget_guard  # noqa: E402


REQUIRED_SCOPES = (
    "monthly_total",
    "single_call",
    "provider:openai",
    "provider:gemini",
    "provider:claude",
    "cron:p4_evidence_summary",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def build_report(*, prompt: str, max_output_tokens: int = 200) -> dict[str, Any]:
    budget_guard.ensure_budget_schema()
    statuses = {
        scope: budget_guard.get_budget_status(scope, estimated_cost=0.01)
        for scope in REQUIRED_SCOPES
    }
    preflight = llm_gateway.budget_preflight(
        prompt,
        purpose="p4_evidence_summary",
        max_output_tokens=max_output_tokens,
        cost_tag="cron:p4_evidence_summary",
    )
    providers = preflight.get("providers") if isinstance(preflight.get("providers"), list) else []
    checks = {
        "provider_calls_blocked": not bool(preflight.get("provider_calls_allowed")),
        "required_scopes_configured": all(bool(statuses[scope].get("configured")) for scope in REQUIRED_SCOPES),
        "single_call_cap_configured": bool(statuses["single_call"].get("configured"))
        and _float(statuses["single_call"].get("cap_usd")) > 0,
        "single_call_in_every_provider_plan": bool(providers)
        and all("single_call" in (provider.get("scopes") or []) for provider in providers),
        "monthly_total_required": all("monthly_total" in (provider.get("scopes") or []) for provider in providers),
        "provider_scope_required": all(
            any(str(scope).startswith("provider:") for scope in (provider.get("scopes") or []))
            for provider in providers
        ),
        "task_scope_required": all("cron:p4_evidence_summary" in (provider.get("scopes") or []) for provider in providers),
        "no_provider_calls": True,
        "no_ledger_writes": True,
        "no_sync_or_task_side_effect": True,
    }
    return {
        "mode": "read_only_llm_gateway_budget_acceptance_v0",
        "generated_at": _now(),
        "provider_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "required_scopes": statuses,
        "preflight": preflight,
        "usage_by_provider": budget_guard.usage_by_provider(limit=20),
        "usage_by_cron": budget_guard.usage_by_cron(limit=20),
    }


def render_markdown(report: dict[str, Any]) -> str:
    preflight = report["preflight"]
    lines = [
        "# V-KPI LLM Gateway Budget Acceptance",
        "",
        "Read-only P4 budget report. It does not call providers, write ledgers, enqueue tasks, or run sync.",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Passed: `{str(report['passed']).lower()}`",
        f"- Provider calls: `{str(report['provider_calls']).lower()}`",
        f"- Provider gate: `{preflight.get('provider_gate_reason')}`",
        f"- Monthly env budget: `${_float(preflight.get('monthly_env_budget_usd')):.2f}`",
        f"- Single-call scope: `{preflight.get('single_call_scope')}`",
        "",
        "## Required Scopes",
        "",
        "| Scope | Configured | Cap | Current | Allowed for $0.01 |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for scope, status in report["required_scopes"].items():
        lines.append(
            f"| `{scope}` | `{str(bool(status.get('configured'))).lower()}` | "
            f"${_float(status.get('cap_usd')):.2f} | ${_float(status.get('current_spend')):.4f} | "
            f"`{str(bool(status.get('allowed'))).lower()}` |"
        )
    lines.extend(["", "## Provider Preflight", "", "| Provider | Configured | Estimated | Budget | Calls Allowed | Scopes |", "| --- | --- | ---: | --- | --- | --- |"])
    for provider in preflight.get("providers") or []:
        lines.append(
            f"| `{provider.get('provider')}` | `{str(bool(provider.get('configured'))).lower()}` | "
            f"${_float(provider.get('estimated_cost_usd')):.2f} | `{str(bool(provider.get('budget_allowed'))).lower()}` | "
            f"`{str(bool(provider.get('provider_calls_allowed'))).lower()}` | `{','.join(provider.get('scopes') or [])}` |"
        )
    lines.extend(["", "## Checks", ""])
    for key, value in report["checks"].items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only V-KPI LLM budget acceptance report.")
    parser.add_argument("--prompt", default="Summarize existing evidence only. Do not create new facts.")
    parser.add_argument("--max-output-tokens", type=int, default=200)
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
        report = build_report(
            prompt=str(args.prompt or ""),
            max_output_tokens=max(1, min(4000, int(args.max_output_tokens or 200))),
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
