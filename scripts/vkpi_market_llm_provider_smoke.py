#!/usr/bin/env python3
"""V-KPI market data and LLM provider smoke report.

Default mode is read-only: no provider calls, no LLM calls, no DB writes, no
sync jobs. Use --execute-live-probe for one low-cost provider-health probe and
--execute-llm-single for one budget-gated LLM call.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# Path patch must precede this import: under pytest the module loads as
# scripts.<name>, so scripts/ is not sys.path[0] and stdout_utils would
# raise ModuleNotFoundError at collection time.
from stdout_utils import out as stdout_out  # noqa: E402

from app.db.connection import close_db_runtime  # noqa: E402
from app.services.system import provider_health  # noqa: E402
from app.domains.market import provider_preflight as market_provider_preflight  # noqa: E402
from app.domains.market import llm_quality as market_llm_quality  # noqa: E402
from app.platform import llm_gateway  # noqa: E402


logging.getLogger("httpx").setLevel(logging.WARNING)

LIVE_PROBE_PROVIDER_MAP = {
    "google_gemini_llm": "google",
    "google_youtube_data": "youtube",
    "apify_market_fallback": "apify",
    "openai_llm": "openai",
    "anthropic_llm": "anthropic",
}


def _safe_env_value(names: list[str]) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    env_values = market_provider_preflight._env_file_values()
    for name in names:
        value = str(env_values.get(name) or "").strip()
        if value:
            return value
    return ""


def _source_by_key(report: dict[str, Any], source_key: str) -> dict[str, Any] | None:
    for source in report.get("sources") or []:
        if source.get("source_key") == source_key:
            return source
    return None


async def _execute_live_probe(report: dict[str, Any], source_key: str) -> dict[str, Any]:
    source = _source_by_key(report, source_key)
    if not source:
        return {"ok": False, "status": "skipped", "error": f"unknown source_key {source_key}", "provider_calls": False}
    provider = LIVE_PROBE_PROVIDER_MAP.get(source_key)
    if not provider:
        return {"ok": False, "status": "skipped", "error": "no low-cost provider probe defined", "provider_calls": False}
    if not source.get("configured"):
        return {"ok": False, "status": "not_configured", "error": "required env keys missing", "provider_calls": False}
    api_key = _safe_env_value(list(source.get("env_any") or []) + list(source.get("env_all") or []))
    result = await provider_health.probe_provider(provider, api_key=api_key or None)
    return {**result, "source_key": source_key, "provider_calls": True, "write_db": False}


def _execute_llm_single(prompt: str, preferred_provider: str, max_output_tokens: int) -> dict[str, Any]:
    return llm_gateway.invoke(
        prompt,
        purpose="market_provider_smoke",
        max_output_tokens=max_output_tokens,
        preferred_provider=preferred_provider,
        skip_budget_check=False,
        cost_tag="cron:market_provider_smoke",
        metadata={"manual_live_smoke": True, "script": "vkpi_market_llm_provider_smoke.py"},
    )


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# V-KPI Market + LLM Provider Smoke",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Mode: `{report.get('mode')}`",
        f"- Provider calls: `{report.get('provider_calls')}`",
        f"- LLM calls: `{report.get('llm_calls')}`",
        f"- External HTTP calls: `{report.get('external_http_calls')}`",
        f"- DB writes: `{report.get('write_db')}`",
        f"- Sync triggered: `{report.get('sync_triggered')}`",
        f"- Passed: `{report.get('passed')}`",
        "",
        "## Summary",
        "",
        f"- Sources: `{summary.get('source_count')}`",
        f"- Configured sources: `{summary.get('configured_sources')}`",
        f"- Partial sources: `{summary.get('partial_sources')}`",
        f"- Not configured sources: `{summary.get('not_configured_sources')}`",
        f"- LLM gate: `{summary.get('llm_gate_reason')}`",
        f"- LLM provider calls allowed: `{summary.get('llm_provider_calls_allowed')}`",
        "",
        "## Source Matrix",
        "",
        "| Source | Provider | Category | Readiness | Gate |",
        "|---|---|---|---|---|",
    ]
    for source in report.get("sources") or []:
        lines.append(
            "| {label} | `{provider}` | `{category}` | `{readiness}` | `{gate}` |".format(
                label=source.get("label"),
                provider=source.get("provider"),
                category=source.get("category"),
                readiness=source.get("readiness"),
                gate=source.get("execution_gate"),
            )
        )
    lines.extend(["", "## Checks", ""])
    for name, value in (report.get("checks") or {}).items():
        lines.append(f"- `{name}`: `{value}`")
    live_probe = report.get("live_probe_result")
    if live_probe:
        lines.extend(["", "## Live Probe", "", "```json", json.dumps(live_probe, ensure_ascii=False, indent=2), "```"])
    llm_result = report.get("llm_single_result")
    if llm_result:
        quality_gate = report.get("llm_quality_gate")
        if quality_gate:
            lines.extend(
                [
                    "",
                    "## LLM Quality Gate",
                    "",
                    f"- Usable for UI: `{quality_gate.get('usable_for_ui')}`",
                    f"- Status: `{quality_gate.get('status')}`",
                    f"- Reasons: `{', '.join(quality_gate.get('reasons') or [])}`",
                ]
            )
        safe_llm = {
            key: value
            for key, value in llm_result.items()
            if key not in {"prompt", "raw", "request", "response"}
        }
        lines.extend(["", "## LLM Single Result", "", "```json", json.dumps(safe_llm, ensure_ascii=False, indent=2), "```"])
    return "\n".join(lines) + "\n"


async def build_report(args: argparse.Namespace) -> dict[str, Any]:
    report = market_provider_preflight.build_provider_preflight(
        prompt=args.prompt,
        preferred_llm_provider=args.preferred_provider,
        max_output_tokens=args.max_output_tokens,
    )
    if args.execute_live_probe:
        probe_result = await _execute_live_probe(report, args.live_source_key)
        report["provider_calls"] = bool(probe_result.get("provider_calls"))
        report["external_http_calls"] = bool(probe_result.get("provider_calls"))
        report["live_probe_result"] = probe_result
    if args.execute_llm_single:
        llm_result = _execute_llm_single(args.prompt, args.preferred_provider, args.max_output_tokens)
        llm_provider_called = str(llm_result.get("provider") or "") != "rule_v0"
        quality_gate = market_llm_quality.evaluate_market_llm_output(llm_result)
        report["llm_calls"] = llm_provider_called
        report["provider_calls"] = bool(report.get("provider_calls")) or llm_provider_called
        report["external_http_calls"] = bool(report.get("external_http_calls")) or llm_provider_called
        report["write_db"] = True
        report["llm_single_result"] = llm_result
        report["llm_quality_gate"] = quality_gate
        report.setdefault("summary", {})["llm_output_usable_for_ui"] = quality_gate["usable_for_ui"]
        report.setdefault("checks", {})["llm_output_quality_gate_present"] = True
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", default="", help="Write JSON report to this path")
    parser.add_argument("--md-out", default="", help="Write Markdown report to this path")
    parser.add_argument("--prompt", default=market_provider_preflight.DEFAULT_PROMPT)
    parser.add_argument("--preferred-provider", default="google", choices=["google", "openai", "anthropic"])
    parser.add_argument("--max-output-tokens", type=int, default=160)
    parser.add_argument("--execute-live-probe", action="store_true", help="Run one low-cost provider-health HTTP probe")
    parser.add_argument("--live-source-key", default="google_gemini_llm", choices=sorted(LIVE_PROBE_PROVIDER_MAP.keys()))
    parser.add_argument("--execute-llm-single", action="store_true", help="Run one budget-gated LLM invocation and write ledger rows")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = asyncio.run(build_report(args))
        if args.json_out:
            out = Path(args.json_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.md_out:
            out = Path(args.md_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(render_markdown(report), encoding="utf-8")
        stdout_out(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("passed") else 2
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
