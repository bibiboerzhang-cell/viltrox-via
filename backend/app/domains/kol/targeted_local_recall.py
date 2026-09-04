"""Independent QueryCell execution for the provider-free local KOL pool."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.domains.kol import (
    growth_candidate_scoring,
    profile_recall_backfill_ladder as _ladder,
    search_relaxation as _relax,
    targeted_query_execution,
)
from app.domains.kol.targeted_local_backfill import (
    _aggregate_backfill,
    _backfill_rows,
    _backfill_take,
)
from app.domains.kol.targeted_local_selection import balanced_take as _balanced_take
from app.domains.kol.targeted_local_support import (
    _annotate,
    _cell_context,
    _dedupe,
    _identity_keys,
    _rank_key,
    _text,
)


Recall = Callable[..., dict[str, Any]]

# Server-owned bounds. QueryCell raw_limit is already normalized to 10..15,
# but it must not be allowed to multiply the legacy smart-local 500-candidate
# scan without an explicit aggregate ceiling.
SERVER_TOTAL_CANDIDATE_BUDGET = 240
SERVER_PER_CELL_CANDIDATE_CAP = 60
SERVER_DEFERRED_DISPLAY_CAP = 30


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
    # 缺口只按精准命中计:回填区(带 backfill_tier 标记)永远不冒充精准命中。
    precise = [item for item in items if not _ladder.is_backfill_item(item)]
    shortfall = max(0, target - len(precise))
    return {
        "schema": "smart_local_qualified_v2",
        "status": "ready" if not shortfall else "shortfall",
        "policy": dict(first.get("policy") or {}),
        "qualified_count": len(qualified_available),
        "returned_count": len(items),
        "qualified_returned_count": len(precise),
        "precise_returned_count": len(precise),
        "backfill_returned_count": len(items) - len(precise),
        "backfill": _aggregate_backfill(contracts, items=items, target=target),
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
    growth_supplement_rows: list[dict[str, Any]]
    backfill_rows: list[dict[str, Any]]
    growth_rejected: int
    local_not_passed: int
    diagnostics: dict[str, Any]


@dataclass
class _CellExecution:
    results: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    deferred_candidates: list[dict[str, Any]] = field(default_factory=list)
    backfill_candidates: list[dict[str, Any]] = field(default_factory=list)
    evaluated_candidates: list[dict[str, Any]] = field(default_factory=list)
    contracts: list[dict[str, Any]] = field(default_factory=list)
    cell_runs: list[dict[str, Any]] = field(default_factory=list)
    growth_rejected: int = 0
    local_not_passed: int = 0


@dataclass(frozen=True)
class _CandidateSelection:
    selected: list[dict[str, Any]]
    precise: list[dict[str, Any]]
    backfill: list[dict[str, Any]]
    backfill_available: int
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
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    int,
    int,
]:
    """Separate strict matches from visible, honestly-labelled supplements.

    Search and outreach are different decisions.  A missing three-video
    activation sample must keep a row out of the strict outreach target, but it
    must not erase a creator that otherwise matches the operator's natural
    language request.  Such rows remain visible as qualification backfill and
    carry the exact missing evidence; explicit market/platform/language and
    account-quality hard filters have already run upstream and stay hard.
    """

    qualified_rows: list[dict[str, Any]] = []
    deferred_rows: list[dict[str, Any]] = []
    growth_supplement_rows: list[dict[str, Any]] = []
    growth_rejected = 0
    local_not_passed = 0
    for row in rows:
        local_passed, activity_deferred = _qualification_state(row)
        growth_passed = True
        growth_reasons: list[str] = []
        if objective == "prospective_growth":
            scene_passed = row.get("product_scene_evidence_pass") is True
            activation_passed = row.get("market_activation_pass") is True
            growth_passed = bool(scene_passed and activation_passed)
            if not scene_passed:
                growth_reasons.append("product_scene_evidence_missing")
            if not activation_passed:
                activation_status = str(
                    row.get("market_activation_status") or "market_activation_missing"
                ).strip()
                growth_reasons.append(activation_status or "market_activation_missing")
            row["growth_qualification_pass"] = growth_passed
            row["growth_qualification_reasons"] = growth_reasons
            row["growth_qualification_state"] = (
                "strict_qualified"
                if growth_passed
                else "below_floor"
                if "below_floor" in growth_reasons
                else "evidence_pending"
            )
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
        elif local_passed and not growth_passed:
            # Keep relevant creators visible without promoting them into the
            # strict target.  The existing backfill contract guarantees they
            # are labelled, non-counting and sorted behind precise matches.
            _ladder.mark_backfill_item(row, _ladder.TIER_QUALIFICATION_RELAXED)
            notes = [
                str(value)
                for value in (row.get("selection_notes") or ())
                if str(value)
            ]
            if "product_scene_evidence_missing" in growth_reasons:
                note = "产品使用场景待核验"
                if note not in notes:
                    notes.append(note)
            activation_reason = next(
                (
                    reason
                    for reason in growth_reasons
                    if reason != "product_scene_evidence_missing"
                ),
                "",
            )
            if activation_reason:
                note = (
                    "市场活性未达门槛"
                    if activation_reason == "below_floor"
                    else "市场表现数据待补"
                )
                if note not in notes:
                    notes.append(note)
            row["selection_notes"] = notes
            row["growth_evidence_pending"] = row["growth_qualification_state"] == "evidence_pending"
            growth_supplement_rows.append(row)
        elif growth_passed:
            local_not_passed += 1
    return (
        qualified_rows,
        deferred_rows,
        growth_supplement_rows,
        growth_rejected,
        local_not_passed,
    )


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
        "backfill_returned": len(result.get("backfill_items") or []),
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
    (
        qualified_rows,
        deferred_rows,
        growth_supplement_rows,
        growth_rejected,
        local_not_passed,
    ) = (
        _partition_qualified_rows(rows, objective=objective)
    )
    qualified_rows.sort(key=_rank_key, reverse=True)
    deferred_rows.sort(key=_rank_key, reverse=True)
    qualified_rows = qualified_rows[: int(cell["raw_limit"])]
    deferred_rows = deferred_rows[: int(cell["raw_limit"])]
    # 回填区只打分不设闸(它本来就是「放宽后」的人),标记原样保留,永不计入目标。
    backfill_rows = _backfill_rows(result, cell, candidate_cap)
    # Growth evidence gaps are a display/backfill concern, not a reason to
    # erase otherwise relevant creators from search results.
    backfill_rows.extend(growth_supplement_rows)
    backfill_rows = _dedupe(backfill_rows)
    backfill_rows.sort(key=_rank_key, reverse=True)
    backfill_rows = backfill_rows[:candidate_cap]
    if backfill_rows:
        backfill_rows = _growth_score_rows(
            backfill_rows,
            objective=objective,
            brief=brief,
            cell=cell,
        )
    for row in backfill_rows:
        row["counts_toward_target"] = False
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
        growth_supplement_rows=growth_supplement_rows,
        backfill_rows=backfill_rows,
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
        execution.backfill_candidates.extend(outcome.backfill_rows)
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
    precise = _balanced_take(
        merged,
        target=safe_target,
        creator_quota=creator_quota,
        reviewer_quota=reviewer_quota,
    )
    backfill, backfill_available = _backfill_take(
        execution.backfill_candidates,
        taken_identity_keys=qualified_identity_keys
        | {key for item in deferred_display for key in _identity_keys(item)},
        capacity=safe_target - len(precise),
    )
    selected = [*precise, *backfill]
    creator = [item for item in selected if item.get("bucket") != "reviewer"]
    reviewer = [item for item in selected if item.get("bucket") == "reviewer"]
    business_buckets = {
        lane: [item for item in selected if item.get("candidate_bucket") == lane]
        for lane in ("core_vertical", "expansion", "exploration")
    }
    return _CandidateSelection(
        selected=selected,
        precise=precise,
        backfill=backfill,
        backfill_available=backfill_available,
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


def _cell_favorite_exclusion(result: dict[str, Any]) -> tuple[int, list[int]]:
    """一个 cell 的「已被同事关注而排除」计数 + 身份样本(样本可能被服务端截断)。"""

    diagnostics = result.get("diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    block = diagnostics.get("favorite_exclusion")
    block = block if isinstance(block, dict) else {}
    raw_count = diagnostics.get("favorite_excluded_count")
    count = raw_count if isinstance(raw_count, int) and raw_count > 0 else 0
    sample = [value for value in (block.get("excluded_ids") or ()) if isinstance(value, int)]
    return count, sample


def _favorite_excluded_unique(execution: _CellExecution) -> int:
    """按身份去重后的「已被同事关注、本次未展示」人数。

    逐 cell 相加会把同一个人算很多遍(同一个库被每个 cell 各查一次),这个数要印在门面上
    ("另有 N 人已被同事关注"),虚高比不说更糟。召回诊断里带着被排除的人的 id 样本:
    样本齐全就按身份去重取真值;样本被截断(身份拿不全)就退回单 cell 最大值——宁可少说,
    也不虚报。
    """

    identities: set[int] = set()
    per_cell: list[int] = []
    sample_complete = True
    for result in execution.results:
        count, sample = _cell_favorite_exclusion(result)
        per_cell.append(count)
        identities.update(sample)
        if len(sample) < count:
            sample_complete = False
    return len(identities) if sample_complete else max(per_cell, default=0)


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
        # 定向路径的补充人选**已经并进 ``items``** 并逐人盖了章(``backfill_tier`` /
        # ``precision_match=False`` / ``counts_toward_target=False``),顶层再放一份就是
        # 同一批人的第二个引用。而 ``{**first}`` 继承下来的那份是**第一个 cell 的原始召回
        # 残值**——既不是这次选中的人,数量也对不上(实测 items=20 而残值=15)。
        # 契约定死:定向路径此键恒空,消费方只按 ``items`` 里的标记分区;非定向兜底路径
        # 仍然只把补充人选放在这个键里,所以键本身保留,不删。
        "backfill_items": [],
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
            "precise_count": len(selection.precise),
            "backfill_count": len(selection.backfill),
            "backfill_available_count": selection.backfill_available,
            # 缺口 / 契约只按精准命中计;回填人已带标记,不冒充命中。
            "shortfall": max(0, safe_target - len(selection.precise)),
            "result_contract_satisfied": len(selection.precise) >= safe_target,
            "backfill_ladder": qualification.get("backfill"),
            "result_explanation": _ladder.explain_result(
                requested=safe_target,
                precise_count=len(selection.precise),
                backfill_by_tier=(qualification.get("backfill") or {}).get("filled_by_tier"),
                gaps=(qualification.get("backfill") or {}).get("gaps"),
                favorite_excluded=_favorite_excluded_unique(execution),
                favorite_annotated=sum(
                    1 for item in selected if _relax.is_team_favorite(item)
                ),
                # 「资料待核验」的人另列一区展示,门面照实计入总人数,但绝不并进精准命中。
                deferred_count=len(selection.deferred_display),
            ),
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
