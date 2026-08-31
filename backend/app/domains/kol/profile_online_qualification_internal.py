"""Pure reducers used by the strict online qualification boundary."""
from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Any


def search_objective(
    brief: dict[str, Any],
    cell_inputs: dict[int, list[dict[str, Any]]],
    *,
    text: Callable[[Any], str],
) -> str:
    objective = text(brief.get("objective"))
    if objective:
        return objective
    return next(
        (
            text((entry.get("query_cell") or {}).get("objective"))
            for entries in cell_inputs.values()
            for entry in entries
            if isinstance(entry.get("query_cell"), dict)
            and text((entry.get("query_cell") or {}).get("objective"))
        ),
        "",
    )


def mark_pending_content(adapted: list[dict[str, Any]]) -> set[int]:
    pending_ids: set[int] = set()
    for item in adapted:
        if item.get("prospective_content_evidence_pending") is not True:
            continue
        proof = item.get("qualification_evidence")
        if not isinstance(proof, dict):
            continue
        reasons = list(proof.get("rejection_reasons") or [])
        if "low_relevance" not in reasons:
            continue
        proof["rejection_reasons"] = [
            "pending_content_evidence" if reason == "low_relevance" else reason
            for reason in reasons
        ]
        relevance = proof.get("relevance") if isinstance(proof.get("relevance"), dict) else {}
        proof["relevance"] = {
            **relevance,
            "passed": False,
            "status": "pending_content_evidence",
            "pending": True,
            "reason": "content_locator_present_but_description_caption_transcript_missing",
        }
        pending_ids.add(int(item.get("kol_pool_id") or 0))
    return pending_ids


def rewrite_pending_counts(
    strict_contract: dict[str, Any], pending_content_ids: set[int]
) -> None:
    if not pending_content_ids:
        return
    rejected = strict_contract.get("rejected_by_reason")
    if not isinstance(rejected, dict):
        return
    low_count = max(
        0, int(rejected.get("low_relevance") or 0) - len(pending_content_ids)
    )
    if low_count:
        rejected["low_relevance"] = low_count
    else:
        rejected.pop("low_relevance", None)
    rejected["pending_content_evidence"] = (
        int(rejected.get("pending_content_evidence") or 0) + len(pending_content_ids)
    )


def build_outcomes(
    adapted: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    sources: dict[int, dict[str, Any]],
    *,
    pending_reasons: Collection[str],
    canonical_creator_key: Callable[[dict[str, Any]], str],
    project_online_item: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
    selected_ids = {int(item.get("kol_pool_id") or 0) for item in selected}
    outcomes: list[dict[str, Any]] = []
    for item in adapted:
        synthetic_id = int(item.get("kol_pool_id") or 0)
        proof = (
            item.get("qualification_evidence")
            if isinstance(item.get("qualification_evidence"), dict)
            else {}
        )
        reasons = list(proof.get("rejection_reasons") or [])
        eight_gates_passed = all(
            isinstance(proof.get(field), dict) and proof[field].get("passed") is True
            for field in (
                "account_quality",
                "followers",
                "activity",
                "market",
                "language",
                "profile_type",
                "platform",
                "relevance",
            )
        )
        if synthetic_id in selected_ids:
            status = "selected"
        elif "duplicate_local_identity" in reasons:
            status = "duplicate_local"
        elif "duplicate_canonical_identity" in reasons:
            status = "duplicate_online"
        elif proof.get("passed") is True:
            status = "qualified_overflow"
        elif reasons and set(reasons).issubset(pending_reasons):
            status = "pending"
        else:
            status = "rejected"
        outcomes.append(
            {
                "status": status,
                "eight_gates_passed": eight_gates_passed,
                "reasons": reasons,
                "canonical_key": canonical_creator_key(item),
                "item": (
                    project_online_item(item)
                    if status in {"selected", "qualified_overflow"}
                    else None
                ),
                "source": (
                    sources.get(synthetic_id)
                    if status in {"selected", "qualified_overflow"}
                    else None
                ),
            }
        )
    return outcomes
