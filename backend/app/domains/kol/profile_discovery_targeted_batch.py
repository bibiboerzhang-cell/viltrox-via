"""Exact, cost-gated QueryCell rounds for the queued discovery pipeline."""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logging import get_logger
from app.domains.kol import (
    profile_discovery_evidence,
    profile_discovery_rounds,
    recall_favorite_exclusion,
    targeted_query_execution,
)
from app.domains.kol.discovery_filters import _int, _text

logger = get_logger(__name__)
Discover = Callable[..., Awaitable[dict[str, Any]]]


def _forecast_rows(
    plan: dict[str, Any],
    *,
    plan_legs: list[str],
) -> list[dict[str, Any]]:
    """Build the exact per-cell bill before any provider coroutine is created."""

    rows: list[dict[str, Any]] = []
    for run in plan.get("runs") or []:
        if not isinstance(run, dict):
            continue
        cell_limits = (
            run.get("per_platform_limits")
            if isinstance(run.get("per_platform_limits"), dict)
            else {}
        )
        forecast = profile_discovery_rounds.round_cost_forecast(
            run.get("platforms") or plan_legs,
            round_no=_int(plan.get("round_no"), 1),
            per_platform_limit=max(
                cell_limits.values(),
                default=_int(run.get("raw_limit"), 12),
            ),
            per_platform_limits=cell_limits,
            # exact_query=True performs one YouTube search query for this cell.
            youtube_query_variants=1,
        )
        forecast.update({
            "query_cell_id": _text(run.get("query_cell_id")),
            "query_cell_query": _text(run.get("query")),
            "query_variant": _text(run.get("query_variant")),
            "fallback_index": run.get("fallback_index"),
        })
        rows.append(forecast)
    return rows


def _aggregate_forecast(
    rows: list[dict[str, Any]],
    *,
    round_no: int,
) -> dict[str, Any]:
    platforms: list[str] = []
    for row in rows:
        for platform in row.get("platforms") or []:
            normalized = _text(platform).lower()
            if normalized and normalized not in platforms:
                platforms.append(normalized)
    return {
        "round_no": max(1, int(round_no or 1)),
        "query_cell_count": len(rows),
        "platforms": platforms,
        "apify_runs": sum(_int(row.get("apify_runs"), 0) for row in rows),
        "youtube_search_calls": sum(
            _int(row.get("youtube_search_calls"), 0) for row in rows
        ),
        "youtube_combined_quota_units": sum(
            _int(row.get("youtube_combined_quota_units"), 0) for row in rows
        ),
        "youtube_api_calls": sum(_int(row.get("youtube_api_calls"), 0) for row in rows),
        # Deprecated compatibility alias.  Since 2026-06-01 search.list uses
        # its own Search Queries bucket, so this may only mirror combined
        # quota units; it must never add search calls back into one fake sum.
        "youtube_quota_units": sum(_int(row.get("youtube_quota_units"), 0) for row in rows),
        "youtube_quota_units_deprecated": True,
        "estimated_usd": round(sum(float(row.get("estimated_usd") or 0.0) for row in rows), 4),
    }


def round_plan_actual_youtube_kwargs(rounds: Any) -> dict[str, int]:
    """Map observed rounds to the three explicit round-plan actual fields."""

    rows = [row for row in (rounds or []) if isinstance(row, dict)]
    return {
        "actual_search_calls": sum(
            _int(row.get("youtube_search_calls_actual"), 0) for row in rows
        ),
        "actual_combined_quota_units": sum(
            _int(row.get("youtube_combined_quota_units_actual"), 0) for row in rows
        ),
        "actual_youtube_api_calls": sum(
            _int(row.get("youtube_api_calls_actual"), 0) for row in rows
        ),
    }


def term_evidence_youtube_forecast_kwargs(forecasts: Any) -> dict[str, int | None]:
    """Map forecasts to term-evidence fields without reviving the old fake total."""

    rows = [row for row in (forecasts or []) if isinstance(row, dict)]

    def total(field: str) -> int | None:
        return sum(_int(row.get(field), 0) for row in rows) if rows else None

    return {
        "youtube_search_calls_forecast": total("youtube_search_calls"),
        "youtube_combined_quota_units_forecast": total("youtube_combined_quota_units"),
        "youtube_api_calls_forecast": total("youtube_api_calls"),
    }


