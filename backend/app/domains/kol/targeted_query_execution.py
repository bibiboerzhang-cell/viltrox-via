"""Execute targeted KOL query cells without a merged broad-query fallback.

The planner owns the meaning of a QueryCell.  This module owns the execution
boundary: round one sends every primary exactly once; a later shortfall round
may send only the server-planned fallback at that deterministic round index.
Every query keeps its own small raw quota and candidates retain the cell and
query variant that produced them.  No caller-provided cursor can select or
repeat a query, so this path cannot collapse back into a broad merged query.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from app.domains.kol.identity import canonical_creator_aliases
from app.domains.kol.targeted_search_contract import rebuild_locked_term_groups_for_cell


MAX_FIRST_ROUND_CELLS = 8
MAX_CELL_CONCURRENCY = 2
MIN_CELL_RAW_LIMIT = 10
MAX_CELL_RAW_LIMIT = 15

Discover = Callable[..., Awaitable[dict[str, Any]]]
Preflight = Callable[[dict[str, Any]], dict[str, Any]]

MAX_FALLBACK_QUERIES = 3
TARGETED_CURSOR_SCHEMA = "targeted_query_cell_cursor_v1"


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_first_round_cells(value: Any) -> tuple[list[dict[str, Any]], int]:
    """Return bounded, executable round-one cells and the omitted count."""

    raw_cells = value if isinstance(value, list) else []
    cells: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_queries: set[str] = set()
    for raw in raw_cells:
        if not isinstance(raw, dict) or _int(raw.get("round"), 1) != 1:
            continue
        query = _text(raw.get("primary_query"))[:500]
        cell_id = _text(raw.get("query_cell_id"))[:120]
        query_key = query.casefold()
        if not query or not cell_id or cell_id in seen_ids or query_key in seen_queries:
            continue
        seen_ids.add(cell_id)
        seen_queries.add(query_key)
        platforms = []
        for platform in raw.get("platforms") if isinstance(raw.get("platforms"), list) else []:
            normalized = _text(platform).lower()
            if normalized and normalized not in platforms:
                platforms.append(normalized)
        fallback_queries: list[str] = []
        fallback_seen = {query_key}
        for value in (
            raw.get("fallback_queries")
            if isinstance(raw.get("fallback_queries"), list)
            else []
        )[:MAX_FALLBACK_QUERIES]:
            fallback = _text(value)[:500]
            fallback_key = fallback.casefold()
            if fallback and fallback_key not in fallback_seen:
                fallback_seen.add(fallback_key)
                fallback_queries.append(fallback)
        cells.append({
            "query_cell_id": cell_id,
            "objective": _text(raw.get("objective"))[:80],
            "segment": _text(raw.get("segment"))[:120],
            "segment_label": _text(raw.get("segment_label"))[:240],
            "primary_query": query,
            "fallback_queries": fallback_queries,
            "platforms": platforms,
            "raw_limit": max(
                MIN_CELL_RAW_LIMIT,
                min(MAX_CELL_RAW_LIMIT, _int(raw.get("raw_limit"), 12)),
            ),
            "required_evidence_groups": [
                _text(group)[:80]
                for group in (raw.get("required_evidence_groups") or [])[:8]
                if _text(group)
            ],
            "brand_or_model_required": raw.get("brand_or_model_required") is True,
            "brand_or_model_ranking_weight": raw.get("brand_or_model_ranking_weight"),
            **(
                {"locked_term_groups": locked_term_groups}
                if (locked_term_groups := rebuild_locked_term_groups_for_cell(raw))
                else {}
            ),
        })
    omitted = max(0, len(cells) - MAX_FIRST_ROUND_CELLS)
    return cells[:MAX_FIRST_ROUND_CELLS], omitted


def _cell_projection(
    cell: dict[str, Any],
    *,
    executed_query: str | None = None,
    round_no: int = 1,
    query_variant: str = "primary",
    fallback_index: int | None = None,
) -> dict[str, Any]:
    projection = {
        "query_cell_id": cell["query_cell_id"],
        "objective": cell.get("objective"),
        "segment": cell.get("segment"),
        "segment_label": cell.get("segment_label"),
        "primary_query": cell["primary_query"],
        "executed_query": _text(executed_query) or cell["primary_query"],
        "round_no": max(1, int(round_no or 1)),
        "query_variant": query_variant,
        "required_evidence_groups": list(cell.get("required_evidence_groups") or []),
        "brand_or_model_required": cell.get("brand_or_model_required") is True,
        "brand_or_model_ranking_weight": cell.get("brand_or_model_ranking_weight"),
        **(
            {"locked_term_groups": dict(cell["locked_term_groups"])}
            if isinstance(cell.get("locked_term_groups"), dict)
            else {}
        ),
    }
    if fallback_index is not None:
        projection["fallback_index"] = max(0, int(fallback_index))
    return projection


def _matched_cell_list(value: Any) -> list[dict[str, Any]]:
    """Only a JSON-list may carry candidate-to-cell provenance."""

    if not isinstance(value, list):
        return []
    return [dict(raw) for raw in value[:MAX_FIRST_ROUND_CELLS] if isinstance(raw, dict)]


def _annotate_candidate(raw: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    item = dict(raw)
    cell = run["cell"]
    executed_query = run["query"]
    projection = _cell_projection(
        cell,
        executed_query=executed_query,
        round_no=run["round_no"],
        query_variant=run["query_variant"],
        fallback_index=run.get("fallback_index"),
    )
    item.update({
        "query_cell_id": cell["query_cell_id"],
        "query_cell_segment": cell.get("segment"),
        "query_cell_query": executed_query,
        "query_cell_primary_query": cell["primary_query"],
        "query_cell_round": run["round_no"],
        "query_variant": run["query_variant"],
        "discovery_query": _text(item.get("discovery_query")) or executed_query,
        "targeted_search": projection,
        "matched_query_cells": [projection],
    })
    return item


def _merge_candidate(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if merged.get(key) in (None, "", [], {}):
            merged[key] = value
    matches: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in (existing, incoming):
        for raw in _matched_cell_list(source.get("matched_query_cells")):
            cell_id = _text(raw.get("query_cell_id"))
            if cell_id and cell_id not in seen:
                seen.add(cell_id)
                matches.append(dict(raw))
    merged["matched_query_cells"] = matches
    return merged


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    aliases_by_index: list[set[str]] = []
    for candidate in candidates:
        aliases = canonical_creator_aliases(candidate)
        matching_index = next(
            (index for index, existing in enumerate(aliases_by_index) if aliases and aliases.intersection(existing)),
            None,
        )
        if matching_index is None:
            output.append(candidate)
            aliases_by_index.append(set(aliases))
            continue
        output[matching_index] = _merge_candidate(output[matching_index], candidate)
        aliases_by_index[matching_index].update(aliases)
    return output


async def execute_first_round_query_cells(
    *,
    query_cells: Any,
    base_kwargs: dict[str, Any],
    discover: Discover,
) -> dict[str, Any]:
    """Run each authoritative QueryCell exactly once and aggregate evidence."""

    return await execute_query_cell_round(
        query_cells=query_cells,
        base_kwargs=base_kwargs,
        discover=discover,
        round_no=1,
    )


def _platform_execution(cell: dict[str, Any], base_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Resolve the operator-owned platform boundary and bounded per-leg quotas."""

    base_platforms = [
        _text(platform).lower()
        for platform in (
            base_kwargs.get("platforms")
            if isinstance(base_kwargs.get("platforms"), (list, tuple, set))
            else [base_kwargs.get("platforms")]
        )
        if _text(platform)
    ]
    platforms = base_platforms if "platforms" in base_kwargs else list(cell.get("platforms") or [])
    raw_limit = int(cell["raw_limit"])
    platform_count = max(1, len(platforms))
    base_share, remainder = divmod(raw_limit, platform_count)
    platform_limits = {
        platform: max(1, base_share + (1 if index < remainder else 0))
        for index, platform in enumerate(platforms)
    }
    return {
        "platforms": platforms,
        "raw_limit": raw_limit,
        "per_platform_limit": max(platform_limits.values(), default=raw_limit),
        "per_platform_limits": platform_limits,
    }


