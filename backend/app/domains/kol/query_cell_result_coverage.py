"""Bounded, IO-free result coverage from server retrieval and qualification.

Discovery tags establish retrieval lineage only. Qualification observations must
come from the collector after its real gates; this is not a client input schema.
No per-cell financial allocation is inferred from an aggregate provider bill.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any

from app.domains.kol.search_plan_semantics import summarize_query_cell_coverage

MAX_CELLS = 8
MAX_IDENTITIES = 150
MAX_ROUNDS = 3
MIN_RAW_LIMIT = 10
MAX_RAW_LIMIT = 15


def _rows(value: Any, limit: int = 150) -> list[dict[str, Any]]:
    return [row for row in value[:limit] if isinstance(row, dict)] if isinstance(value, list) else []


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _count(value: Any, default: int = 0) -> int:
    return max(0, value) if type(value) is int else default


def _identity(value: Any) -> str:
    key = _text(value)
    if not key or key in {"pool:0", "kol:0"}:
        return ""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def new_cell_result_ledger(cells: Any) -> dict[str, Any]:
    return {"cells": {
        _text(cell.get("query_cell_id")): {
            "retrieved_count": 0, "identities": [], "unidentified_count": 0,
            "qualification": {}, "selected": [], "observed_rounds": [],
            "qualification_observed": False,
        }
        for cell in _rows(cells, MAX_CELLS) if _text(cell.get("query_cell_id"))
    }, "qualification_rounds": [], "identity_limit_reached": False, "identity_aliases": {}}


def _merge_identity_groups(ledger: dict[str, Any], previous: set[str], chosen: str) -> None:
    for row in (ledger.get("cells") or {}).values():
        for field in ("identities", "selected"):
            row[field] = list(dict.fromkeys(chosen if key in previous else key for key in row[field]))
        statuses = [row["qualification"].pop(key) for key in previous if key in row["qualification"]]
        if statuses:
            row["qualification"][chosen] = next(status for status in ("qualified", "pending", "rejected") if status in statuses)


def _resolved_identity(ledger: dict[str, Any], value: Any, aliases: Any = None) -> str:
    key = _identity(value)
    if not key:
        return ""
    lookup = ledger.setdefault("identity_aliases", {})
    tokens = {key}
    if isinstance(aliases, list):
        tokens.update(token for alias in aliases[:16] if (token := _identity(alias)))
    previous = {lookup[token] for token in tokens if token in lookup}
    chosen = min(previous) if previous else key
    if len(previous) > 1:
        _merge_identity_groups(ledger, previous, chosen)
        for token, group in list(lookup.items()):
            if group in previous:
                lookup[token] = chosen
    for token in tokens:
        if token in lookup or len(lookup) < MAX_IDENTITIES * 16:
            lookup[token] = chosen
        else:
            ledger["identity_limit_reached"] = True
    return chosen


def record_cell_retrieval(
    ledger: dict[str, Any], *, runs: Any, candidates: Any,
) -> None:
    """Count provider returns, then canonical identities after cross-cell merge.

    Candidate records contain only canonical_key and server query_cell_ids. A
    missing identity makes exact unique/duplicate counts unknown, never zero.
    """
    cells = ledger.get("cells") or {}
    called: set[str] = set()
    for run in _rows(runs, 32):
        cell_id = _text(run.get("query_cell_id"))
        if cell_id not in cells or _count(run.get("provider_calls")) < 1:
            continue
        called.add(cell_id)
        row = cells[cell_id]
        row["retrieved_count"] += _count(run.get("returned"))
        round_no = _count(run.get("round_no"), 1)
        if round_no not in row["observed_rounds"]:
            row["observed_rounds"].append(round_no)
    for candidate in _rows(candidates):
        identity = _resolved_identity(ledger, candidate.get("canonical_key"), candidate.get("aliases"))
        raw_ids = candidate.get("query_cell_ids")
        ids = set(value for value in raw_ids[:MAX_CELLS] if isinstance(value, str)) if isinstance(raw_ids, list) else set()
        for cell_id in ids.intersection(called):
            row = cells[cell_id]
            if not identity:
                row["unidentified_count"] += 1
            elif identity not in row["identities"]:
                if len(row["identities"]) < MAX_IDENTITIES:
                    row["identities"].append(identity)
                else:
                    ledger["identity_limit_reached"] = True


def _cell_outcome(observation: dict[str, Any], cell: dict[str, Any], *, provider_stop_reason: Any = "") -> str:
    if provider_stop_reason in {"provider_outcome_unknown", "provider_dispatch_blocked"}:
        return "pending"
    status = _text(observation.get("status"))
    if (status in {"selected", "qualified_overflow"} and cell.get("passed") is True
            and observation.get("eight_gates_passed") is True):
        return "qualified"
    if status == "pending":
        return "pending"
    return "rejected"


def postgate_qualification_stats(previous_stats: Any, observations: Any) -> dict[str, Any]:
    """Keep intent scores separate from complete, net-new eligibility counts."""
    previous = previous_stats if isinstance(previous_stats, dict) else {}
    counts = [sum(_cell_outcome(observation, cell) == "qualified"
                  for cell in _rows(observation.get("cells"), MAX_CELLS))
              for observation in _rows(observations)]
    return {
        **previous,
        "intent_qualified_cell_count": _count(previous.get("intent_qualified_cell_count"),
                                               _count(previous.get("qualified_cell_count"))),
        "qualified_cell_count": sum(counts),
        "candidate_with_qualified_cell_count": sum(count > 0 for count in counts),
    }


def observe_cell_qualification(ledger: dict[str, Any], payload: dict[str, Any]) -> None:
    """Fold real post-gate observations; raw matched tags never become passed."""
    round_no = _count(payload.get("round_no"))
    if round_no not in range(1, MAX_ROUNDS + 1) or round_no in ledger["qualification_rounds"]:
        return
    ledger["qualification_rounds"].append(round_no)
    cells = ledger.get("cells") or {}
    for row in cells.values():
        if round_no in row["observed_rounds"] and row["retrieved_count"] == 0:
            row["qualification_observed"] = True
    for observation in _rows(payload.get("observations")):
        identity = _resolved_identity(ledger, observation.get("canonical_key"))
        if not identity:
            continue
        for cell in _rows(observation.get("cells"), MAX_CELLS):
            row = cells.get(_text(cell.get("query_cell_id")))
            if row is None or identity not in row["identities"]:
                continue
            row["qualification_observed"] = True
            previous = row["qualification"].get(identity)
            if previous != "qualified":
                row["qualification"][identity] = _cell_outcome(
                    observation, cell, provider_stop_reason=payload.get("provider_stop_reason"),
                )
    for item in _rows(payload.get("accepted"), 30):
        identity = _resolved_identity(ledger, item.get("canonical_key"))
        for cell in _rows(item.get("cells"), MAX_CELLS):
            row = cells.get(_text(cell.get("query_cell_id")))
            if (row is not None and cell.get("passed") is True
                    and row["qualification"].get(identity) == "qualified"
                    and identity not in row["selected"]):
                row["selected"].append(identity)


def cell_result_counts(row: dict[str, Any]) -> dict[str, Any]:
    statuses = list((row.get("qualification") or {}).values())
    observed = row.get("qualification_observed") is True
    known = len(row.get("identities") or [])
    unidentified = _count(row.get("unidentified_count"))
    retrieved = _count(row.get("retrieved_count"))
    return {
        "retrieved_count": retrieved,
        "unique_count": known if not unidentified else None,
        "duplicate_count": max(0, retrieved - known) if not unidentified else None,
        "known_identity_count": known,
        "unidentified_count": unidentified,
        "pending_count": statuses.count("pending") if observed else None,
        "qualified_count": statuses.count("qualified") if observed else None,
        "rejected_count": statuses.count("rejected") if observed else None,
        "selected_count": len(row.get("selected") or []),
        "unassessed_count": max(0, known - len(statuses)) + unidentified,
        "qualification_status": "observed" if observed else "not_evaluated",
        "identity_scope": "server_canonical_creator_aliases",
    }


def choose_cell_round(
    cells: Any, *, ledger: dict[str, Any], round_no: int,
    candidate_limit: int, accepted_count: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Prefer minimum-coverage gaps inside existing round and raw-row limits."""
    maximum = min(MAX_IDENTITIES, _count(candidate_limit))
    diagnostic: dict[str, Any] = {
        "round_no": round_no, "candidate_limit": maximum,
        "selected_cell_ids": [], "deferred_cell_ids": [], "raw_limit_total": 0,
        "coverage_target": 1, "coverage_target_source": "service_minimum_per_cell",
        "user_requested_per_cell_quota": False, "reason": "",
    }
    if round_no not in range(1, MAX_ROUNDS + 1) or accepted_count >= 30:
        diagnostic["reason"] = "provider_round_budget_exhausted" if round_no > MAX_ROUNDS else "global_target_reached"
        return [], diagnostic
    available = [cell for cell in _rows(cells, MAX_CELLS)
                 if round_no == 1 or len(cell.get("fallback_queries") or []) >= round_no - 1]
    rows = ledger.get("cells") or {}
    gaps = [cell for cell in available if
            (cell_result_counts(rows.get(_text(cell.get("query_cell_id")), {}))["qualified_count"] or 0) < 1]
    choices = available if round_no == 1 or not gaps else gaps
    diagnostic["reason"] = "primary_cells" if round_no == 1 else "minimum_coverage_gaps" if gaps else "global_shortfall_after_minimum_coverage"
    selected: list[dict[str, Any]] = []
    for cell in choices:
        remaining = maximum - diagnostic["raw_limit_total"]
        if remaining < MIN_RAW_LIMIT:
            break
        limit = min(MAX_RAW_LIMIT, max(MIN_RAW_LIMIT, _count(cell.get("raw_limit"), 12)), remaining)
        selected.append({**deepcopy(cell), "raw_limit": limit})
        diagnostic["raw_limit_total"] += limit
        diagnostic["selected_cell_ids"].append(_text(cell.get("query_cell_id")))
    diagnostic["deferred_cell_ids"] = [_text(cell.get("query_cell_id")) for cell in available
                                       if _text(cell.get("query_cell_id")) not in diagnostic["selected_cell_ids"]]
    if not selected:
        diagnostic["reason"] = "candidate_budget_exhausted" if maximum < MIN_RAW_LIMIT else "no_targeted_fallback_remaining"
    return selected, diagnostic


