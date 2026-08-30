"""Independent QueryCell execution for the provider-free local KOL pool."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.domains.kol import growth_candidate_scoring, targeted_query_execution
from app.domains.kol.identity import canonical_creator_aliases


Recall = Callable[..., dict[str, Any]]

# Server-owned bounds. QueryCell raw_limit is already normalized to 10..15,
# but it must not be allowed to multiply the legacy smart-local 500-candidate
# scan without an explicit aggregate ceiling.
SERVER_TOTAL_CANDIDATE_BUDGET = 240
SERVER_PER_CELL_CANDIDATE_CAP = 60
SERVER_DEFERRED_DISPLAY_CAP = 30


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _cell_context(cell: dict[str, Any]) -> dict[str, Any]:
    context = {
        "query_cell_id": cell.get("query_cell_id"),
        "objective": cell.get("objective"),
        "segment": cell.get("segment"),
        "segment_label": cell.get("segment_label"),
        "primary_query": cell.get("primary_query"),
        "required_evidence_groups": list(cell.get("required_evidence_groups") or []),
        "brand_or_model_required": cell.get("brand_or_model_required") is True,
        "brand_or_model_ranking_weight": cell.get("brand_or_model_ranking_weight"),
    }
    if isinstance(cell.get("follower_filter"), dict):
        context["follower_filter"] = dict(cell["follower_filter"])
    if isinstance(cell.get("locked_term_groups"), dict):
        context["locked_term_groups"] = dict(cell["locked_term_groups"])
    return context


def _annotate(items: list[dict[str, Any]], cell: dict[str, Any]) -> list[dict[str, Any]]:
    context = _cell_context(cell)
    return [
        {
            **item,
            "query_cell_id": cell["query_cell_id"],
            "query_cell_segment": cell.get("segment"),
            "query_cell_query": cell["primary_query"],
            "matched_query_cells": [context],
        }
        for item in items
    ]


def _rank_key(item: dict[str, Any]) -> tuple[float, float, float]:
    growth = item.get("growth_candidate_score")
    if growth is not None:
        return (
            _number(growth),
            _number(item.get("evidence_confidence")),
            _number(item.get("display_rank_score")),
        )
    return (
        _number(item.get("display_rank_score")),
        _number(item.get("recall_rank_score")),
        _number(item.get("retrieval_score")),
    )


def _merge_matches(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    winner, other = (
        (existing, incoming)
        if _rank_key(existing) >= _rank_key(incoming)
        else (incoming, existing)
    )
    merged = dict(winner)
    for key, value in other.items():
        if merged.get(key) in (None, "", [], {}):
            merged[key] = value
    contexts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in (existing, incoming):
        for raw in source.get("matched_query_cells") or []:
            if not isinstance(raw, dict):
                continue
            cell_id = _text(raw.get("query_cell_id"))
            if cell_id and cell_id not in seen:
                seen.add(cell_id)
                contexts.append(dict(raw))
    merged["matched_query_cells"] = contexts
    return merged


def _identity_keys(item: dict[str, Any]) -> set[str]:
    aliases = canonical_creator_aliases(item)
    if aliases:
        return {f"alias:{alias}" for alias in aliases}
    return {
        "fallback:"
        f"{_text(item.get('platform')).lower()}:"
        f"{_text(item.get('handle')).lstrip('@').lower()}:"
        f"{item.get('kol_pool_id') or ''}"
    }


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    aliases: list[set[str]] = []
    fallback: dict[str, int] = {}
    for item in items:
        item_aliases = canonical_creator_aliases(item)
        index = next(
            (position for position, known in enumerate(aliases) if item_aliases and item_aliases.intersection(known)),
            None,
        )
        if index is None and not item_aliases:
            key = (
                f"{_text(item.get('platform')).lower()}:"
                f"{_text(item.get('handle')).lstrip('@').lower()}:"
                f"{item.get('kol_pool_id') or ''}"
            )
            index = fallback.get(key)
        if index is None:
            index = len(output)
            output.append(item)
            aliases.append(set(item_aliases))
            if not item_aliases:
                fallback[key] = index
            continue
        output[index] = _merge_matches(output[index], item)
        aliases[index].update(item_aliases)
    return output


def _balanced_take(
    items: list[dict[str, Any]],
    *,
    target: int,
    creator_quota: int,
    reviewer_quota: int,
) -> list[dict[str, Any]]:
    creators = [item for item in items if item.get("bucket") != "reviewer"]
    reviewers = [item for item in items if item.get("bucket") == "reviewer"]
    chosen = [*creators[:creator_quota], *reviewers[:reviewer_quota]]
    chosen_ids = {id(item) for item in chosen}
    chosen.extend(item for item in items if id(item) not in chosen_ids)
    return sorted(chosen[:target], key=_rank_key, reverse=True)


def _candidate_budget_allocations(cell_count: int) -> list[int]:
    """Split one server-owned budget fairly and deterministically across cells."""

    count = max(0, int(cell_count or 0))
    if not count:
        return []
    total = min(SERVER_TOTAL_CANDIDATE_BUDGET, count * SERVER_PER_CELL_CANDIDATE_CAP)
    base, remainder = divmod(total, count)
    return [
        min(SERVER_PER_CELL_CANDIDATE_CAP, base + (1 if index < remainder else 0))
        for index in range(count)
    ]


def _qualification_state(item: dict[str, Any]) -> tuple[bool, bool]:
    proof = item.get("qualification_evidence")
    if not isinstance(proof, dict):
        item["counts_toward_target"] = False
        return False, False
    # Copy before adding the aggregation flag; callers may reuse the recall
    # response object for another cell or persistence projection.
    proof = dict(proof)
    item["qualification_evidence"] = proof
    passed = proof.get("passed") is True
    deferred = proof.get("deferred") is True and not passed
    item["counts_toward_target"] = passed
    proof["counts_toward_target"] = passed
    return passed, deferred


def _effective_candidate_limit(result: dict[str, Any]) -> int | None:
    query = result.get("query") if isinstance(result.get("query"), dict) else {}
    value = query.get("candidate_limit")
    try:
        return max(0, int(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _aggregate_qualification(
    contracts: list[dict[str, Any]],
    *,
    items: list[dict[str, Any]],
    qualified_available: list[dict[str, Any]],
    deferred_items: list[dict[str, Any]],
    deferred_available: int,
    target: int,
    growth_rejected: int,
    local_not_passed: int,
    unique_evaluated: int,
) -> dict[str, Any]:
    first = next((contract for contract in contracts if contract.get("schema") == "smart_local_qualified_v2"), {})
    rejected: dict[str, int] = {}
    funnel: dict[str, int] = {}
    evaluated = 0
    for contract in contracts:
        evaluated += int(contract.get("evaluated_count") or 0)
        for source, destination in (
            (contract.get("rejected_by_reason"), rejected),
            (contract.get("funnel"), funnel),
        ):
            if not isinstance(source, dict):
                continue
            for key, value in source.items():
                destination[str(key)] = destination.get(str(key), 0) + int(value or 0)
    if growth_rejected:
        rejected["prospective_product_scene_or_activation_missing"] = growth_rejected
    if local_not_passed:
        rejected["local_qualification_not_passed"] = local_not_passed
    shortfall = max(0, target - len(items))
    return {
        "schema": "smart_local_qualified_v2",
        "status": "ready" if not shortfall else "shortfall",
        "policy": dict(first.get("policy") or {}),
        "qualified_count": len(qualified_available),
        "returned_count": len(items),
        "qualified_returned_count": len(items),
        "shortfall": shortfall,
        "shortfall_reason": "" if not shortfall else "targeted_query_cells_exhausted",
        "evaluated_count": evaluated,
        "funnel_scope": "cell_candidate_evaluations",
        "unique_evaluated": unique_evaluated,
        "unique_evaluated_scope": "returned_cell_candidates_with_identity",
        "unique_qualified": len(qualified_available),
        "funnel": funnel,
        "rejected_by_reason": rejected,
        "gate_evidence_scope": "returned_targeted_candidates",
        "deferred_activity": {
            "available": deferred_available,
            "returned": len(deferred_items),
            "counts_toward_target": False,
            "selectable": True,
        },
        "ratio_policy": dict(first.get("ratio_policy") or {}),
    }


@dataclass(frozen=True)
class _CellRunOutcome:
    result: dict[str, Any]
    contract: dict[str, Any] | None
    evaluated_rows: list[dict[str, Any]]
    qualified_rows: list[dict[str, Any]]
    deferred_rows: list[dict[str, Any]]
    growth_rejected: int
    local_not_passed: int
    diagnostics: dict[str, Any]


@dataclass
class _CellExecution:
    results: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    deferred_candidates: list[dict[str, Any]] = field(default_factory=list)
    evaluated_candidates: list[dict[str, Any]] = field(default_factory=list)
    contracts: list[dict[str, Any]] = field(default_factory=list)
    cell_runs: list[dict[str, Any]] = field(default_factory=list)
    growth_rejected: int = 0
    local_not_passed: int = 0


@dataclass(frozen=True)
class _CandidateSelection:
    selected: list[dict[str, Any]]
    qualified_available: list[dict[str, Any]]
    deferred_display: list[dict[str, Any]]
    deferred_available: int
    creator: list[dict[str, Any]]
    reviewer: list[dict[str, Any]]
    business_buckets: dict[str, list[dict[str, Any]]]
    unique_evaluated: int


def _safe_target(target: Any) -> int:
    try:
        return max(1, min(int(target or 30), 30))
    except (TypeError, ValueError):
        return 30


def _search_objective(
    brief: dict[str, Any],
    cells: list[dict[str, Any]],
) -> str:
    return _text(
        brief.get("objective")
        or cells[0].get("objective")
        or "prospective_growth"
    )


def _cell_recall_kwargs(
    *,
    base_kwargs: dict[str, Any],
    cell: dict[str, Any],
    candidate_cap: int,
    objective: str,
) -> dict[str, Any]:
    return {
        **base_kwargs,
        "query_text": cell["primary_query"],
        "candidate_limit": candidate_cap,
        "server_candidate_limit_override": candidate_cap,
        "limit": min(30, candidate_cap),
        "required_product_evidence_terms": (
            base_kwargs.get("required_product_evidence_terms")
            if objective == "existing_evidence"
            else None
        ),
        **(
            {"targeted_query_cell": cell}
            if objective == "prospective_growth"
            else {}
        ),
    }


def _candidate_rows(
    result: dict[str, Any],
    cell: dict[str, Any],
    candidate_cap: int,
) -> list[dict[str, Any]]:
    rows = [
        dict(item)
        for item in (result.get("items") or [])
        if isinstance(item, dict)
    ]
    rows = _annotate(rows, cell)
    rows.sort(key=_rank_key, reverse=True)
    return rows[:candidate_cap]


def _growth_score_rows(
    rows: list[dict[str, Any]],
    *,
    objective: str,
    brief: dict[str, Any],
    cell: dict[str, Any],
) -> list[dict[str, Any]]:
    if objective != "prospective_growth":
        return rows
    return growth_candidate_scoring.score_growth_candidates(
        rows,
        search_brief=brief,
        query_cell=cell,
    )


def _partition_qualified_rows(
    rows: list[dict[str, Any]],
    *,
    objective: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int]:
    qualified_rows: list[dict[str, Any]] = []
    deferred_rows: list[dict[str, Any]] = []
    growth_rejected = 0
    local_not_passed = 0
    for row in rows:
        local_passed, activity_deferred = _qualification_state(row)
        growth_passed = True
        if objective == "prospective_growth":
            growth_passed = bool(
                row.get("product_scene_evidence_pass") is True
                and row.get("market_activation_pass") is True
            )
            row["growth_qualification_pass"] = growth_passed
            if not growth_passed:
                growth_rejected += 1
        counts_toward_target = local_passed and growth_passed
        row["counts_toward_target"] = counts_toward_target
        proof = row.get("qualification_evidence")
        if isinstance(proof, dict):
            proof["counts_toward_target"] = counts_toward_target
        if counts_toward_target:
            qualified_rows.append(row)
        elif activity_deferred and growth_passed:
            deferred_rows.append(row)
        elif growth_passed:
            local_not_passed += 1
    return qualified_rows, deferred_rows, growth_rejected, local_not_passed


def _cell_run_diagnostics(
    *,
    cell: dict[str, Any],
    candidate_cap: int,
    result: dict[str, Any],
    evaluated_count: int,
    qualified_rows: list[dict[str, Any]],
    deferred_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    effective_cap = _effective_candidate_limit(result)
    return {
        "query_cell_id": cell["query_cell_id"],
        "segment": cell.get("segment"),
        "primary_query": cell["primary_query"],
        "raw_limit": cell["raw_limit"],
        "candidate_limit_requested": candidate_cap,
        "candidate_limit_effective": effective_cap,
        "candidate_limit_honored": (
            effective_cap <= candidate_cap if effective_cap is not None else None
        ),
        "aggregation_evaluated": evaluated_count,
        "recall_returned": len(result.get("items") or []),
        "qualified_returned": len(qualified_rows),
        "deferred_returned": len(deferred_rows),
    }


def _execute_cell(
    *,
    cell: dict[str, Any],
    candidate_cap: int,
    objective: str,
    brief: dict[str, Any],
    base_kwargs: dict[str, Any],
    recall: Recall,
) -> _CellRunOutcome:
    result = recall(
        **_cell_recall_kwargs(
            base_kwargs=base_kwargs,
            cell=cell,
            candidate_cap=candidate_cap,
            objective=objective,
        )
    )
    result = result if isinstance(result, dict) else {}
    contract = result.get("local_qualification")
    contract = contract if isinstance(contract, dict) else None
    rows = _candidate_rows(result, cell, candidate_cap)
    evaluated_rows = list(rows)
    rows = _growth_score_rows(
        rows,
        objective=objective,
        brief=brief,
        cell=cell,
    )
    qualified_rows, deferred_rows, growth_rejected, local_not_passed = (
        _partition_qualified_rows(rows, objective=objective)
    )
    qualified_rows.sort(key=_rank_key, reverse=True)
    deferred_rows.sort(key=_rank_key, reverse=True)
    qualified_rows = qualified_rows[: int(cell["raw_limit"])]
    deferred_rows = deferred_rows[: int(cell["raw_limit"])]
    diagnostics = _cell_run_diagnostics(
        cell=cell,
        candidate_cap=candidate_cap,
        result=result,
        evaluated_count=len(rows),
        qualified_rows=qualified_rows,
        deferred_rows=deferred_rows,
    )
    return _CellRunOutcome(
        result=result,
        contract=contract,
        evaluated_rows=evaluated_rows,
        qualified_rows=qualified_rows,
        deferred_rows=deferred_rows,
        growth_rejected=growth_rejected,
        local_not_passed=local_not_passed,
        diagnostics=diagnostics,
    )


def _execute_cells(
    *,
    cells: list[dict[str, Any]],
    candidate_allocations: list[int],
    objective: str,
    brief: dict[str, Any],
    base_kwargs: dict[str, Any],
    recall: Recall,
) -> _CellExecution:
    execution = _CellExecution()
    for cell, candidate_cap in zip(cells, candidate_allocations, strict=True):
        outcome = _execute_cell(
            cell=cell,
            candidate_cap=candidate_cap,
            objective=objective,
            brief=brief,
            base_kwargs=base_kwargs,
            recall=recall,
        )
        execution.results.append(outcome.result)
        if outcome.contract is not None:
            execution.contracts.append(outcome.contract)
        execution.evaluated_candidates.extend(outcome.evaluated_rows)
        execution.candidates.extend(outcome.qualified_rows)
        execution.deferred_candidates.extend(outcome.deferred_rows)
        execution.growth_rejected += outcome.growth_rejected
        execution.local_not_passed += outcome.local_not_passed
        execution.cell_runs.append(outcome.diagnostics)
    return execution


def _deferred_display_rows(
    deferred_candidates: list[dict[str, Any]],
    qualified_identity_keys: set[str],
) -> tuple[list[dict[str, Any]], int]:
    available = [
        item
        for item in sorted(_dedupe(deferred_candidates), key=_rank_key, reverse=True)
        if _identity_keys(item).isdisjoint(qualified_identity_keys)
    ]
    display = available[:SERVER_DEFERRED_DISPLAY_CAP]
    for item in display:
        item["counts_toward_target"] = False
        proof = item.get("qualification_evidence")
        if isinstance(proof, dict):
            proof["counts_toward_target"] = False
    return display, len(available)


def _select_candidates(
    execution: _CellExecution,
    *,
    safe_target: int,
    base_kwargs: dict[str, Any],
) -> _CandidateSelection:
    merged = sorted(_dedupe(execution.candidates), key=_rank_key, reverse=True)
    qualified_identity_keys = {
        key for item in merged for key in _identity_keys(item)
    }
    deferred_display, deferred_available = _deferred_display_rows(
        execution.deferred_candidates,
        qualified_identity_keys,
    )
    unique_evaluated = len(_dedupe(execution.evaluated_candidates))
    creator_quota = max(0, int(base_kwargs.get("creator_quota") or 15))
    reviewer_quota = max(0, int(base_kwargs.get("reviewer_quota") or 15))
    selected = _balanced_take(
        merged,
        target=safe_target,
        creator_quota=creator_quota,
        reviewer_quota=reviewer_quota,
    )
    creator = [item for item in selected if item.get("bucket") != "reviewer"]
    reviewer = [item for item in selected if item.get("bucket") == "reviewer"]
    business_buckets = {
        lane: [item for item in selected if item.get("candidate_bucket") == lane]
        for lane in ("core_vertical", "expansion", "exploration")
    }
    return _CandidateSelection(
        selected=selected,
        qualified_available=merged,
        deferred_display=deferred_display,
        deferred_available=deferred_available,
        creator=creator,
        reviewer=reviewer,
        business_buckets=business_buckets,
        unique_evaluated=unique_evaluated,
    )


def _candidate_budget(
    execution: _CellExecution,
    candidate_allocations: list[int],
    unique_evaluated: int,
) -> dict[str, Any]:
    known_honored = [
        run["candidate_limit_honored"]
        for run in execution.cell_runs
        if run.get("candidate_limit_honored") is not None
    ]
    recall_layer_honored = bool(known_honored) and all(known_honored)
    if recall_layer_honored:
        enforcement = "recall_layer_and_post_recall"
    elif known_honored:
        enforcement = "post_recall_deterministic_due_recall_policy_override"
    else:
        enforcement = "requested_and_post_recall_capped_effective_limit_unknown"
    upstream_evaluated = sum(
        int(contract.get("evaluated_count") or 0)
        for contract in execution.contracts
    )
    requested_total = sum(candidate_allocations)
    return {
        "owner": "server",
        "total_cap": SERVER_TOTAL_CANDIDATE_BUDGET,
        "per_cell_cap": SERVER_PER_CELL_CANDIDATE_CAP,
        "requested_total": requested_total,
        "upstream_cell_candidate_evaluations": upstream_evaluated,
        "upstream_within_requested_budget": upstream_evaluated <= requested_total,
        "aggregation_consumed": len(execution.evaluated_candidates),
        "unique_consumed": unique_evaluated,
        "recall_layer_honored": recall_layer_honored if known_honored else None,
        "enforcement": enforcement,
    }


def _project_local_response(
    *,
    first: dict[str, Any],
    cells: list[dict[str, Any]],
    omitted: int,
    objective: str,
    safe_target: int,
    selection: _CandidateSelection,
    qualification: dict[str, Any],
    execution: _CellExecution,
    candidate_budget: dict[str, Any],
) -> dict[str, Any]:
    selected = selection.selected
    return {
        **first,
        "method": "targeted_local_query_cells_v1",
        "match_status": "matched" if selected else "empty",
        "candidate_set_distribution": None,
        "items": selected,
        "deferred_items": selection.deferred_display,
        "buckets": {
            "creator": selection.creator,
            "reviewer": selection.reviewer,
            "unknown": [],
        },
        "business_buckets": selection.business_buckets,
        "local_qualification": qualification,
        "query": {
            **(first.get("query") if isinstance(first.get("query"), dict) else {}),
            "query_text": cells[0]["primary_query"],
            "query_cells": [_cell_context(cell) for cell in cells],
            "query_mode": "independent_query_cells",
        },
        "ranking": {
            **(
                first.get("ranking")
                if isinstance(first.get("ranking"), dict)
                else {}
            ),
            "growth_candidate_version": growth_candidate_scoring.SCORING_VERSION,
            "growth_candidate_primary": objective == "prospective_growth",
            "claim_status": "descriptive_only",
        },
        "diagnostics": {
            **(
                first.get("diagnostics")
                if isinstance(first.get("diagnostics"), dict)
                else {}
            ),
            "requested_count": safe_target,
            "returned_count": len(selected),
            "final_count": len(selected),
            "shortfall": max(0, safe_target - len(selected)),
            "result_contract_satisfied": len(selected) >= safe_target,
            "query_cells_requested": len(cells) + omitted,
            "query_cells_executed": len(cells),
            "query_cells_omitted": omitted,
            "growth_evidence_rejected": execution.growth_rejected,
            "local_qualification_not_passed": execution.local_not_passed,
            "deferred_display_count": len(selection.deferred_display),
            "unique_evaluated": selection.unique_evaluated,
            "unique_qualified": len(selection.qualified_available),
            "candidate_budget": candidate_budget,
            "targeted_cell_runs": execution.cell_runs,
        },
    }


def execute_first_round_local_cells(
    *,
    query_cells: Any,
    search_brief: dict[str, Any] | None,
    base_kwargs: dict[str, Any],
    recall: Recall,
    target: int = 30,
) -> dict[str, Any]:
    """Run exact local recall once per cell, growth-gate, merge, then cap."""

    cells, omitted = targeted_query_execution.normalize_first_round_cells(query_cells)
    if not cells:
        return recall(**base_kwargs)

    safe_target = _safe_target(target)
    brief = search_brief if isinstance(search_brief, dict) else {}
    objective = _search_objective(brief, cells)
    candidate_allocations = _candidate_budget_allocations(len(cells))
    execution = _execute_cells(
        cells=cells,
        candidate_allocations=candidate_allocations,
        objective=objective,
        brief=brief,
        base_kwargs=base_kwargs,
        recall=recall,
    )
    selection = _select_candidates(
        execution,
        safe_target=safe_target,
        base_kwargs=base_kwargs,
    )
    first = execution.results[0] if execution.results else {}
    qualification = _aggregate_qualification(
        execution.contracts,
        items=selection.selected,
        qualified_available=selection.qualified_available,
        deferred_items=selection.deferred_display,
        deferred_available=selection.deferred_available,
        target=safe_target,
        growth_rejected=execution.growth_rejected,
        local_not_passed=execution.local_not_passed,
        unique_evaluated=selection.unique_evaluated,
    )
    budget = _candidate_budget(
        execution,
        candidate_allocations,
        selection.unique_evaluated,
    )
    return _project_local_response(
        first=first,
        cells=cells,
        omitted=omitted,
        objective=objective,
        safe_target=safe_target,
        selection=selection,
        qualification=qualification,
        execution=execution,
        candidate_budget=budget,
    )


__all__ = ["execute_first_round_local_cells"]