def preflight_targeted_round(
    plan: dict[str, Any],
    *,
    plan_legs: list[str],
    state: dict[str, Any],
) -> dict[str, Any]:
    """Forecast and authorize one complete QueryCell round before provider IO.

    The authorization is aggregate, not per cell: two concurrently scheduled
    Instagram cells cannot each pass against the same remaining daily budget.
    Forecast reservations are cumulative across this worker run, which fails
    safely if ledger writes lag behind provider completion.
    """

    round_no = max(1, _int(plan.get("round_no"), 1))
    preflights = state.setdefault("targeted_preflights", {})
    cached = preflights.get(str(round_no))
    if isinstance(cached, dict):
        return cached
    stopped_by = _text(state.get("targeted_gate_stopped_by"))
    if stopped_by:
        verdict = {"allowed": False, "reason": stopped_by, "forecast": {}}
        preflights[str(round_no)] = verdict
        return verdict

    rows = _forecast_rows(plan, plan_legs=plan_legs)
    aggregate = _aggregate_forecast(rows, round_no=round_no)
    forecasts = state.setdefault("round_forecasts", [])
    forecasts.extend(rows)
    started = state.setdefault("targeted_started_monotonic", time.monotonic())
    deadline = profile_discovery_rounds.online_deadline_seconds()
    seconds_left = round(max(0.0, deadline - (time.monotonic() - float(started))), 3)
    cap = profile_discovery_rounds.daily_budget_usd()
    reason = ""
    spend_checked = False
    spent = None
    estimated = float(aggregate.get("estimated_usd") or 0.0)
    reserved = float(state.get("targeted_authorized_estimated_usd") or 0.0)

    if not rows:
        reason = "no_targeted_fallback_remaining"
    elif seconds_left < profile_discovery_rounds.MIN_ROUND_BUDGET_SECONDS:
        reason = "online_deadline_exhausted"
    elif estimated > 0:
        spend_checked = True
        spend = profile_discovery_rounds.daily_discovery_spend_usd() or {}
        if spend.get("available") is not True:
            reason = "daily_budget_unreadable"
        else:
            spent = float(spend.get("spend_usd") or 0.0)
            if spent + reserved + estimated > cap:
                reason = "daily_budget_exhausted"

    allowed = not reason
    if allowed:
        state["targeted_authorized_estimated_usd"] = round(reserved + estimated, 4)
    else:
        state["targeted_gate_stopped_by"] = reason
    verdict = {
        "allowed": allowed,
        "reason": reason,
        "forecast": aggregate,
        "forecasts": rows,
        "seconds_left": seconds_left,
        "daily_budget_usd": round(cap, 4),
        "spend_checked": spend_checked,
        **({"today_spend_usd": round(spent, 4)} if spent is not None else {}),
    }
    for row in rows:
        row["gate_allowed"] = allowed
        row["gate_reason"] = reason
        logger.info(
            "targeted_discovery_forecast %s cell=%s allowed=%s reason=%s",
            profile_discovery_rounds.forecast_line(row),
            row["query_cell_id"],
            allowed,
            reason or "-",
        )
    preflights[str(round_no)] = verdict
    state.setdefault("targeted_gate_verdicts", []).append(verdict)
    return verdict


def build_targeted_round_gate(
    *,
    query_cells: list[dict[str, Any]],
    discovery_kwargs: dict[str, Any],
    plan_legs: list[str],
    state: dict[str, Any],
) -> Callable[[int], dict[str, Any]]:
    """Return the collect-loop gate for fallback rounds."""

    def gate(round_no: int) -> dict[str, Any]:
        stopped_by = _text(state.get("targeted_gate_stopped_by"))
        if stopped_by:
            return {
                "allowed": False,
                "reason": stopped_by,
                "forecast": {},
            }
        plan = targeted_query_execution.plan_query_cell_round(
            query_cells=query_cells,
            base_kwargs=discovery_kwargs,
            round_no=round_no,
        )
        return preflight_targeted_round(plan, plan_legs=plan_legs, state=state)

    return gate


def build_pipeline_round_gate(
    *,
    query_cells: list[dict[str, Any]],
    discovery_kwargs: dict[str, Any],
    plan_legs: list[str],
    state: dict[str, Any],
    per_platform_limit: int,
    per_platform_limits: Any,
) -> Callable[[int], dict[str, Any]]:
    """Select the targeted gate or preserve the legacy pagination gate."""

    if query_cells:
        return build_targeted_round_gate(
            query_cells=query_cells,
            discovery_kwargs=discovery_kwargs,
            plan_legs=plan_legs,
            state=state,
        )
    return profile_discovery_rounds.build_round_gate(
        legs_for_round=lambda round_no: profile_discovery_rounds.platforms_for_round(
            round_no,
            state.get("round_legs") or [],
            state.get("round_cursor") or {},
        ),
        per_platform_limit=per_platform_limit,
        per_platform_limits=per_platform_limits,
        progress_reader=lambda: _int((state.get("round_yield") or {}).get("last")),
    )