def summarize_cell_results(
    cells: Any, *, runs: Any, ledger: dict[str, Any], omitted_count: int = 0,
) -> dict[str, Any]:
    summary = summarize_query_cell_coverage(cells, runs, omitted_count=omitted_count)
    result_rows = ledger.get("cells") or {}
    for cell in summary["cells"]:
        row = result_rows.get(cell["query_cell_id"], {})
        counts = cell_result_counts(row)
        cell.update(counts)
        cell.update({
            "minimum_qualified_target": 1,
            "coverage_target_source": "service_minimum_per_cell",
            "user_requested_per_cell_quota": False,
            "result_coverage_status": "covered" if (counts["qualified_count"] or 0) >= 1 else "partial",
            "actual_cost_usd": None,
            "cost_attribution": "unavailable_no_cell_scoped_settled_observation",
            "candidate_qualification": "server_gate_observations" if counts["qualification_status"] == "observed" else "not_evaluated",
        })
    summary["execution_status"] = summary["status"]
    if any(row["result_coverage_status"] != "covered" for row in summary["cells"]):
        summary["status"] = "partial"
    if ledger.get("identity_limit_reached"):
        summary["status"] = "partial"
        summary["identity_observations_truncated"] = True
    summary.update({
        "result_coverage_scope": "at_least_one_server_qualified_creator_per_cell",
        "coverage_target_source": "service_minimum_per_cell",
        "user_requested_per_cell_quota": False,
        "aggregate_cost_allocation_performed": False,
    })
    return summary
