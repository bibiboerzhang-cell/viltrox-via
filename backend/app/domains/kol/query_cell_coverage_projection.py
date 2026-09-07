"""Narrow public QueryCell counters; no raw evidence, contact data or costs.

The session scrubber still handles the whole payload. Only this bounded schema
is reprojected after scrubbing so missing measurements remain null on readback.
"""
from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any

SCHEMA = "query_cell_coverage_v1"
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,119}$")
_PHONE = re.compile(r"\d[\d(). -]{5,}\d")
_EXECUTION = frozenset({"covered", "partial", "not_executed", "not_executed_duplicate_query",
                        "blocked_or_unverified", "legacy_unverified"})
_COUNTS = {
    "retrieved_count": 1_000_000, "unique_count": 150, "duplicate_count": 1_000_000,
    "known_identity_count": 150, "unidentified_count": 150, "pending_count": 150,
    "qualified_count": 150, "rejected_count": 150, "selected_count": 30,
    "unassessed_count": 150, "provider_runs_reported": 32,
}


def _count(value: Any, maximum: int = 1_000_000) -> int | None:
    return value if type(value) is int and 0 <= value <= maximum else None


def _enum(value: Any, choices: Any, default: str) -> str:
    return value if isinstance(value, str) and value in choices else default


def _cell_id(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        return ""
    phone = _PHONE.search(value)
    return "" if phone and len(re.sub(r"\D", "", phone.group())) >= 7 else value


def _project_cell(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not (cell_id := _cell_id(value.get("query_cell_id"))):
        return {}
    statuses = value.get("count_statuses")
    statuses = statuses if isinstance(statuses, dict) else {}
    counts = {key: None if statuses.get(key) == "unknown" else _count(value.get(key), maximum)
              for key, maximum in _COUNTS.items()}
    qualification = _enum(value.get("qualification_status"), {"observed", "not_evaluated"}, "not_evaluated")
    if qualification != "observed":
        for field in ("qualified_count", "pending_count", "rejected_count"):
            counts[field] = None
    covered = (value.get("result_coverage_status") == "covered"
               and qualification == "observed" and (counts["qualified_count"] or 0) >= 1)
    return {
        "query_cell_id": cell_id, **counts,
        "count_statuses": {key: "unknown" if number is None else "known" for key, number in counts.items()},
        "coverage_status": _enum(value.get("coverage_status"), _EXECUTION, "blocked_or_unverified"),
        "result_coverage_status": "covered" if covered else "partial",
        "qualification_status": qualification,
        "minimum_qualified_target": 1, "coverage_target_source": "service_minimum_per_cell",
        "user_requested_per_cell_quota": False,
        "actual_cost_usd": None, "actual_cost_status": "unknown",
        "cost_attribution": "unavailable_no_cell_scoped_settled_observation",
    }


def project_query_cell_coverage(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != SCHEMA or not isinstance(value.get("cells"), list):
        return {}
    cells, seen = [], set()
    for raw in value["cells"][:8]:
        cell = _project_cell(raw)
        if cell and cell["query_cell_id"] not in seen:
            cells.append(cell)
            seen.add(cell["query_cell_id"])
    omitted = (_count(value.get("query_cells_omitted")) or 0) + max(0, len(value["cells"]) - len(cells))
    execution = _enum(value.get("execution_status"), {"covered", "partial"}, "partial")
    complete = (bool(cells) and not omitted and execution == "covered"
                and all(cell["coverage_status"] == cell["result_coverage_status"] == "covered" for cell in cells))
    output = {
        "schema": SCHEMA, "status": "covered" if complete else "partial", "execution_status": execution,
        "cells": cells, "query_cells_omitted": omitted,
        "coverage_target_source": "service_minimum_per_cell", "user_requested_per_cell_quota": False,
        "aggregate_cost_allocation_performed": False, "claim_status": "descriptive_only",
        "result_coverage_scope": "at_least_one_server_qualified_creator_per_cell",
    }
    for field in ("query_cells_requested", "query_cells_executed", "run_observations_omitted"):
        number = _count(value.get(field))
        if number is not None:
            output[field] = number
    if value.get("identity_observations_truncated") is True:
        output.update({"identity_observations_truncated": True, "status": "partial"})
    if output.get("run_observations_omitted"):
        output["status"] = "partial"
    return output


def sanitize_with_query_cell_coverage(value: Any, sanitize: Callable[[Any], Any]) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    sanitized = sanitize(source)
    sanitized = sanitized if isinstance(sanitized, dict) else {}
    pairs = [(source, sanitized)]
    if isinstance(source.get("online_qualification"), dict) and isinstance(sanitized.get("online_qualification"), dict):
        pairs.append((source["online_qualification"], sanitized["online_qualification"]))
    for original, target in pairs:
        if "query_cell_coverage" not in original:
            continue
        projected = project_query_cell_coverage(original["query_cell_coverage"])
        if projected:
            target["query_cell_coverage"] = projected
        else:
            target.pop("query_cell_coverage", None)
    return sanitized
