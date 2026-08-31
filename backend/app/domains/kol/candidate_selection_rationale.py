"""Deterministic, evidence-bounded reasons for prospective KOL selection.

The score answers ordering; this contract answers the operator's different
question: why was this creator found, what makes them worth reviewing, what is
still missing, and what should be fetched next.  It never calls an LLM and
never converts a descriptive proxy into a conversion or outreach claim.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domains.kol.candidate_selection_rationale_helpers import (
    build_candidate_selection_rationale as _build_candidate_selection_rationale,
)


RATIONALE_SCHEMA = "prospective_candidate_rationale_v1"
CLAIM_STATUS = "descriptive_only"


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _score(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return round(parsed, 6) if 0 <= parsed <= 100 else None


def _terms(value: Any, *, limit: int = 6) -> list[str]:
    raw = value if isinstance(value, (list, tuple, set)) else []
    output: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = _text(item)[:120]
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            output.append(text)
        if len(output) >= limit:
            break
    return output


def _card(
    code: str,
    label: str,
    *,
    status: str,
    summary: str,
    score: Any = None,
    evidence_terms: Any = None,
    evidence_fields: Any = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "status": status,
        "summary": _text(summary)[:360],
        "score": _score(score),
        "evidence_terms": _terms(evidence_terms),
        "evidence_fields": _terms(evidence_fields, limit=8),
    }


def build_candidate_selection_rationale(
    *,
    evidence_contract: Mapping[str, Any],
    activation_gate: Mapping[str, Any],
    audience_contract: Mapping[str, Any],
    content_contract: Mapping[str, Any],
    product_use_fit: Any,
    market_activation: Any,
    audience_fit: Any,
    content_execution: Any,
    evidence_confidence: Any,
) -> dict[str, Any]:
    """Return a stable reason/missing-action bundle from already-audited facts."""
    return _build_candidate_selection_rationale(
        evidence_contract=evidence_contract,
        activation_gate=activation_gate,
        audience_contract=audience_contract,
        content_contract=content_contract,
        product_use_fit=product_use_fit,
        market_activation=market_activation,
        audience_fit=audience_fit,
        content_execution=content_execution,
        evidence_confidence=evidence_confidence,
        text=_text,
        terms=_terms,
        score=_score,
        card=_card,
        schema=RATIONALE_SCHEMA,
        claim_status=CLAIM_STATUS,
    )


__all__ = [
    "CLAIM_STATUS",
    "RATIONALE_SCHEMA",
    "build_candidate_selection_rationale",
]