def plan_query_cell_round(
    *,
    query_cells: Any,
    base_kwargs: dict[str, Any],
    round_no: int,
) -> dict[str, Any]:
    """Build one deterministic provider round without performing any IO.

    Round 1 owns primaries.  Round N>1 owns fallback index N-2.  The mapping is
    derived only from the normalized server plan, never from a replay cursor,
    which proves every fallback is attempted at most once.
    """

    normalized_round = max(1, _int(round_no, 1))
    cells, omitted_count = normalize_first_round_cells(query_cells)
    runs: list[dict[str, Any]] = []
    for cell in cells:
        fallback_index: int | None = None
        if normalized_round == 1:
            query = cell["primary_query"]
            query_variant = "primary"
        else:
            fallback_index = normalized_round - 2
            fallbacks = cell.get("fallback_queries") or []
            if fallback_index >= len(fallbacks):
                continue
            query = fallbacks[fallback_index]
            query_variant = "fallback"
        execution = _platform_execution(cell, base_kwargs)
        runs.append({
            "cell": cell,
            "query_cell_id": cell["query_cell_id"],
            "primary_query": cell["primary_query"],
            "query": query,
            "query_variant": query_variant,
            "fallback_index": fallback_index,
            "round_no": normalized_round,
            **execution,
        })

    next_fallback_index = normalized_round - 1
    remaining = [
        {
            "query_cell_id": cell["query_cell_id"],
            "fallback_index": next_fallback_index,
        }
        for cell in cells
        if next_fallback_index < len(cell.get("fallback_queries") or [])
    ]
    next_cursor = (
        {
            "schema": TARGETED_CURSOR_SCHEMA,
            "completed_round": normalized_round,
            "next_round": normalized_round + 1,
            "remaining": remaining,
        }
        if remaining
        else {}
    )
    return {
        "round_no": normalized_round,
        "runs": runs,
        "cells": cells,
        "query_cells_requested": len(cells) + omitted_count,
        "query_cells_omitted": omitted_count,
        "has_more": bool(remaining),
        "next_cursor": next_cursor,
    }