def finalize_online_result(
    result: dict[str, Any],
    *,
    query_cells: list[dict[str, Any]],
    query_cells_omitted: int,
    search_brief: Any,
    objective: Any,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Attach honest targeted continuation and complete gate diagnostics."""

    if query_cells:
        collect_gate = result.get("round_gate") if isinstance(result.get("round_gate"), dict) else {}
        result["round_gate"] = {
            "stopped_by": (
                _text(state.get("targeted_gate_stopped_by"))
                or _text(collect_gate.get("stopped_by"))
                or None
            ),
            "verdicts": list(state.get("targeted_gate_verdicts") or []),
        }
    exhausted = bool(result.get("exhausted"))
    brief = search_brief if isinstance(search_brief, dict) else {}
    result["targeted_search"] = {
        "search_spec_version": _text(brief.get("search_spec_version")),
        "objective": _text(objective),
        "first_round_strategy": "independent_query_cells" if query_cells else "legacy_compat_query",
        "query_cells_requested": len(query_cells) + max(0, int(query_cells_omitted or 0)),
        "query_cells_executed": len(query_cells),
        "query_cells_omitted": max(0, int(query_cells_omitted or 0)),
        "fallback_queries_used": bool(state.get("fallback_queries_used")),
        "provider_rounds": (
            len(state.get("targeted_rounds_executed") or [])
            if query_cells
            else _int(result.get("provider_rounds"))
        ),
        "collector_rounds": _int(result.get("provider_rounds")),
        "has_more": not exhausted,
        "next_cursor": dict(state.get("round_cursor") or {}) if not exhausted else {},
        "shortfall": _int(result.get("shortfall")),
        "shortfall_reasons": dict(result.get("shortfall_reasons") or {}),
        "claim_status": "descriptive_only",
    }
    return result


def exhaustion_reason(query_cells: list[dict[str, Any]]) -> str:
    return (
        "targeted_query_fallbacks_exhausted"
        if query_cells
        else "bounded_provider_batch_exhausted"
    )


async def fetch_targeted_round(
    *,
    round_no: int,
    query_cells: list[dict[str, Any]],
    discovery_kwargs: dict[str, Any],
    plan_legs: list[str],
    state: dict[str, Any],
    favorite_identity_keys: set[str],
    discover: Discover,
) -> dict[str, Any]:
    """Run one deterministic primary/fallback round and update diagnostics."""

    batch = await targeted_query_execution.execute_query_cell_round(
        query_cells=query_cells,
        base_kwargs=discovery_kwargs,
        discover=discover,
        round_no=round_no,
        before_provider_calls=lambda plan: preflight_targeted_round(
            plan,
            plan_legs=plan_legs,
            state=state,
        ),
    )
    if batch.get("status") == "blocked":
        gate = batch.get("provider_gate") if isinstance(batch.get("provider_gate"), dict) else {}
        reason = _text(gate.get("reason")) or "targeted_round_gate_denied"
        state["targeted_gate_stopped_by"] = reason
        if gate and gate not in state.setdefault("targeted_gate_verdicts", []):
            state["targeted_gate_verdicts"].append(gate)
    if batch.get("targeted_round_complete") is True:
        executed_rounds = state.setdefault("targeted_rounds_executed", [])
        if round_no not in executed_rounds:
            executed_rounds.append(round_no)
        state.setdefault("term_rounds", []).append(profile_discovery_evidence.observe_round(
            round_no=round_no,
            platform_results=batch.get("platform_results"),
            candidates=batch.get("new_creators"),
        ))
        state.setdefault("observed_candidates", []).extend(
            row for row in (batch.get("new_creators") or []) if isinstance(row, dict)
        )
        kept, favorite_block = recall_favorite_exclusion.exclude_favorited_online_candidates(
            batch.get("new_creators") or [],
            identity_keys=favorite_identity_keys,
        )
        batch["new_creators"] = kept
        state.setdefault("favorite_blocks", []).append(favorite_block)
        if batch.get("fallback_queries_used") is True:
            state["fallback_queries_used"] = True
    if not state.setdefault("round_legs", []):
        state["round_legs"].extend(
            _text(item) for item in (batch.get("platforms") or []) if _text(item)
        )
    cursor = state.setdefault("round_cursor", {})
    cursor.clear()
    cursor.update(batch.get("next_cursor") or {})
    state.setdefault("round_yield", {})["last"] = len(batch.get("new_creators") or [])
    return batch


# Compatibility name retained for callers that still import it.  It now
# executes the requested deterministic round instead of returning an empty
# result after round one.
fetch_targeted_first_round = fetch_targeted_round


__all__ = [
    "build_pipeline_round_gate",
    "build_targeted_round_gate",
    "exhaustion_reason",
    "fetch_targeted_first_round",
    "fetch_targeted_round",
    "finalize_online_result",
    "preflight_targeted_round",
    "round_plan_actual_youtube_kwargs",
    "term_evidence_youtube_forecast_kwargs",
]
