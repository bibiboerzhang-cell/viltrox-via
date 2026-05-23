#!/usr/bin/env python3
"""Plan or explicitly execute qualified KOL Apify batch refreshes.

Default mode is safe: it builds the qualified batch plan and runs the executor
with provider calls blocked. Real Apify calls require both ``--execute`` and
``--allow-provider-calls``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DEFAULT_OPS_DIR = ROOT / "runtime" / "ops"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime  # noqa: E402
from app.services.vkpi import apify_batch_refresh  # noqa: E402


def _platforms(value: str) -> set[str]:
    return {apify_batch_refresh.normalize_platform(item) for item in str(value or "").split(",") if item.strip()}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan or explicitly execute V-KPI qualified Apify batch refresh")
    parser.add_argument("--limit", type=int, default=50, help="Max qualified KOL rows to plan")
    parser.add_argument("--offset", type=int, default=0, help="Qualified selector offset")
    parser.add_argument("--platforms", default="", help="Comma-separated platform filter")
    parser.add_argument("--tiers", default="hot", help="Comma-separated refresh tiers")
    parser.add_argument("--stale-before", default="", help="Only plan rows refreshed before this UTC timestamp")
    parser.add_argument("--stale-days", type=int, default=0, help="Compute stale-before as now minus N days")
    parser.add_argument("--max-posts", type=int, default=1, help="Latest post sample per KOL")
    parser.add_argument("--max-concurrent-runs", type=int, default=2, help="Outer Apify run concurrency cap")
    parser.add_argument("--chunk-sizes", default="", help="Optional platform chunk overrides, e.g. instagram=25,tiktok=10")
    parser.add_argument("--timeout-seconds", type=int, default=apify_batch_refresh.DEFAULT_RUN_TIMEOUT_SECONDS, help="Apify actor timeout if real execution is explicitly enabled")
    parser.add_argument("--execute", action="store_true", help="Run the executor. Provider calls still require --allow-provider-calls.")
    parser.add_argument("--allow-provider-calls", action="store_true", help="Actually call Apify. Use only after backup-first operator approval.")
    parser.add_argument("--max-live-targets", type=int, default=25, help="Hard cap for live provider execution targets. Default 25, max 100.")
    parser.add_argument("--live-window-index", type=int, default=0, help="Restrict execution to one safe live window from the full plan, 1-based")
    parser.add_argument("--compact", action="store_true", help="Omit full batch target lists from output")
    parser.add_argument("--json-out", default="", help="Optional JSON artifact path. Defaults to runtime/ops.")
    parser.add_argument("--no-artifact", action="store_true", help="Do not write an operator JSON artifact")
    return parser.parse_args(argv)


def _compact_plan(plan: dict[str, Any]) -> dict[str, Any]:
    result = dict(plan)
    result["batches"] = [
        {
            "batch_key": batch.get("batch_key"),
            "platform": batch.get("platform"),
            "target_count": batch.get("target_count"),
            "actor_id": batch.get("actor_id"),
            "kol_pool_ids": batch.get("kol_pool_ids"),
        }
        for batch in (plan.get("batches") or [])
        if isinstance(batch, dict)
    ]
    return result


def _safe_int(value: Any) -> int:
    try:
        return int(float(str(value or "").strip()))
    except (TypeError, ValueError):
        return 0


def provider_config_summary(plan: dict[str, Any]) -> dict[str, Any]:
    platforms = set()
    if isinstance(plan.get("platforms"), dict):
        platforms.update(str(platform) for platform in plan["platforms"].keys())
    for batch in (plan.get("batches") or []):
        if isinstance(batch, dict) and batch.get("platform"):
            platforms.add(str(batch["platform"]))

    token_configured = bool(os.environ.get("APIFY_TOKEN", "").strip())
    platform_rows: dict[str, dict[str, Any]] = {}
    missing_platforms: list[str] = []
    for platform in sorted(apify_batch_refresh.normalize_platform(item) for item in platforms if item):
        env_key = apify_batch_refresh.ACTOR_ENV_KEYS.get(platform, "")
        actor_env_configured = bool(env_key and os.environ.get(env_key, "").strip())
        actor_id = apify_batch_refresh.actor_id_for_platform(platform)
        configured = bool(token_configured and actor_id)
        if not configured:
            missing_platforms.append(platform)
        platform_rows[platform] = {
            "actor_id": actor_id,
            "actor_env_key": env_key,
            "actor_env_configured": actor_env_configured,
            "actor_source": "env" if actor_env_configured else "default",
            "configured": configured,
        }
    return {
        "token_configured": token_configured,
        "platform_count": len(platform_rows),
        "platforms": platform_rows,
        "missing_platforms": missing_platforms,
        "configured": bool(token_configured and not missing_platforms),
    }


def execution_preflight(
    args: argparse.Namespace,
    plan: dict[str, Any],
    provider_config: dict[str, Any],
    window_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target_count = _safe_int(plan.get("total_targets"))
    batch_count = _safe_int(plan.get("batch_count"))
    skipped = [item for item in (plan.get("skipped") or []) if isinstance(item, dict)]
    live_target_cap = max(1, min(100, _safe_int(args.max_live_targets) or 25))
    selector_ready = bool(plan.get("selector_ready"))
    provider_configured = bool(provider_config.get("configured"))
    windows = window_summary if isinstance(window_summary, dict) else {}
    window_count = _safe_int(windows.get("window_count"))
    windowed_execution_available = bool(window_count and not windows.get("requires_replan_for_full_live"))
    checks = {
        "selector_ready": selector_ready,
        "has_targets": target_count > 0,
        "within_live_target_cap": target_count <= live_target_cap,
        "provider_configured": provider_configured,
        "no_skipped_targets": not skipped,
        "windowed_execution_available": windowed_execution_available,
    }
    warnings: list[str] = []
    if skipped:
        warnings.append("plan_contains_skipped_targets")

    if not selector_ready:
        status = "selector_not_ready"
    elif target_count <= 0:
        status = "no_targets_to_execute"
    elif target_count > live_target_cap:
        if provider_configured and not skipped and windowed_execution_available:
            status = "requires_windowed_execution"
            warnings.append("requires_windowed_execution")
        else:
            status = "live_target_cap_exceeded"
    elif not provider_configured:
        status = "provider_not_configured"
    elif skipped:
        status = "review_skipped_targets"
    else:
        status = "ready"

    return {
        "status": status,
        "can_execute_if_authorized": status == "ready",
        "can_execute_by_windows": status in {"ready", "requires_windowed_execution"},
        "target_count": target_count,
        "batch_count": batch_count,
        "live_target_cap": live_target_cap,
        "checks": checks,
        "warnings": warnings,
    }


def _batch_target_count(batch: dict[str, Any]) -> int:
    explicit = _safe_int(batch.get("target_count"))
    if explicit:
        return explicit
    targets = batch.get("targets") if isinstance(batch.get("targets"), list) else []
    if targets:
        return len(targets)
    kol_pool_ids = batch.get("kol_pool_ids") if isinstance(batch.get("kol_pool_ids"), list) else []
    return len(kol_pool_ids)


def safe_live_windows(args: argparse.Namespace, plan: dict[str, Any]) -> dict[str, Any]:
    live_target_cap = max(1, min(100, _safe_int(args.max_live_targets) or 25))
    windows: list[dict[str, Any]] = []
    oversized_batches: list[dict[str, Any]] = []
    recommended_chunk_overrides: dict[str, int] = {}
    current_batches: list[dict[str, Any]] = []
    current_platforms: dict[str, int] = {}
    current_count = 0

    def flush_window() -> None:
        nonlocal current_batches, current_platforms, current_count
        if not current_batches:
            return
        windows.append(
            {
                "window_index": len(windows) + 1,
                "target_count": current_count,
                "batch_count": len(current_batches),
                "platforms": dict(current_platforms),
                "batches": current_batches,
            }
        )
        current_batches = []
        current_platforms = {}
        current_count = 0

    for raw_batch in plan.get("batches") or []:
        if not isinstance(raw_batch, dict):
            continue
        target_count = _batch_target_count(raw_batch)
        platform = apify_batch_refresh.normalize_platform(raw_batch.get("platform"))
        batch = {
            "batch_key": raw_batch.get("batch_key"),
            "platform": platform,
            "target_count": target_count,
            "kol_pool_ids": raw_batch.get("kol_pool_ids") if isinstance(raw_batch.get("kol_pool_ids"), list) else [],
        }
        if target_count > live_target_cap:
            oversized_batches.append(
                {
                    **batch,
                    "recommended_chunk_size": live_target_cap,
                }
            )
            if platform:
                recommended_chunk_overrides[platform] = live_target_cap
            continue
        if current_batches and current_count + target_count > live_target_cap:
            flush_window()
        current_batches.append(batch)
        current_count += target_count
        if platform:
            current_platforms[platform] = current_platforms.get(platform, 0) + target_count
    flush_window()

    chunk_arg = ",".join(f"{platform}={size}" for platform, size in sorted(recommended_chunk_overrides.items()))
    return {
        "live_target_cap": live_target_cap,
        "window_count": len(windows),
        "windows": windows,
        "oversized_batch_count": len(oversized_batches),
        "oversized_batches": oversized_batches,
        "requires_replan_for_full_live": bool(oversized_batches),
        "recommended_chunk_overrides": recommended_chunk_overrides,
        "recommended_chunk_sizes_arg": chunk_arg,
    }


def _platform_counts_for_batches(batches: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for batch in batches:
        platform = apify_batch_refresh.normalize_platform(batch.get("platform"))
        if platform:
            counts[platform] = counts.get(platform, 0) + _batch_target_count(batch)
    return counts


def _empty_window_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        **plan,
        "selector_ready": False,
        "source_total": 0,
        "source_by_platform": {},
        "tier_distribution": {},
        "total_targets": 0,
        "batch_count": 0,
        "batches": [],
        "skipped": [],
        "platforms": {},
    }


def select_live_window(
    args: argparse.Namespace,
    plan: dict[str, Any],
    windows: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    requested_index = _safe_int(getattr(args, "live_window_index", 0))
    if requested_index <= 0:
        return plan, {
            "requested": False,
            "selected": False,
            "reason": "not_requested",
            "requested_index": 0,
            "full_target_count": _safe_int(plan.get("total_targets")),
            "full_batch_count": _safe_int(plan.get("batch_count")),
            "full_window_count": _safe_int(windows.get("window_count")),
        }

    all_windows = [item for item in (windows.get("windows") or []) if isinstance(item, dict)]
    selected_window = next((item for item in all_windows if _safe_int(item.get("window_index")) == requested_index), None)
    if not selected_window:
        return _empty_window_plan(plan), {
            "requested": True,
            "selected": False,
            "reason": "window_not_found",
            "requested_index": requested_index,
            "full_target_count": _safe_int(plan.get("total_targets")),
            "full_batch_count": _safe_int(plan.get("batch_count")),
            "full_window_count": _safe_int(windows.get("window_count")),
        }

    selected_keys = {
        str(batch.get("batch_key") or "")
        for batch in (selected_window.get("batches") or [])
        if isinstance(batch, dict) and batch.get("batch_key")
    }
    selected_batches = [
        batch
        for batch in (plan.get("batches") or [])
        if isinstance(batch, dict) and str(batch.get("batch_key") or "") in selected_keys
    ]
    target_count = sum(_batch_target_count(batch) for batch in selected_batches)
    selected_plan = {
        **plan,
        "source_total": target_count,
        "source_by_platform": _platform_counts_for_batches(selected_batches),
        "total_targets": target_count,
        "batch_count": len(selected_batches),
        "batches": selected_batches,
        "skipped": [],
        "platforms": _platform_counts_for_batches(selected_batches),
    }
    return selected_plan, {
        "requested": True,
        "selected": True,
        "reason": "selected",
        "requested_index": requested_index,
        "selected_window_index": requested_index,
        "selected_target_count": target_count,
        "selected_batch_count": len(selected_batches),
        "selected_batch_keys": [str(batch.get("batch_key") or "") for batch in selected_batches],
        "selected_platforms": _platform_counts_for_batches(selected_batches),
        "full_target_count": _safe_int(plan.get("total_targets")),
        "full_batch_count": _safe_int(plan.get("batch_count")),
        "full_window_count": _safe_int(windows.get("window_count")),
    }


def _cli_command(args: argparse.Namespace, *, live_window_index: int = 0, execute: bool = False) -> str:
    parts = [
        "PYTHONPATH=backend",
        ".venv/bin/python",
        "scripts/vkpi_apify_batch_refresh.py",
        "--limit",
        str(max(1, min(1200, _safe_int(args.limit) or 50))),
        "--offset",
        str(max(0, min(5000, _safe_int(args.offset)))),
        "--tiers",
        str(args.tiers or "hot"),
        "--max-posts",
        str(max(1, min(3, _safe_int(args.max_posts) or 1))),
        "--max-concurrent-runs",
        str(max(1, min(3, _safe_int(args.max_concurrent_runs) or 2))),
        "--max-live-targets",
        str(max(1, min(100, _safe_int(args.max_live_targets) or 25))),
        "--compact",
    ]
    if str(args.platforms or "").strip():
        parts.extend(["--platforms", str(args.platforms)])
    if str(args.stale_before or "").strip():
        parts.extend(["--stale-before", str(args.stale_before)])
    elif _safe_int(args.stale_days):
        parts.extend(["--stale-days", str(_safe_int(args.stale_days))])
    if str(args.chunk_sizes or "").strip():
        parts.extend(["--chunk-sizes", str(args.chunk_sizes)])
    if live_window_index > 0:
        parts.extend(["--live-window-index", str(live_window_index)])
    if execute:
        parts.extend(["--execute", "--allow-provider-calls"])
    return shlex.join(parts)


def window_execution_runbook(
    args: argparse.Namespace,
    full_windows: dict[str, Any],
    window_selection: dict[str, Any],
) -> dict[str, Any]:
    window_count = _safe_int(full_windows.get("window_count"))
    requires_replan = bool(full_windows.get("requires_replan_for_full_live"))
    windows = [item for item in (full_windows.get("windows") or []) if isinstance(item, dict)]
    execute_commands = [
        {
            "window_index": _safe_int(window.get("window_index")),
            "target_count": _safe_int(window.get("target_count")),
            "batch_count": _safe_int(window.get("batch_count")),
            "platforms": window.get("platforms") if isinstance(window.get("platforms"), dict) else {},
            "command": _cli_command(args, live_window_index=_safe_int(window.get("window_index")), execute=True),
        }
        for window in windows
    ]
    if requires_replan:
        reason = "replan_required"
    elif window_count <= 0:
        reason = "no_safe_windows"
    else:
        reason = "provider_authorization_required"
    return {
        "available": bool(window_count and not requires_replan),
        "reason": reason,
        "requires_explicit_authorization": True,
        "preflight_command": _cli_command(args, execute=False),
        "execute_all_at_once_allowed": window_count <= 1 and not requires_replan,
        "window_count": window_count,
        "live_target_cap": _safe_int(full_windows.get("live_target_cap")),
        "selected_window_index": _safe_int(window_selection.get("selected_window_index") or window_selection.get("requested_index")),
        "execute_commands": execute_commands,
    }


def operator_summary(result: dict[str, Any]) -> dict[str, Any]:
    plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
    execution = result.get("execution") if isinstance(result.get("execution"), dict) else {}
    execution_summary = execution.get("summary") if isinstance(execution.get("summary"), dict) else {}
    provider_gate = result.get("provider_gate") if isinstance(result.get("provider_gate"), dict) else {}
    provider_config = result.get("provider_config") if isinstance(result.get("provider_config"), dict) else {}
    preflight = result.get("execution_preflight") if isinstance(result.get("execution_preflight"), dict) else {}
    windows = result.get("safe_live_windows") if isinstance(result.get("safe_live_windows"), dict) else {}
    skipped = [item for item in (plan.get("skipped") or []) if isinstance(item, dict)]
    skipped_reasons: dict[str, int] = {}
    for item in skipped:
        reason = str(item.get("reason") or "unknown")
        skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1

    provider_calls_allowed = bool(result.get("provider_calls_allowed"))
    gate_reason = str(provider_gate.get("reason") or "")
    failed_batches = _safe_int(execution_summary.get("failed_batches") or execution.get("failed_batches"))
    retry_count = _safe_int(execution_summary.get("retry_count"))
    if gate_reason == "live_target_cap_exceeded":
        readiness = "live_target_cap_exceeded"
    elif gate_reason == "no_targets_to_execute":
        readiness = "no_targets_to_execute"
    elif gate_reason == "provider_not_configured":
        readiness = "provider_not_configured"
    elif not provider_calls_allowed:
        readiness = "blocked_provider_calls"
    elif failed_batches or retry_count:
        readiness = "review_required"
    else:
        readiness = "ready"

    return {
        "readiness": readiness,
        "mode": result.get("mode"),
        "provider_calls_requested": bool(provider_gate.get("requested")),
        "provider_calls_allowed": provider_calls_allowed,
        "provider_gate_reason": gate_reason,
        "provider_configured": bool(provider_config.get("configured")),
        "missing_provider_platforms": provider_config.get("missing_platforms") if isinstance(provider_config.get("missing_platforms"), list) else [],
        "execution_preflight_status": str(preflight.get("status") or ""),
        "can_execute_if_authorized": bool(preflight.get("can_execute_if_authorized")),
        "can_execute_by_windows": bool(preflight.get("can_execute_by_windows")),
        "safe_window_count": _safe_int(windows.get("window_count")),
        "oversized_batch_count": _safe_int(windows.get("oversized_batch_count")),
        "requires_replan_for_full_live": bool(windows.get("requires_replan_for_full_live")),
        "live_target_cap": _safe_int(provider_gate.get("live_target_cap")),
        "selector_ready": bool(plan.get("selector_ready")),
        "source_total": _safe_int(plan.get("source_total")),
        "target_count": _safe_int(plan.get("total_targets")),
        "batch_count": _safe_int(plan.get("batch_count")),
        "platforms": plan.get("platforms") if isinstance(plan.get("platforms"), dict) else {},
        "skipped_count": len(skipped),
        "skipped_reasons": skipped_reasons,
        "executed": bool(execution.get("executed")),
        "execution_reason": str(execution.get("reason") or ""),
        "retry_count": retry_count,
        "failed_batches": failed_batches,
    }


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    return apify_batch_refresh.qualified_apify_batch_plan(
        limit=max(1, min(1200, int(args.limit or 50))),
        offset=max(0, min(5000, int(args.offset or 0))),
        stale_before=str(args.stale_before or ""),
        stale_days=max(0, int(args.stale_days or 0)),
        platforms=_platforms(args.platforms),
        tiers=_platforms(args.tiers),
        max_posts=max(1, min(3, int(args.max_posts or 1))),
        max_concurrent=max(1, min(3, int(args.max_concurrent_runs or 2))),
        chunk_overrides=apify_batch_refresh.parse_chunk_overrides(args.chunk_sizes),
    )


def provider_gate(args: argparse.Namespace, plan: dict[str, Any], provider_config: dict[str, Any] | None = None) -> dict[str, Any]:
    requested = bool(args.execute and args.allow_provider_calls)
    target_count = _safe_int(plan.get("total_targets"))
    live_target_cap = max(1, min(100, _safe_int(args.max_live_targets) or 25))
    if not requested:
        reason = "provider_calls_not_requested"
        allowed = False
    elif target_count <= 0:
        reason = "no_targets_to_execute"
        allowed = False
    elif target_count > live_target_cap:
        reason = "live_target_cap_exceeded"
        allowed = False
    elif not bool((provider_config or {}).get("configured")):
        reason = "provider_not_configured"
        allowed = False
    else:
        reason = "allowed"
        allowed = True
    return {
        "requested": requested,
        "allowed": allowed,
        "reason": reason,
        "target_count": target_count,
        "live_target_cap": live_target_cap,
    }


async def run_from_args(args: argparse.Namespace) -> dict[str, Any]:
    full_plan = build_plan(args)
    full_windows = safe_live_windows(args, full_plan)
    plan, window_selection = select_live_window(args, full_plan, full_windows)
    provider_config = provider_config_summary(plan)
    windows = safe_live_windows(args, plan)
    preflight = execution_preflight(args, plan, provider_config, windows)
    gate = provider_gate(args, plan, provider_config)
    execution = await apify_batch_refresh.execute_apify_batch_plan(
        plan,
        allow_provider_calls=bool(gate["allowed"]),
        timeout_secs=max(30, min(1800, int(args.timeout_seconds or apify_batch_refresh.DEFAULT_RUN_TIMEOUT_SECONDS))),
    )
    if gate["requested"] and not gate["allowed"] and isinstance(execution, dict):
        execution = dict(execution)
        execution["reason"] = gate["reason"]
    result = {
        "mode": "execute" if args.execute else "plan_with_blocked_executor",
        "provider_calls_allowed": bool(gate["allowed"]),
        "provider_gate": gate,
        "provider_config": provider_config,
        "execution_preflight": preflight,
        "safe_live_windows": windows,
        "full_safe_live_windows": full_windows if window_selection.get("requested") else windows,
        "window_selection": window_selection,
        "window_execution_runbook": window_execution_runbook(args, full_windows, window_selection),
        "plan": _compact_plan(plan) if args.compact else plan,
        "execution": execution,
    }
    result["operator_summary"] = operator_summary(result)
    return result


def _artifact_path(args: argparse.Namespace) -> Path:
    if args.json_out:
        path = Path(str(args.json_out)).expanduser()
        return path if path.is_absolute() else ROOT / path
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    mode = "execute" if args.execute and args.allow_provider_calls else "plan"
    return DEFAULT_OPS_DIR / f"{now}-apify-batch-refresh-{mode}.json"


def write_artifact(result: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if args.no_artifact:
        return result
    path = _artifact_path(args)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(result)
    payload["artifact"] = {
        "path": str(path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider_calls_allowed": bool(result.get("provider_calls_allowed")),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2) + "\n", encoding="utf-8")
    return payload


async def async_main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = write_artifact(await run_from_args(args), args)
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        execution = result.get("execution") if isinstance(result, dict) else {}
        provider_gate_result = result.get("provider_gate") if isinstance(result, dict) and isinstance(result.get("provider_gate"), dict) else {}
        if provider_gate_result.get("reason") == "live_target_cap_exceeded":
            return 3
        if args.execute and args.allow_provider_calls and isinstance(execution, dict):
            if int(execution.get("failed_batches") or 0):
                return 2
        return 0
    finally:
        await close_db_runtime()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