def _blocked_result(plan: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    reason = _text(verdict.get("reason")) or "targeted_round_gate_denied"
    round_no = int(plan.get("round_no") or 1)
    runs = plan.get("runs") or []
    # The denied round itself remains executable later, so has_more is true
    # even when it was the final planned fallback.  The pipeline's round gate
    # carries the denial forward and will not silently skip to another query.
    blocked_cursor = {
        "schema": TARGETED_CURSOR_SCHEMA,
        "blocked_round": round_no,
        "retry_required": True,
    }
    return {
        "status": "blocked",
        "query": _text(runs[0].get("query")) if runs else "",
        "query_mode": "targeted_first_round_exact" if round_no == 1 else "targeted_fallback_exact",
        "new_creators": [],
        "existing_matches": [],
        "items": [],
        "platform_results": [],
        "errors": [{"status": "blocked", "reason": reason}],
        "provider_calls": False,
        "provider_call_count": 0,
        "has_more": bool(runs),
        "next_cursor": blocked_cursor if runs else {},
        "next_page_cursors": {},
        "query_cell_runs": [
            {
                "query_cell_id": run["query_cell_id"],
                "primary_query": run["primary_query"],
                "executed_query": run["query"],
                "query_variant": run["query_variant"],
                "fallback_index": run.get("fallback_index"),
                "round_no": round_no,
                "raw_limit": run["raw_limit"],
                "platforms": list(run["platforms"]),
                "per_platform_limits": dict(run["per_platform_limits"]),
                "status": "blocked",
                "returned": 0,
                "provider_calls": 0,
            }
            for run in runs
        ],
        "query_cells_requested": plan.get("query_cells_requested", 0),
        "query_cells_executed": 0,
        "query_cells_omitted": plan.get("query_cells_omitted", 0),
        "raw_candidate_occurrences": 0,
        "unique_candidate_count": 0,
        "candidate_cell_match_count": 0,
        "fallback_queries_used": False,
        "first_round_complete": False,
        "targeted_round_complete": False,
        "round_no": round_no,
        "provider_gate": verdict,
    }


async def execute_query_cell_round(
    *,
    query_cells: Any,
    base_kwargs: dict[str, Any],
    discover: Discover,
    round_no: int,
    before_provider_calls: Preflight | None = None,
) -> dict[str, Any]:
    """Execute one primary/fallback round after a mandatory optional preflight."""

    plan = plan_query_cell_round(
        query_cells=query_cells,
        base_kwargs=base_kwargs,
        round_no=round_no,
    )
    cells = plan["cells"]
    planned_runs = plan["runs"]
    omitted_count = int(plan["query_cells_omitted"])
    if not cells:
        return {
            "status": "invalid_query_cells",
            "query": "",
            "query_mode": "targeted_first_round_exact" if int(plan["round_no"]) == 1 else "targeted_fallback_exact",
            "new_creators": [],
            "existing_matches": [],
            "items": [],
            "platform_results": [],
            "errors": [{"status": "invalid_query_cells", "message": "no executable first-round query cell"}],
            "provider_calls": False,
            "provider_call_count": 0,
            "has_more": bool(plan["has_more"]),
            "next_cursor": dict(plan["next_cursor"]),
            "query_cell_runs": [],
            "query_cells_omitted": omitted_count,
            "raw_candidate_occurrences": 0,
            "unique_candidate_count": 0,
            "candidate_cell_match_count": 0,
            "round_no": int(plan["round_no"]),
        }
    if not planned_runs:
        return {
            "status": "exhausted",
            "query": "",
            "query_mode": "targeted_fallback_exact",
            "new_creators": [],
            "existing_matches": [],
            "items": [],
            "platform_results": [],
            "errors": [],
            "provider_calls": False,
            "provider_call_count": 0,
            "has_more": False,
            "next_cursor": {},
            "next_page_cursors": {},
            "query_cell_runs": [],
            "query_cells_requested": len(cells) + omitted_count,
            "query_cells_executed": 0,
            "query_cells_omitted": omitted_count,
            "raw_candidate_occurrences": 0,
            "unique_candidate_count": 0,
            "candidate_cell_match_count": 0,
            "fallback_queries_used": False,
            "first_round_complete": False,
            "targeted_round_complete": False,
            "round_no": int(plan["round_no"]),
        }

    if before_provider_calls is not None:
        try:
            verdict = before_provider_calls(plan)
        except Exception as exc:
            verdict = {
                "allowed": False,
                "reason": "targeted_round_preflight_unavailable",
                "error_type": type(exc).__name__,
            }
        verdict = verdict if isinstance(verdict, dict) else {}
        if verdict.get("allowed") is not True:
            return _blocked_result(plan, verdict)

    semaphore = asyncio.Semaphore(MAX_CELL_CONCURRENCY)

    async def _run(run: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | BaseException]:
        kwargs = {
            **base_kwargs,
            "query_text": run["query"],
            "search_query_en": run["query"],
            "platforms": run["platforms"],
            "limit": run["raw_limit"],
            "per_platform_limit": run["per_platform_limit"],
            "per_platform_limits": run["per_platform_limits"],
            "auto_enroll": False,
            "page_cursors": None,
            "exact_query": True,
        }
        try:
            async with semaphore:
                result = await discover(**kwargs)
            return run, result if isinstance(result, dict) else {}
        except Exception as exc:  # one failed cell must not erase other first-round evidence
            return run, exc

    outcomes = await asyncio.gather(*(_run(run) for run in planned_runs))
    candidates: list[dict[str, Any]] = []
    existing_matches: list[dict[str, Any]] = []
    platform_results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    run_summaries: list[dict[str, Any]] = []
    provider_call_count = 0
    used_platforms: list[str] = []
    for run, outcome in outcomes:
        cell = run["cell"]
        if isinstance(outcome, BaseException):
            errors.append({
                "query_cell_id": cell["query_cell_id"],
                "status": "failed",
                "message": type(outcome).__name__,
            })
            run_summaries.append({
                "query_cell_id": cell["query_cell_id"],
                "primary_query": run["primary_query"],
                "executed_query": run["query"],
                "query_variant": run["query_variant"],
                "fallback_index": run.get("fallback_index"),
                "round_no": run["round_no"],
                "raw_limit": run["raw_limit"],
                "status": "failed",
                "returned": 0,
                "provider_calls": 0,
            })
            continue
        called = outcome.get("provider_calls") is not False
        cell_platform_results = [
            raw for raw in (outcome.get("platform_results") or []) if isinstance(raw, dict)
        ]
        cell_provider_calls = (
            max(1, _int(outcome.get("provider_call_count"), len(cell_platform_results) or 1))
            if called else 0
        )
        provider_call_count += cell_provider_calls
        for platform in outcome.get("platforms") or []:
            normalized = _text(platform).lower()
            if normalized and normalized not in used_platforms:
                used_platforms.append(normalized)
        cell_candidates = [
            _annotate_candidate(raw, run)
            for raw in (outcome.get("new_creators") or [])
            if isinstance(raw, dict)
        ]
        candidates.extend(cell_candidates)
        existing_matches.extend(
            _annotate_candidate(raw, run)
            for raw in (outcome.get("existing_matches") or [])
            if isinstance(raw, dict)
        )
        for raw in cell_platform_results:
            platform_results.append({
                **raw,
                "query_cell_id": cell["query_cell_id"],
                "query_cell_query": run["query"],
                "query_variant": run["query_variant"],
                "round_no": run["round_no"],
            })
        for raw in outcome.get("errors") or []:
            if isinstance(raw, dict):
                errors.append({**raw, "query_cell_id": cell["query_cell_id"]})
        run_summaries.append({
            "query_cell_id": cell["query_cell_id"],
            "segment": cell.get("segment"),
            "primary_query": run["primary_query"],
            "executed_query": run["query"],
            "query_variant": run["query_variant"],
            "fallback_index": run.get("fallback_index"),
            "round_no": run["round_no"],
            "raw_limit": run["raw_limit"],
            "status": _text(outcome.get("status")) or "unknown",
            "returned": len(cell_candidates),
            "provider_calls": cell_provider_calls,
            "platforms": list(outcome.get("platforms") or run["platforms"]),
            "per_platform_limits": dict(outcome.get("per_platform_limits") or run["per_platform_limits"]),
            "query_mode": _text(outcome.get("query_mode")) or "exact_query_cell",
        })

    deduped = _dedupe_candidates(candidates)
    deduped_existing = _dedupe_candidates(existing_matches)
    candidate_cell_match_count = sum(
        len(_matched_cell_list(item.get("matched_query_cells")))
        for item in deduped
    )
    if deduped and errors:
        status = "partial"
    elif deduped:
        status = "ready"
    elif errors:
        status = "failed"
    else:
        status = "empty"
    return {
        "status": status,
        "query": run_summaries[0]["executed_query"],
        "query_mode": "targeted_first_round_exact" if int(plan["round_no"]) == 1 else "targeted_fallback_exact",
        "platforms": used_platforms,
        "new_creators": deduped,
        "existing_matches": deduped_existing,
        "items": [*deduped_existing, *deduped],
        "platform_results": platform_results,
        "errors": errors,
        "provider_calls": provider_call_count > 0,
        "provider_call_count": provider_call_count,
        "has_more": bool(plan["has_more"]),
        "next_cursor": dict(plan["next_cursor"]),
        "next_page_cursors": {},
        "query_cell_runs": run_summaries,
        "query_cells_requested": len(cells) + omitted_count,
        "query_cells_executed": len(run_summaries),
        "query_cells_omitted": omitted_count,
        "raw_candidate_occurrences": len(candidates),
        "unique_candidate_count": len(deduped),
        "candidate_cell_match_count": candidate_cell_match_count,
        "fallback_queries_used": int(plan["round_no"]) > 1,
        "first_round_complete": int(plan["round_no"]) == 1,
        "targeted_round_complete": True,
        "round_no": int(plan["round_no"]),
    }


__all__ = [
    "MAX_FIRST_ROUND_CELLS",
    "execute_query_cell_round",
    "execute_first_round_query_cells",
    "normalize_first_round_cells",
    "plan_query_cell_round",
]
