#!/usr/bin/env python3
"""Read-only P4.58 Gemini 30-KOL batch dry-run report.

This script plans a future 30-KOL Gemini deep-scan batch without creating an
executor. It does not call Gemini, enqueue tasks, run sync, call Apify, or write
database rows.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
ADR_PATH = ROOT / "docs" / "vkpi" / "adr" / "2026-05-23-gemini-batch30-dry-run.md"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.connection import close_db_runtime  # noqa: E402
from app.domains.intelligence import gemini_single_kol_preflight  # noqa: E402
from app.services.vkpi import natural_search  # noqa: E402


MAX_TARGET_KOLS = 30
DEFAULT_TARGET_KOLS = 30
DEFAULT_WINDOW_SIZE = 5
MAX_WINDOW_SIZE = 5
INITIAL_CONCURRENCY = 1
HARD_MAX_CONCURRENCY = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int(value: Any) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _ids_from_text(value: str) -> list[int]:
    ids: list[int] = []
    for part in str(value or "").replace(";", ",").split(","):
        parsed = _int(part)
        if parsed > 0 and parsed not in ids:
            ids.append(parsed)
    return ids


def _candidate_ids_from_search(query: str, *, limit: int) -> list[int]:
    payload = natural_search.search(query, limit=max(1, min(MAX_TARGET_KOLS, int(limit or DEFAULT_TARGET_KOLS))))
    ids: list[int] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict) or item.get("source_table") != "vkpi_kol_pool":
            continue
        kol_pool_id = _int(item.get("source_id"))
        if kol_pool_id > 0 and kol_pool_id not in ids:
            ids.append(kol_pool_id)
    return ids


def _bounded_policy(*, requested_target_size: int, requested_window_size: int, requested_concurrency: int) -> dict[str, Any]:
    effective_target_size = max(1, min(MAX_TARGET_KOLS, int(requested_target_size or DEFAULT_TARGET_KOLS)))
    effective_window_size = max(1, min(MAX_WINDOW_SIZE, int(requested_window_size or DEFAULT_WINDOW_SIZE), effective_target_size))
    effective_concurrency = max(1, min(HARD_MAX_CONCURRENCY, int(requested_concurrency or INITIAL_CONCURRENCY)))
    return {
        "stage": "P4.58",
        "mode": "dry_run_only",
        "batch_execution_allowed": False,
        "executor_exists": False,
        "provider_calls_allowed": False,
        "requires_single_live_result_before_batch": True,
        "requires_single_live_review_before_batch": True,
        "requested_target_size": int(requested_target_size or DEFAULT_TARGET_KOLS),
        "effective_target_size": effective_target_size,
        "max_target_size": MAX_TARGET_KOLS,
        "requested_window_size": int(requested_window_size or DEFAULT_WINDOW_SIZE),
        "effective_window_size": effective_window_size,
        "max_window_size": MAX_WINDOW_SIZE,
        "requested_concurrency": int(requested_concurrency or INITIAL_CONCURRENCY),
        "effective_concurrency": effective_concurrency,
        "initial_concurrency": INITIAL_CONCURRENCY,
        "hard_max_concurrency": HARD_MAX_CONCURRENCY,
        "minimum_delay_seconds_between_starts": 60,
        "stop_conditions": {
            "errors_gte": 3,
            "error_rate_gte": 0.20,
            "budget_warning_or_hard_stop": True,
            "provider_quota_or_rate_limit": True,
            "single_live_result_not_reviewed": True,
        },
        "retry_policy": {
            "max_retries_per_kol": 2,
            "backoff_minutes": [15, 60],
            "retryable": ["network_timeout", "provider_5xx", "temporary_file_processing_failure"],
            "not_retryable": [
                "no_cached_video_candidates",
                "invalid_video_url",
                "provider_not_configured",
                "budget_hard_stop",
                "quota_hard_stop",
                "model_configuration_error",
            ],
        },
        "required_budget_scopes_before_future_executor": [
            "monthly_total",
            "single_call",
            "provider:gemini",
            "cron:p4_gemini_single_kol",
            "cron:p4_gemini_batch_30_future",
        ],
    }


def _decision_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(item.get("decision") or "unknown") for item in items)
    return dict(sorted(counts.items()))


def _blocker_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in items:
        blockers = item.get("blockers") if isinstance(item.get("blockers"), list) else []
        for blocker in blockers:
            text = str(blocker or "").strip()
            if text:
                counts[text] += 1
    return dict(sorted(counts.items()))


def _summarize_report(kol_pool_id: int, report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    blockers = report.get("blockers") if isinstance(report.get("blockers"), list) else []
    return {
        "kol_pool_id": int(kol_pool_id),
        "decision": report.get("decision") or "unknown",
        "decision_reason": report.get("decision_reason") or "",
        "blockers": blockers,
        "summary": {
            "candidate_count": _int(summary.get("candidate_count")),
            "valid_video_url": bool(summary.get("valid_video_url")),
            "provider_path": summary.get("provider_path") or "",
            "top_video_url": summary.get("top_video_url") or "",
            "provider_calls_allowed": bool(summary.get("provider_calls_allowed")),
            "ready_for_manual_live_test": bool(summary.get("ready_for_manual_live_test")),
            "provider_gate_reason": summary.get("provider_gate_reason") or "",
        },
        "provider_calls": bool(report.get("provider_calls")),
        "llm_calls": bool(report.get("llm_calls")),
        "write_db": bool(report.get("write_db")),
        "sync_triggered": bool(report.get("sync_triggered")),
        "task_enqueued": bool(report.get("task_enqueued")),
    }


def _windows(target_ids: list[int], *, window_size: int) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for index in range(0, len(target_ids), max(1, int(window_size or DEFAULT_WINDOW_SIZE))):
        chunk = target_ids[index : index + max(1, int(window_size or DEFAULT_WINDOW_SIZE))]
        windows.append(
            {
                "window_index": len(windows) + 1,
                "kol_pool_ids": chunk,
                "target_count": len(chunk),
                "execution_enabled": False,
                "execution_command": "",
                "note": "Planning window only. No executor exists and no provider calls are allowed.",
            }
        )
    return windows


def _readiness(*, target_count: int, decisions: dict[str, int], side_effect_guard_passed: bool) -> str:
    if not side_effect_guard_passed:
        return "failed_side_effect_guard"
    if target_count <= 0:
        return "blocked_no_eligible_candidates"
    if decisions.get("hold", 0) > 0:
        return "blocked_provider_or_budget_hold"
    if decisions.get("go_manual_single_call", 0) > 0:
        return "blocked_single_live_review_required"
    if decisions.get("no_go_for_this_kol", 0) > 0 or decisions.get("not_found", 0) > 0:
        return "blocked_no_eligible_candidates"
    return "blocked_no_eligible_candidates"


def build_report(
    *,
    query: str = "viltrox",
    kol_pool_ids: list[int] | None = None,
    candidate_limit: int = 24,
    target_size: int = DEFAULT_TARGET_KOLS,
    window_size: int = DEFAULT_WINDOW_SIZE,
    requested_concurrency: int = INITIAL_CONCURRENCY,
) -> dict[str, Any]:
    policy = _bounded_policy(
        requested_target_size=target_size,
        requested_window_size=window_size,
        requested_concurrency=requested_concurrency,
    )
    source_ids = list(dict.fromkeys(int(value) for value in (kol_pool_ids or []) if int(value or 0) > 0))
    candidate_source = "explicit_ids" if source_ids else "natural_search"
    if not source_ids:
        source_ids = _candidate_ids_from_search(query, limit=int(policy["effective_target_size"]))
    target_ids = source_ids[: int(policy["effective_target_size"])]
    items: list[dict[str, Any]] = []
    for kol_pool_id in target_ids:
        try:
            report = gemini_single_kol_preflight.build_kol_pool_gemini_go_no_go(
                int(kol_pool_id),
                candidate_limit=max(1, min(100, int(candidate_limit or 24))),
            )
        except LookupError as exc:
            report = {
                "kol_pool_id": int(kol_pool_id),
                "decision": "not_found",
                "decision_reason": str(exc),
                "blockers": ["kol_pool_not_found"],
                "provider_calls": False,
                "llm_calls": False,
                "write_db": False,
                "sync_triggered": False,
                "task_enqueued": False,
                "summary": {},
            }
        items.append(_summarize_report(int(kol_pool_id), report))
    decisions = _decision_counts(items)
    blockers = _blocker_counts(items)
    windows = _windows(target_ids, window_size=int(policy["effective_window_size"]))
    side_effect_guard_passed = not any(
        bool(item.get(key))
        for item in items
        for key in ("provider_calls", "llm_calls", "write_db", "sync_triggered", "task_enqueued")
    )
    checks = {
        "adr_exists": ADR_PATH.exists(),
        "target_count_bounded": len(target_ids) <= MAX_TARGET_KOLS,
        "window_size_bounded": int(policy["effective_window_size"]) <= MAX_WINDOW_SIZE,
        "concurrency_bounded": int(policy["effective_concurrency"]) <= HARD_MAX_CONCURRENCY,
        "batch_execution_blocked": bool(policy["batch_execution_allowed"]) is False,
        "executor_absent": bool(policy["executor_exists"]) is False,
        "single_live_review_required": bool(policy["requires_single_live_review_before_batch"]),
        "no_execution_commands": not any(bool(window.get("execution_command")) for window in windows),
        "provider_calls_blocked": not any(bool(item.get("provider_calls")) for item in items),
        "llm_calls_blocked": not any(bool(item.get("llm_calls")) for item in items),
        "write_db_blocked": not any(bool(item.get("write_db")) for item in items),
        "sync_blocked": not any(bool(item.get("sync_triggered")) for item in items),
        "task_enqueue_blocked": not any(bool(item.get("task_enqueued")) for item in items),
        "stop_conditions_defined": bool(policy["stop_conditions"].get("errors_gte") == 3),
        "retry_policy_defined": bool(policy["retry_policy"].get("max_retries_per_kol") == 2),
    }
    return {
        "mode": "read_only_p4_58_gemini_batch30_dry_run",
        "generated_at": _now(),
        "query": query,
        "candidate_source": candidate_source,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "batch_execution_allowed": False,
        "passed": all(bool(value) for value in checks.values()),
        "readiness": _readiness(
            target_count=len(target_ids),
            decisions=decisions,
            side_effect_guard_passed=side_effect_guard_passed,
        ),
        "checks": checks,
        "policy": policy,
        "targets": {
            "requested_ids": source_ids,
            "effective_ids": target_ids,
            "requested_count": len(source_ids),
            "effective_count": len(target_ids),
            "decision_counts": decisions,
            "blocker_counts": blockers,
            "items": items,
        },
        "dry_run_windows": windows,
        "artifacts": {
            "adr_path": str(ADR_PATH.relative_to(ROOT)),
        },
        "next_steps": [
            "Keep the 30-KOL batch executor absent.",
            "Run exactly one paid KOL only after explicit operator approval and passing budget gates.",
            "Review the single live result for cost, latency, fields, and evidence quality.",
            "Only then decide whether to create a cron:p4_gemini_batch_30 budget scope and batch executor.",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    policy = report["policy"]
    targets = report["targets"]
    lines = [
        "# V-KPI P4.58 Gemini Batch-30 Dry Run",
        "",
        "Read-only planning report. It does not call Gemini, LLMs, Apify, sync, task queues, or write DB rows.",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Passed: `{str(report['passed']).lower()}`",
        f"- Readiness: `{report['readiness']}`",
        f"- Candidate source: `{report['candidate_source']}`",
        f"- Provider calls: `{str(report['provider_calls']).lower()}`",
        f"- LLM calls: `{str(report['llm_calls']).lower()}`",
        f"- Write DB: `{str(report['write_db']).lower()}`",
        f"- Batch execution allowed: `{str(report['batch_execution_allowed']).lower()}`",
        f"- Effective target count: `{targets['effective_count']}`",
        f"- Effective window size: `{policy['effective_window_size']}`",
        f"- Effective concurrency: `{policy['effective_concurrency']}`",
        "",
        "## Decision Counts",
        "",
        f"`{targets['decision_counts']}`",
        "",
        "## Blocker Counts",
        "",
        f"`{targets['blocker_counts']}`",
        "",
        "## Controls",
        "",
        f"- max targets: `{policy['max_target_size']}`",
        f"- max window size: `{policy['max_window_size']}`",
        f"- hard max concurrency: `{policy['hard_max_concurrency']}`",
        f"- min delay seconds: `{policy['minimum_delay_seconds_between_starts']}`",
        f"- max retries per KOL: `{policy['retry_policy']['max_retries_per_kol']}`",
        f"- stop errors >= `{policy['stop_conditions']['errors_gte']}`",
        f"- stop error rate >= `{policy['stop_conditions']['error_rate_gte']}`",
        "",
        "## Dry-Run Windows",
        "",
    ]
    for window in report["dry_run_windows"]:
        lines.append(
            f"- window `{window['window_index']}`: count `{window['target_count']}`, execution `{str(window['execution_enabled']).lower()}`, ids `{window['kol_pool_ids']}`"
        )
    lines.extend(["", "## Checks", ""])
    for key, value in report["checks"].items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    lines.extend(["", "## Next Steps", ""])
    lines.extend(f"- {item}" for item in report["next_steps"])
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only P4.58 Gemini 30-KOL batch dry-run report.")
    parser.add_argument("--query", default="viltrox")
    parser.add_argument("--kol-pool-ids", default="", help="Comma-separated KOL pool ids. If omitted, natural search provides targets.")
    parser.add_argument("--candidate-limit", type=int, default=24)
    parser.add_argument("--target-size", type=int, default=DEFAULT_TARGET_KOLS)
    parser.add_argument("--window-size", type=int, default=DEFAULT_WINDOW_SIZE)
    parser.add_argument("--requested-concurrency", type=int, default=INITIAL_CONCURRENCY)
    parser.add_argument("--json-out", default="")
    parser.add_argument("--md-out", default="")
    parser.add_argument("--json", action="store_true")
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
            query=str(args.query or "viltrox"),
            kol_pool_ids=_ids_from_text(args.kol_pool_ids),
            candidate_limit=max(1, min(100, int(args.candidate_limit or 24))),
            target_size=max(1, int(args.target_size or DEFAULT_TARGET_KOLS)),
            window_size=max(1, int(args.window_size or DEFAULT_WINDOW_SIZE)),
            requested_concurrency=max(1, int(args.requested_concurrency or INITIAL_CONCURRENCY)),
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
