"""Shared, provider-free operator constraints for preview and queued search.

The server rebuilds this projection from request fields and its own planner.
An incoming spec/hash is never authority. The hash covers effective constraints,
not model prose, timestamps, or whether an operator used a chip or natural text.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from app.domains.kol import search_auto_relax, search_relaxation
from app.domains.kol.profile_recall_search_spec import operator_filter_spec
from app.domains.kol.search_geo_intent import resolve_geo_intent

SCHEMA = "operator_search_spec_v1"


def normalize_operator_body(body: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(body)
    filters = result.get("filters")
    if filters is not None and not isinstance(filters, dict):
        raise ValueError("filters must be an object")
    filters = dict(filters or {})
    for key, aliases in {
        "languages": ("content_languages",), "profile_types": ("kol_types",),
        "followers_min": ("follower_min",), "followers_max": ("follower_max",),
    }.items():
        if key not in filters:
            for source in (filters, result):
                found = next((name for name in (key, *aliases) if name in source and source[name] is not None), None)
                if found is not None:
                    filters[key] = deepcopy(source[found])
                    break
        for alias in aliases:
            filters.pop(alias, None)
            result.pop(alias, None)
    result["filters"] = filters
    return result


def build_operator_search_spec(
    *, plan: dict[str, Any], body: dict[str, Any],
    recall_filters: dict[str, Any], market: Any = "", platforms: Any = None,
) -> dict[str, Any]:
    """Preserve explicit filters, with model additions opt-in and auditable."""
    body = normalize_operator_body(body)
    geo = resolve_geo_intent(body.get("query_text") or body.get("input") or body.get("query") or plan.get("original_query") or (plan.get("filter_proposal") or {}).get("original_query") or "", body)
    if geo["ambiguity_reasons"]:
        raise ValueError("ambiguous_geo_constraints")
    explicit, origins = search_auto_relax.assemble_recall_filters(body)
    # Existing preview auto-relax output remains authoritative only for inferred
    # additions. It cannot remove or replace literal operator fields.
    filters = {**deepcopy(recall_filters), **deepcopy(explicit)}
    for canonical, alias in (("followers_min", "follower_min"), ("followers_max", "follower_max")):
        if canonical not in filters and alias in filters:
            filters[canonical] = filters[alias]
        filters.pop(alias, None)
    filters, origins = search_auto_relax.merge_plan_filters(filters, origins, plan)
    proposal = search_auto_relax.filter_proposal(plan)
    for field in proposal.get("relaxable_fields") or []:
        key = search_auto_relax.FACET_TO_FILTER_KEY.get(field)
        if key and key in filters and key not in explicit:
            origins[key] = search_auto_relax.ORIGIN_MODEL
    filters, origins, added, dropped = search_auto_relax.split_additions(
        filters, origins, plan,
        keep=body.get(search_auto_relax.BODY_AUTO_FILTERS_KEY) is True,
        dropped_keys=search_auto_relax.dropped_auto_filter_keys(body),
    )
    follower = plan.get("follower_filter") if isinstance(plan.get("follower_filter"), dict) else {}
    if follower.get("source") in {"operator_text", "operator_filter"}:
        for key in ("followers_min", "followers_max"):
            if key not in explicit and follower.get(key) is not None:
                filters[key] = follower[key]
                origins[key] = search_auto_relax.ORIGIN_OPERATOR
    languages = filters.get("languages")
    if languages is None:
        languages = body.get("languages") or body.get("content_languages")
    profile_types = filters.get("profile_types")
    if profile_types is None:
        profile_types = body.get("profile_types") or body.get("kol_types")
    policy_inputs = {
        "market": market or "", "platforms": deepcopy(platforms),
        "languages": deepcopy(languages), "profile_types": deepcopy(profile_types),
        "gate_mode": search_relaxation.resolve_mode(body),
        "geo_constraints": {
            "creator_countries": list(geo["creator_countries"]),
            "audience_markets": list(geo["audience_markets"]),
            "creator_mode": geo["creator_mode"],
            "audience_mode": geo["audience_mode"],
        },
    }
    policy_inputs["hide_team_favorites"] = search_relaxation.resolve_hide_team_favorites(body, mode=policy_inputs["gate_mode"])
    normalized = operator_filter_spec(languages=languages, profile_types=profile_types)
    for value in normalized.values():
        value["values"] = sorted(value["values"])
        value["invalid"] = sorted(value["invalid"])
    constraints = {
        "market": str(market or "").lower(),
        "platforms": sorted({str(p).strip().lower() for p in (
            platforms if isinstance(platforms, (list, tuple, set)) else [platforms]
        ) if p}),
        "operator_filters": normalized,
        "filters": deepcopy(filters),
        "gate_mode": policy_inputs["gate_mode"],
        "hide_team_favorites": policy_inputs["hide_team_favorites"],
        "exclude_chinese": bool(body.get("exclude_chinese", True)),
        "geo_constraints": deepcopy(policy_inputs["geo_constraints"]),
    }
    # These fields have one normalized representation regardless of input UI.
    for key in ("languages", "profile_types"):
        constraints["filters"].pop(key, None)
    encoded = json.dumps(constraints, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return {
        "schema": SCHEMA, "version": 1,
        "constraint_hash": hashlib.sha256(encoded.encode()).hexdigest(),
        "filters": deepcopy(filters), "origins": dict(origins),
        "policy_inputs": policy_inputs, "constraints": constraints,
        "geo_intent": geo,
        "model_additions": added, "model_additions_dropped": dropped,
    }


def online_policy_inputs(payload: dict[str, Any]) -> dict[str, Any]:
    """Read the worker-rebuilt policy, falling back for legacy internal callers."""
    spec = payload.get("operator_search_spec")
    if isinstance(spec, dict) and spec.get("schema") == SCHEMA:
        value = spec.get("policy_inputs")
        if isinstance(value, dict):
            return deepcopy(value)
    filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
    return {
        "languages": filters.get("languages", payload.get("languages") or payload.get("content_languages")),
        "profile_types": filters.get("profile_types", payload.get("profile_types") or payload.get("kol_types")),
    }


def requires_qualified_online(payload: dict[str, Any], *, followers_min: Any = None, followers_max: Any = None) -> bool:
    """Legacy single-batch searches must not ignore rebuilt operator gates."""
    spec = payload.get("operator_search_spec")
    if not isinstance(spec, dict) or spec.get("schema") != SCHEMA:
        return False
    inputs = online_policy_inputs(payload)
    filters = operator_filter_spec(languages=inputs.get("languages"), profile_types=inputs.get("profile_types"))
    geo = inputs.get("geo_constraints") or {}
    return bool(any(value["requested"] for value in filters.values())
                or geo.get("creator_countries") or geo.get("audience_markets")
                or followers_min is not None or followers_max is not None)
