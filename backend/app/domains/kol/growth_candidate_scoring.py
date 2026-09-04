"""Pure, evidence-bounded scoring for prospective-growth KOL candidates.

This module intentionally does not decide search eligibility, call providers,
or persist state.  It ranks already-discovered candidates for the
``prospective_growth`` objective: people who have public evidence that they
could use the product in the requested scene and whose observed content signals
suggest market-activation potential.

Important boundaries:

* Viltrox/brand history has zero weight.  It is recorded only as ignored
  provenance and can never prove product use or improve a score.
* Real partnership/conversion outcomes are reported separately and are never
  mixed into the descriptive proxy score.
* Missing values are omitted and observed weights are renormalised.  Missing
  evidence lowers ``evidence_confidence`` instead of becoming a fake zero.
* Followers are not an eligibility gate.  Their within-platform percentile is
  only a 5% reach signal inside ``market_activation``.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from app.domains.kol import targeted_search_contract
from app.domains.kol.candidate_selection_rationale import (
    build_candidate_selection_rationale,
)
from app.domains.kol.growth_candidate_evidence import (
    _AUDIENCE_GROUP_NAMES,
    _GENERIC_NON_PROOF_TERMS,
    _KNOWN_PRODUCT_PHRASES,
    _PRODUCT_ALIAS_GROUPS,
    _PRODUCT_GROUP_NAMES,
    _ROLE_GROUP_NAMES,
    _SCENE_ALIAS_GROUPS,
    _SCENE_GROUP_NAMES,
    product_scene_evidence as _product_scene_evidence,
)
from app.domains.kol.growth_candidate_metrics import (
    ACTIVATION_SIGNAL_WEIGHTS,
    _dedupe_terms,
    _iter_values,
    _normal_text,
    _number,
    audience_fit as _audience_fit,
    content_execution as _content_execution,
    market_activation as _market_activation,
    market_activation_gate as _market_activation_gate,
    outcome_observation as _outcome_observation,
    platform_percentiles as _platform_percentiles,
    present_fields as _present_fields,
    sample_depth as _sample_depth,
    weighted_observed_score as _weighted_observed_score,
)


SCORING_VERSION = "prospective_growth_candidate_v2"
CLAIM_STATUS = "descriptive_only"

GROWTH_DIMENSION_WEIGHTS: dict[str, float] = {
    "product_use_fit": 0.40,
    "market_activation": 0.30,
    "audience_fit": 0.15,
    "content_execution": 0.15,
}
_BRAND_HISTORY_FIELDS = (
    "viltrox_mentions",
    "viltrox_mention_count",
    "viltrox_fit_score",
    "brand_affinity",
    "brand_affinity_score",
    "brand_collaborations",
    "brand_history",
    "existing_brand_evidence",
    "existing_viltrox_user",
)


def _infer_locked_term_kind(value: Any) -> str:
    term = _normal_text(value)
    if not term or term in _GENERIC_NON_PROOF_TERMS:
        return ""
    if any(term in {_normal_text(alias) for alias in group} for group in _PRODUCT_ALIAS_GROUPS):
        return "product"
    if any(term in {_normal_text(alias) for alias in group} for group in _SCENE_ALIAS_GROUPS):
        return "scene"
    if re.search(r"\b(?:\d{1,3}mm|f\d+(?: \d+)?|ttl|hss|speedlight|strobe|flash|monitor)\b", term):
        return "product"
    return ""


def _locked_term_groups(
    search_brief: Mapping[str, Any],
    query_cell: Mapping[str, Any],
) -> dict[str, list[str]]:
    controlled = targeted_search_contract.rebuild_locked_term_groups_for_cell(query_cell)
    if controlled:
        grouped = {"product": [], "scene": [], "role": [], "audience": []}
        for group in controlled.get("groups") or []:
            if not isinstance(group, Mapping):
                continue
            kind = _normal_text(group.get("kind"))
            canonical = _normal_text(group.get("canonical_term"))
            if kind in {"product", "scene", "role"} and canonical:
                grouped[kind].append(canonical)
        return {key: _dedupe_terms(values) for key, values in grouped.items()}

    grouped: dict[str, list[Any]] = {
        "product": [], "scene": [], "role": [], "audience": [],
    }

    def collect(payload: Any) -> None:
        if not isinstance(payload, Mapping):
            return
        for key in _PRODUCT_GROUP_NAMES:
            if key in payload:
                grouped["product"].extend(_iter_values(payload.get(key)))
        for key in _SCENE_GROUP_NAMES:
            if key in payload:
                grouped["scene"].extend(_iter_values(payload.get(key)))
        for key in _AUDIENCE_GROUP_NAMES:
            if key in payload:
                grouped["audience"].extend(_iter_values(payload.get(key)))
        for key in _ROLE_GROUP_NAMES:
            if key in payload:
                grouped["role"].extend(_iter_values(payload.get(key)))
        for nested_key in ("product", "requirements", "hard_constraints"):
            nested = payload.get(nested_key)
            if isinstance(nested, Mapping):
                collect(nested)

    collect(search_brief)
    collect(query_cell)

    # Current QueryCell contracts expose the segment separately.  It remains a
    # scene proof requirement even before callers migrate it under locked_terms.
    for key in ("segment", "segment_label"):
        grouped["scene"].extend(_iter_values(query_cell.get(key)))

    # Transitional capability extraction is deliberately allowlisted; the
    # whole provider query is never treated as proof requirements.
    query_blob = _normal_text(query_cell.get("primary_query"))
    for phrase in _KNOWN_PRODUCT_PHRASES:
        if _normal_text(phrase) in query_blob:
            grouped["product"].append(phrase)

    output = {key: _dedupe_terms(values) for key, values in grouped.items()}
    # Transitional fields remain supported only when they resolve through the
    # same static registry.  Arbitrary model/client terms never become hard
    # evidence requirements.
    for kind in ("product", "scene", "role"):
        output[kind] = _dedupe_terms(
            canonical
            for value in output[kind]
            if (canonical := targeted_search_contract.canonical_controlled_term(kind, value))
        )
    return output


def score_growth_candidates(
    items: Sequence[Mapping[str, Any]],
    search_brief: Mapping[str, Any] | None = None,
    query_cell: Mapping[str, Any] | None = None,
    activation_calibration_mask: Sequence[bool] | None = None,
) -> list[dict[str, Any]]:
    """Return scored copies of ``items`` without changing input order or data.

    ``search_brief`` and ``query_cell`` accept plain dictionaries so the module
    can be adopted before the queue/session schemas are migrated.  Product-use
    fit requires one specific product/capability proof and one requested-scene
    proof in ``match_evidence``.  Generic brand/ecosystem/category terms alone
    never satisfy either leg.
    """

    brief = dict(search_brief or {})
    cell = dict(query_cell or {})
    candidates = [dict(item) for item in items]
    locked = _locked_term_groups(brief, cell)
    locked_spec = targeted_search_contract.rebuild_locked_term_groups_for_cell(cell)
    product_evidence_required = cell.get("product_evidence_required") is not False
    evidence_by_index = [
        _product_scene_evidence(
            item,
            locked,
            locked_spec=locked_spec,
            product_evidence_required=product_evidence_required,
            role_match_mode=str(cell.get("role_match_mode") or "any"),
        )
        for item in candidates
    ]
    eligible_indices = {
        index
        for index, (_score, contract, _strength) in enumerate(evidence_by_index)
        if contract["passed"]
        and (
            activation_calibration_mask is None
            or (
                index < len(activation_calibration_mask)
                and activation_calibration_mask[index] is True
            )
        )
    }
    percentiles_by_index = _platform_percentiles(
        candidates,
        eligible_indices=eligible_indices,
    )
    output: list[dict[str, Any]] = []

    for index, item in enumerate(candidates):
        product_use_fit, evidence_contract, proof_strength = evidence_by_index[index]
        market_activation, activation_contract = _market_activation(percentiles_by_index[index])
        activation_gate = _market_activation_gate(item)
        representative_sample = (
            item.get("activation_metrics_scope") == "exact_query_hit_45d"
            and item.get("avg_views") is None
        )
        if representative_sample and "avg_views" in activation_contract["values"]:
            activation_contract["values"]["representative_video_views"] = (
                activation_contract["values"].pop("avg_views")
            )
            activation_contract["metric_alias_policy"] = (
                "internal_calibration_only_never_project_as_avg_views"
            )
        activation_contract.update({
            "observed_source_fields": _present_fields(
                item,
                (
                    "avg_views",
                    "representative_video_views",
                    "representative_video_likes",
                    "representative_video_comments",
                    "avg_likes",
                    "avg_comments",
                    "engagement_rate",
                    "views_per_follower",
                    "comments_per_follower",
                ),
            ),
            "avg_views_source": item.get("avg_views_source"),
            "avg_views_scope": item.get("avg_views_scope"),
            "channel_lifetime_proxy": item.get("avg_views_scope") == "channel_lifetime_proxy",
            "activation_metrics_source": item.get("activation_metrics_source"),
            "activation_metrics_scope": item.get("activation_metrics_scope"),
            "activation_sample_count": item.get("activation_sample_count"),
            "activation_sample_confidence": (
                "low"
                if _number(item.get("activation_sample_count"), minimum=0.0) == 1
                else None
            ),
            "strict_gate": activation_gate,
            "market_activation_pass": activation_gate["passed"],
            "market_activation_status": activation_gate["status"],
            "calibration_eligible": index in eligible_indices,
            "calibration_population_policy": (
                "same_platform_after_hard_facets_and_product_scene_evidence"
            ),
            "claim_status": CLAIM_STATUS,
        })
        audience_fit, audience_contract = _audience_fit(item, brief, cell)
        content_execution, content_contract = _content_execution(item)

        dimension_components = (
            *(
                (("product_use_fit", product_use_fit, GROWTH_DIMENSION_WEIGHTS["product_use_fit"]),)
                if product_evidence_required
                else ()
            ),
            ("market_activation", market_activation, GROWTH_DIMENSION_WEIGHTS["market_activation"]),
            ("audience_fit", audience_fit, GROWTH_DIMENSION_WEIGHTS["audience_fit"]),
            ("content_execution", content_execution, GROWTH_DIMENSION_WEIGHTS["content_execution"]),
        )
        growth_score, missing_dimensions, _binary_coverage = _weighted_observed_score(dimension_components)
        if not evidence_contract["passed"]:
            growth_score = None

        active_weight = 1.0 if product_evidence_required else 1.0 - GROWTH_DIMENSION_WEIGHTS["product_use_fit"]
        dimension_coverage = (
            (
                GROWTH_DIMENSION_WEIGHTS["product_use_fit"]
                * (1.0 if product_use_fit is not None else 0.0)
                if product_evidence_required
                else 0.0
            )
            + GROWTH_DIMENSION_WEIGHTS["market_activation"] * activation_contract["signal_coverage"]
            + GROWTH_DIMENSION_WEIGHTS["audience_fit"] * audience_contract["signal_coverage"]
            + GROWTH_DIMENSION_WEIGHTS["content_execution"] * content_contract["signal_coverage"]
        ) / active_weight
        sample_depth = _sample_depth(item, evidence_contract)
        evidence_confidence = round(
            100.0 * min(1.0, 0.50 * dimension_coverage + 0.30 * proof_strength + 0.20 * sample_depth),
            6,
        )
        confidence_level = "high" if evidence_confidence >= 75 else "medium" if evidence_confidence >= 45 else "low"
        if growth_score is None:
            decision_mode = (
                "not_rankable_product_scene_evidence"
                if product_evidence_required
                else "not_rankable_people_scene_evidence"
            )
        elif confidence_level == "low":
            decision_mode = "human_review_required"
        else:
            decision_mode = "human_decision_support"
        selection_rationale = build_candidate_selection_rationale(
            evidence_contract=evidence_contract,
            activation_gate=activation_gate,
            audience_contract=audience_contract,
            content_contract=content_contract,
            product_use_fit=product_use_fit,
            market_activation=market_activation,
            audience_fit=audience_fit,
            content_execution=content_execution,
            evidence_confidence=evidence_confidence,
        )

        scored = dict(item)
        scored.update(
            {
                "product_use_fit": product_use_fit,
                "product_scene_evidence_pass": bool(evidence_contract["passed"]),
                "people_scene_evidence_pass": bool(evidence_contract["passed"]),
                "market_activation": market_activation,
                "market_activation_pass": activation_gate["passed"],
                "market_activation_status": activation_gate["status"],
                "audience_fit": audience_fit,
                "content_execution": content_execution,
                "evidence_confidence": evidence_confidence,
                "growth_candidate_score": growth_score,
                "selection_rationale": selection_rationale,
                "claim_status": CLAIM_STATUS,
                "growth_candidate_scoring": {
                    "version": SCORING_VERSION,
                    "objective": "prospective_growth",
                    "dimension_weights": dict(GROWTH_DIMENSION_WEIGHTS),
                    "missing_dimensions": missing_dimensions,
                    "missing_value_policy": "omit_and_renormalize_never_zero_impute",
                    "evidence_contract": evidence_contract,
                    "platform_activation": activation_contract,
                    "audience": audience_contract,
                    "content_execution": content_contract,
                    "confidence": {
                        "score": evidence_confidence,
                        "level": confidence_level,
                        "decision_mode": decision_mode,
                        "dimension_coverage": round(dimension_coverage, 6),
                        "sample_depth": sample_depth,
                        "note": "证据覆盖置信度，不是预测准确率或业务结果。",
                    },
                    "followers_policy": "low_weight_reach_signal_never_eligibility_gate",
                    "brand_history_weight": 0.0,
                    "ignored_brand_history_fields": _present_fields(item, _BRAND_HISTORY_FIELDS),
                    "real_outcome": _outcome_observation(item),
                    "selection_rationale": selection_rationale,
                    "claim_status": CLAIM_STATUS,
                },
            }
        )
        output.append(scored)
    return output


def growth_candidate_sort_key(item: Mapping[str, Any]) -> tuple[float, float]:
    """Sort helper: descriptive score first, then its evidence confidence."""

    score = _number(item.get("growth_candidate_score"))
    confidence = _number(item.get("evidence_confidence"))
    return (score if score is not None else -1.0, confidence if confidence is not None else -1.0)


__all__ = [
    "SCORING_VERSION",
    "CLAIM_STATUS",
    "GROWTH_DIMENSION_WEIGHTS",
    "ACTIVATION_SIGNAL_WEIGHTS",
    "score_growth_candidates",
    "growth_candidate_sort_key",
]
