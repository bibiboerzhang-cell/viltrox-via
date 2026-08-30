"""Prospective-growth scoring across online candidate query cells."""
from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from app.domains.kol import growth_candidate_scoring, profile_recall_qualification
from app.domains.kol.profile_online_evidence import _project_online_match_evidence


_GROWTH_OUTPUT_FIELDS = (
    "product_use_fit",
    "product_scene_evidence_pass",
    "market_activation",
    "market_activation_pass",
    "market_activation_status",
    "audience_fit",
    "content_execution",
    "evidence_confidence",
    "growth_candidate_score",
    "claim_status",
    "growth_candidate_scoring",
)
_ACTIVATION_CALIBRATION_GATES = (
    "account_quality",
    "followers",
    "activity",
    "market",
    "language",
    "profile_type",
    "platform",
)
_PENDING_ACTIVATION_STATES = frozenset({
    "market_activation_missing",
    "insufficient_sample",
})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _growth_cell_summary(
    *,
    cell: dict[str, Any],
    scored: dict[str, Any],
    match_evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    product_scene_pass = scored.get("product_scene_evidence_pass") is True
    market_activation_pass = scored.get("market_activation_pass") is True
    reasons: list[str] = []
    if not product_scene_pass:
        reasons.append("product_scene_evidence_missing")
    if not market_activation_pass:
        reasons.append(
            _text(scored.get("market_activation_status"))
            or "market_activation_missing"
        )
    return {
        "query_cell_id": _text(cell.get("query_cell_id")) or "legacy_single_query",
        "segment": _text(cell.get("segment")) or None,
        "segment_label": _text(cell.get("segment_label")) or None,
        "primary_query": _text(cell.get("primary_query"))[:500],
        "passed": not reasons,
        "reasons": reasons,
        "product_scene_evidence_pass": product_scene_pass,
        "market_activation_pass": market_activation_pass,
        "market_activation_status": scored.get("market_activation_status"),
        "product_use_fit": scored.get("product_use_fit"),
        "market_activation": scored.get("market_activation"),
        "audience_fit": scored.get("audience_fit"),
        "content_execution": scored.get("content_execution"),
        "evidence_confidence": scored.get("evidence_confidence"),
        "growth_candidate_score": scored.get("growth_candidate_score"),
        "match_evidence": _project_online_match_evidence(match_evidence),
        "claim_status": "descriptive_only",
    }


def activation_calibration_ids(
    adapted: list[dict[str, Any]],
    *,
    rows: dict[int, dict[str, Any]],
    evidence: dict[int, dict[str, Any]],
    policy: dict[str, Any],
    local_canonical_keys: set[str],
    as_of: datetime | None,
    target_count: int,
) -> set[int]:
    """Return identities eligible to influence within-platform percentiles."""

    probes = [dict(item) for item in adapted]
    profile_recall_qualification.qualify_local_candidates(
        buckets={
            "creator": [item for item in probes if item.get("bucket") != "reviewer"],
            "reviewer": [item for item in probes if item.get("bucket") == "reviewer"],
        },
        rows_by_id=rows,
        evidence_by_id=evidence,
        policy=policy,
        creator_quota=target_count,
        reviewer_quota=0,
        target_count=target_count,
        excluded_identity_aliases=local_canonical_keys,
        identity_aliases_fn=profile_recall_qualification.canonical_creator_aliases,
        excluded_identity_reason="duplicate_local_identity",
        as_of=as_of,
    )
    eligible: set[int] = set()
    for item in probes:
        proof = item.get("qualification_evidence")
        if not isinstance(proof, dict):
            continue
        reasons = set(proof.get("rejection_reasons") or [])
        identity_pass = not reasons.intersection({
            "duplicate_local_identity",
            "duplicate_canonical_identity",
        })
        if identity_pass and all(
            isinstance(proof.get(gate), dict) and proof[gate].get("passed") is True
            for gate in _ACTIVATION_CALIBRATION_GATES
        ):
            eligible.add(int(item.get("kol_pool_id") or 0))
    return eligible


def surface_growth_gate_reasons(
    adapted: list[dict[str, Any]],
    strict_contract: dict[str, Any],
) -> None:
    """Replace generic relevance failures with explicit activation evidence states."""

    replacements: dict[str, int] = {}
    for item in adapted:
        if item.get("growth_qualification_pass") is True:
            continue
        summaries = [
            entry
            for entry in item.get("cell_qualification") or []
            if isinstance(entry, dict)
            and entry.get("product_scene_evidence_pass") is True
        ]
        statuses = {
            _text(entry.get("market_activation_status"))
            for entry in summaries
            if _text(entry.get("market_activation_status"))
        }
        reason = next(
            (
                status
                for status in (
                    "insufficient_sample",
                    "market_activation_missing",
                    "below_floor",
                )
                if status in statuses
            ),
            "",
        )
        proof = item.get("qualification_evidence")
        if not reason or not isinstance(proof, dict):
            continue
        reasons = list(proof.get("rejection_reasons") or [])
        if "low_relevance" not in reasons:
            continue
        proof["rejection_reasons"] = [
            reason if value == "low_relevance" else value
            for value in reasons
        ]
        relevance = proof.get("relevance") if isinstance(proof.get("relevance"), dict) else {}
        proof["relevance"] = {
            **relevance,
            "passed": False,
            "status": reason,
            "pending": reason in _PENDING_ACTIVATION_STATES,
            "reason": reason,
        }
        replacements[reason] = replacements.get(reason, 0) + 1

    rejected = strict_contract.get("rejected_by_reason")
    if not replacements or not isinstance(rejected, dict):
        return
    replaced_count = sum(replacements.values())
    low_count = max(0, int(rejected.get("low_relevance") or 0) - replaced_count)
    if low_count:
        rejected["low_relevance"] = low_count
    else:
        rejected.pop("low_relevance", None)
    for reason, count in replacements.items():
        rejected[reason] = int(rejected.get(reason) or 0) + count


def _apply_prospective_growth_cell_scoring(
    adapted: list[dict[str, Any]],
    *,
    cell_inputs_by_id: dict[int, list[dict[str, Any]]],
    search_brief: dict[str, Any],
    activation_calibration_ids: set[int] | None = None,
) -> dict[str, int]:
    """Evaluate candidate×QueryCell pairs and retain each candidate's best passing cell."""

    grouped: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}
    for index, item in enumerate(adapted):
        synthetic_id = int(item.get("kol_pool_id") or 0)
        for entry in cell_inputs_by_id.get(synthetic_id, []):
            cell = entry.get("query_cell") if isinstance(entry.get("query_cell"), dict) else {}
            key = (
                _text(cell.get("query_cell_id")) or "legacy_single_query",
                json.dumps(cell, sort_keys=True, ensure_ascii=True, separators=(",", ":")),
            )
            grouped.setdefault(key, []).append((index, entry))

    choices_by_index: dict[int, list[dict[str, Any]]] = {
        index: [] for index in range(len(adapted))
    }
    for entries in grouped.values():
        cell = entries[0][1].get("query_cell") or {}
        scoring_items: list[dict[str, Any]] = []
        calibration_mask: list[bool] = []
        for index, entry in entries:
            scoring_item = dict(adapted[index])
            scoring_item["match_evidence"] = list(entry.get("match_evidence") or [])
            scoring_items.append(scoring_item)
            calibration_mask.append(
                activation_calibration_ids is None
                or int(scoring_item.get("kol_pool_id") or 0) in activation_calibration_ids
            )
        scored_items = growth_candidate_scoring.score_growth_candidates(
            scoring_items,
            search_brief=search_brief,
            query_cell=cell,
            activation_calibration_mask=calibration_mask,
        )
        for (index, entry), scored in zip(entries, scored_items):
            match_evidence = list(entry.get("match_evidence") or [])
            summary = _growth_cell_summary(
                cell=cell,
                scored=scored,
                match_evidence=match_evidence,
            )
            choices_by_index[index].append({
                "cell": dict(cell),
                "scored": scored,
                "summary": summary,
                "match_evidence": match_evidence,
            })

    qualified_cell_count = 0
    candidate_with_qualified_cell_count = 0
    multi_cell_candidate_count = 0
    for index, original in enumerate(adapted):
        choices = choices_by_index.get(index, [])
        if len(choices) > 1:
            multi_cell_candidate_count += 1
        qualified = [choice for choice in choices if choice["summary"]["passed"] is True]
        qualified_cell_count += len(qualified)
        if qualified:
            candidate_with_qualified_cell_count += 1

        ranked = qualified or choices
        chosen = max(
            ranked,
            key=lambda choice: growth_candidate_scoring.growth_candidate_sort_key(choice["scored"]),
            default=None,
        )
        item = dict(chosen["scored"] if chosen else original)
        item["cell_qualification"] = [choice["summary"] for choice in choices]
        item["growth_qualification_pass"] = bool(qualified)
        item["best_qualified_cell"] = dict(chosen["summary"]) if qualified and chosen else None
        if qualified and chosen:
            best_cell = chosen["cell"]
            item["match_evidence"] = list(chosen["match_evidence"])
            item["query_cell_id"] = _text(best_cell.get("query_cell_id")) or None
            item["query_cell_segment"] = _text(best_cell.get("segment")) or None
            item["query_cell_query"] = _text(best_cell.get("primary_query")) or None
            growth_score = chosen["scored"].get("growth_candidate_score")
            if growth_score is not None:
                item["display_rank_score"] = growth_score
                item["recall_rank_score"] = growth_score
        else:
            item["match_evidence"] = []
            # The best failed attempt remains descriptive diagnostics only.
            if chosen:
                for field in _GROWTH_OUTPUT_FIELDS:
                    item[field] = chosen["scored"].get(field)
        content_status = (
            original.get("content_evidence_status")
            if isinstance(original.get("content_evidence_status"), dict)
            else {}
        )
        any_product_scene_pass = any(
            choice["summary"]["product_scene_evidence_pass"] is True
            for choice in choices
        )
        item["prospective_content_evidence_pending"] = bool(
            not qualified
            and not any_product_scene_pass
            and content_status.get("has_content_locator") is True
            and content_status.get("detail_text_available") is not True
        )
        adapted[index] = item

    return {
        "unique_candidate_count": len(adapted),
        "cell_evaluation_count": sum(len(choices) for choices in choices_by_index.values()),
        "qualified_cell_count": qualified_cell_count,
        "candidate_with_qualified_cell_count": candidate_with_qualified_cell_count,
        "multi_cell_candidate_count": multi_cell_candidate_count,
    }


__all__ = [
    "_apply_prospective_growth_cell_scoring",
    "_growth_cell_summary",
    "activation_calibration_ids",
    "surface_growth_gate_reasons",
]
