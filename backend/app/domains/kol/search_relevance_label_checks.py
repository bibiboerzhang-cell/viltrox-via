"""Per-row rule registry for human label validation.

Candidate export and the validation entrypoint live in
:mod:`search_relevance_eval`; this module owns the row-level rules as a
(check function -> error codes) registry.  Every check receives the same
immutable :class:`LabelRowContext` and returns the error codes it owns, in
the exact historical order — downstream consumers match on these strings, so
codes and their sequence are part of the behavior contract.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping

from app.domains.kol.search_relevance_eval import (
    HUMAN_LABEL_SOURCE,
    LABEL_SCHEMA_VERSION,
    PROHIBITED_LABELERS,
    _safe_mapping,
    _text,
)


@dataclass(frozen=True)
class LabelRowContext:
    """One label row plus everything the row-level rules need to judge it."""

    raw: Mapping[str, Any]
    manifest: Mapping[str, Any]
    query: dict[str, Any]
    candidate: dict[str, Any]
    query_id: str
    candidate_id: str
    review_role: str
    review_slot: str
    slot_key: tuple[str, str, str, str]
    seen_slots: set[tuple[str, str, str, str]] = field(repr=False)
    expected: dict[str, Any] | None
    labeler: str
    reviewed_at: str
    unable_to_judge: bool
    notes: Any


def build_label_row_context(
    raw: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    manifest_index: Mapping[tuple[str, str], dict[str, Any]],
    seen_slots: set[tuple[str, str, str, str]],
) -> LabelRowContext:
    query = _safe_mapping(raw.get("query"))
    candidate = _safe_mapping(raw.get("candidate"))
    query_id = _text(query.get("id"))
    candidate_id = _text(candidate.get("id"))
    review_role = _text(raw.get("review_role")).lower()
    review_slot = _text(raw.get("review_slot"))
    raw_unable = raw.get("unable_to_judge")
    raw_notes = raw.get("notes")
    return LabelRowContext(
        raw=raw,
        manifest=manifest,
        query=query,
        candidate=candidate,
        query_id=query_id,
        candidate_id=candidate_id,
        review_role=review_role,
        review_slot=review_slot,
        slot_key=(query_id, candidate_id, review_role, review_slot),
        seen_slots=seen_slots,
        expected=manifest_index.get((query_id, candidate_id)),
        labeler=_text(raw.get("labeler")),
        reviewed_at=_text(raw.get("reviewed_at")),
        unable_to_judge=raw_unable if isinstance(raw_unable, bool) else False,
        notes="" if raw_notes is None else raw_notes,
    )


def _check_labeler(labeler: str) -> list[str]:
    if not labeler:
        return ["missing_labeler"]
    if not re.fullmatch(r"human:[a-z0-9][a-z0-9._-]{2,63}", labeler, flags=re.IGNORECASE):
        return ["labeler_must_use_human_reviewer_id"]
    if any(
        token in PROHIBITED_LABELERS
        for token in re.findall(r"[a-z0-9]+", labeler.casefold())
    ):
        return ["non_human_labeler_forbidden"]
    return []


def _check_reviewed_at(reviewed_at: str) -> list[str]:
    try:
        reviewed_at_value = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError:
        reviewed_at_value = None
    if reviewed_at_value is None or reviewed_at_value.tzinfo is None:
        return ["reviewed_at_must_be_timezone_iso8601"]
    return []


def _check_label_provenance(context: LabelRowContext) -> list[str]:
    codes: list[str] = []
    if _text(context.raw.get("schema_version")) != LABEL_SCHEMA_VERSION:
        codes.append("invalid_label_schema_version")
    if _text(context.raw.get("label_status")) != "reviewed":
        codes.append("label_status_not_reviewed")
    if _text(context.raw.get("label_source")) != HUMAN_LABEL_SOURCE:
        codes.append("label_source_not_human_review")
    codes.extend(_check_labeler(context.labeler))
    codes.extend(_check_reviewed_at(context.reviewed_at))
    return codes


def _check_review_identity(context: LabelRowContext) -> list[str]:
    codes: list[str] = []
    if not context.query_id:
        codes.append("missing_query_id")
    if not context.candidate_id:
        codes.append("missing_candidate_id")
    if context.review_role not in {"independent", "adjudication"}:
        codes.append("invalid_review_role")
    elif context.review_role == "independent" and context.review_slot not in {"A", "B"}:
        codes.append("independent_review_slot_must_be_a_or_b")
    elif context.review_role == "adjudication" and context.review_slot != "adjudication":
        codes.append("adjudication_review_slot_invalid")
    if context.slot_key in context.seen_slots:
        codes.append("duplicate_candidate_review_slot")
    context.seen_slots.add(context.slot_key)
    return codes


def _check_candidate_against_manifest(context: LabelRowContext) -> list[str]:
    codes: list[str] = []
    try:
        candidate_rank = int(context.candidate.get("rank"))
    except (TypeError, ValueError):
        candidate_rank = 0
    if candidate_rank != int(context.expected.get("rank") or 0):
        codes.append("candidate_rank_mismatch")
    if _text(context.candidate.get("match_tier")) != _text(context.expected.get("match_tier")):
        codes.append("candidate_match_tier_mismatch")
    if _text(context.candidate.get("manifest_fingerprint")) != _text(
        context.manifest.get("manifest_fingerprint")
    ):
        codes.append("manifest_fingerprint_mismatch")
    return codes


def _check_manifest_alignment(context: LabelRowContext) -> list[str]:
    codes: list[str] = []
    if context.expected is None:
        codes.append("candidate_not_in_manifest")
    if _text(context.query.get("suite_version")) != _text(
        context.manifest.get("query_suite_version")
    ):
        codes.append("query_suite_version_mismatch")
    expected_query = next(
        (
            row
            for row in context.manifest.get("queries") or []
            if isinstance(row, Mapping) and _text(row.get("query_id")) == context.query_id
        ),
        {},
    )
    if _text(context.query.get("text")) != _text(expected_query.get("query_text")):
        codes.append("query_text_mismatch")
    if context.expected is not None:
        codes.extend(_check_candidate_against_manifest(context))
    return codes


def _check_judgment_values(context: LabelRowContext) -> list[str]:
    codes: list[str] = []
    if not isinstance(context.raw.get("unable_to_judge"), bool):
        codes.append("unable_to_judge_must_be_boolean")
    relevance = context.raw.get("relevance")
    vertical_fit = context.raw.get("vertical_fit")
    evidence_sufficient = context.raw.get("evidence_sufficient")
    if context.unable_to_judge:
        if any(value is not None for value in (relevance, vertical_fit, evidence_sufficient)):
            codes.append("unable_to_judge_requires_null_judgments")
    else:
        if (
            isinstance(relevance, bool)
            or not isinstance(relevance, int)
            or relevance not in {0, 1, 2, 3}
        ):
            codes.append("relevance_must_be_integer_0_to_3")
        if not isinstance(vertical_fit, bool):
            codes.append("vertical_fit_must_be_boolean")
        if not isinstance(evidence_sufficient, bool):
            codes.append("evidence_sufficient_must_be_boolean")
    return codes


def _check_notes(context: LabelRowContext) -> list[str]:
    codes: list[str] = []
    if not isinstance(context.notes, str):
        codes.append("notes_must_be_string")
    elif len(context.notes) > 4000:
        codes.append("notes_too_long")
    if context.unable_to_judge and not _text(context.notes):
        codes.append("notes_required_when_unable_to_judge")
    return codes


# Registry order == historical issue emission order; do not reorder.
LABEL_ROW_CHECKS: tuple[Callable[[LabelRowContext], list[str]], ...] = (
    _check_label_provenance,
    _check_review_identity,
    _check_manifest_alignment,
    _check_judgment_values,
    _check_notes,
)


def normalized_label_row(context: LabelRowContext) -> dict[str, Any]:
    """Build the normalized record for a row that passed every check."""

    return {
        "query_id": context.query_id,
        "candidate_id": context.candidate_id,
        "rank": int(context.candidate.get("rank")),
        "match_tier": _text(context.candidate.get("match_tier")),
        "labeler": context.labeler,
        "reviewed_at": context.reviewed_at,
        "review_role": context.review_role,
        "review_slot": context.review_slot,
        "unable_to_judge": bool(context.unable_to_judge),
        "relevance": None if context.unable_to_judge else int(context.raw.get("relevance")),
        "vertical_fit": None if context.unable_to_judge else bool(context.raw.get("vertical_fit")),
        "evidence_sufficient": (
            None if context.unable_to_judge else bool(context.raw.get("evidence_sufficient"))
        ),
        "notes": context.notes,
    }
