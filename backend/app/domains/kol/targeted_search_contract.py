"""Pure contract helpers for targeted, first-round KOL discovery.

The default objective is prospective growth: find creators who are likely to
use the product and can activate its target market.  Brand/model mentions are
therefore neither an eligibility requirement nor a ranking signal.  The
legacy ``existing_evidence`` objective remains available for workflows that
explicitly need creators already talking about Viltrox.

This module is deliberately IO-free.  It produces auditable ``QueryCell``
dictionaries for the execution pipeline and parses operator-owned follower
ranges without silently relaxing them.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from app.domains.kol.targeted_search_cell import (
    build_query_cell as _cell,
    first_round_raw_limit as _first_round_raw_limit,
    prospective_primary_query as _prospective_primary_query,
)
from app.domains.kol.targeted_search_capability import (
    APERTURE_RE as _APERTURE_RE,
    FOCAL_LENGTH_RE as _FOCAL_LENGTH_RE,
    fast_lens_aperture as _fast_lens_aperture,
    lens_focal_span as _lens_focal_span,
    lens_focals as _lens_focals,
    prospective_lens_capability as _prospective_lens_capability,
)
from app.domains.kol.search_intent_text import affirmative_search_text
from app.domains.kol.targeted_search_filters import (
    DEFAULT_OBJECTIVE,
    EXISTING_EVIDENCE,
    PROSPECTIVE_GROWTH,
    SUPPORTED_OBJECTIVES,
    normalize_objective,
    operator_platforms as _operator_platforms,
    parse_follower_range,
)
from app.domains.kol.targeted_search_persona import (
    build_target_persona_text,
    has_creator_role as _has_creator_role,
)
from app.domains.kol.targeted_search_segments import (
    extract_explicit_segments as _extract_explicit_segments,
)
from app.domains.kol.targeted_search_terms import (
    LOCKED_TERM_GROUPS_SCHEMA,
    LOCKED_TERM_GROUPS_SOURCE,
    LOCKED_TERM_GROUPS_VERSION,
    build_locked_term_groups,
    canonical_controlled_term,
    controlled_aliases_for,
    controlled_capability_use_terms_for,
    project_locked_term_groups,
    rebuild_locked_term_groups_for_cell,
)


SEARCH_SPEC_VERSION = "targeted_search_v2"

def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _dedupe(values: Iterable[Any]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _text(value)
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            output.append(item)
    return output


def extract_explicit_segments(query: Any = "", body: Any = None) -> list[dict[str, Any]]:
    """Extract operator-owned industries/use-cases and keep each one independent."""

    return _extract_explicit_segments(query, body)


def build_target_persona(
    *,
    query: Any,
    body: Any,
    product: Any,
    product_focus: Iterable[Any],
) -> str:
    """Describe who to find; product identity is supporting evidence only."""

    segments = extract_explicit_segments(query, body)
    product_present = isinstance(product, dict) and bool(product)
    capability = (
        _product_capability(product, product_focus, operator_segments=segments)
        if product_present else ""
    )
    return build_target_persona_text(
        segments=segments,
        product_focus=product_focus,
        product_present=product_present,
        capability=capability,
        affirmative_query=affirmative_search_text(query),
    )


def _product_capability(
    product: Any,
    focus_terms: Iterable[Any],
    *,
    objective: str = PROSPECTIVE_GROWTH,
    operator_segments: Iterable[Any] = (),
) -> str:
    item = product if isinstance(product, dict) else {}
    focus_values = list(focus_terms or [])
    product_blob = " ".join(
        _text(item.get(key)).lower()
        for key in (
            "category_main", "category_detail", "series", "model_name",
            "marketing_name", "description", "specs_line", "resolved_alias",
            "resolved_canonical", "resolved_model_identity",
        )
    )
    focus_blob = " ".join(_text(term).lower() for term in focus_values)
    blob = _text(f"{product_blob} {focus_blob}")
    product_is_lens = (
        "lens" in product_blob
        or "镜头" in product_blob
        or _FOCAL_LENGTH_RE.search(product_blob) is not None
    )
    product_is_flash = any(term in product_blob for term in ("flash", "strobe", "闪光"))
    product_is_monitor = any(term in product_blob for term in ("monitor", "监视器"))
    product_is_studio_light = any(
        term in product_blob
        for term in (
            "studio light", "video light", "continuous light", "cob light",
            "摄影灯", "影视灯", "影棚灯", "补光灯",
        )
    )
    product_is_teleconverter = any(
        term in product_blob for term in ("teleconverter", "teleplus", "增距镜")
    )
    # A resolved catalog record is authoritative for capability. Planner focus
    # prose must never turn a real lens into a monitor/flash or vice versa.
    if item:
        if product_is_studio_light:
            return (
                "300w studio lighting"
                if re.search(r"(?<![a-z0-9])300\s*w(?:atts?)?(?![a-z0-9])", product_blob)
                else "studio lighting"
            )
        if product_is_flash:
            return "on-camera flash"
        if product_is_monitor:
            return "camera monitor"
        if product_is_teleconverter:
            return "teleconverter"
    if product_is_lens or "lens" in focus_blob or "镜头" in focus_blob:
        if objective == PROSPECTIVE_GROWTH:
            # Resolved product facts outrank planner-authored focus prose.  The
            # latter is only a fallback when no product record is available.
            capability_source = product_blob if product_is_lens else focus_blob
            return _prospective_lens_capability(
                capability_source,
                operator_segments=operator_segments,
            )
        if "anamorphic" in blob or "cine" in blob:
            return "cinema lens"
        focal = _FOCAL_LENGTH_RE.search(product_blob)
        return f"{focal.group(0).replace(' ', '')} lens" if focal else "camera lens"
    if not item and ("flash" in focus_blob or "strobe" in focus_blob or "闪光" in focus_blob):
        return "on-camera flash"
    if not item and ("monitor" in focus_blob or "监视器" in focus_blob):
        return "camera monitor"
    if objective == EXISTING_EVIDENCE:
        for term in focus_values:
            candidate = _text(term).lower()
            if candidate and "viltrox" not in candidate and len(candidate.split()) <= 3:
                return candidate
    return "creator gear"


def _operator_product_capability(
    query: Any,
    *,
    operator_segments: Iterable[Any] = (),
) -> str:
    """Return only a capability explicitly stated by the operator."""

    value = affirmative_search_text(query).lower()
    if not value:
        return ""
    wattage = re.search(
        r"(?<![a-z0-9])(?P<watts>\d{2,4})\s*w(?:atts?)?(?![a-z0-9])",
        value,
    )
    wattage_light = bool(
        wattage
        and any(term in value for term in ("light", "lighting", "flash", "strobe", "灯"))
    )
    if wattage_light or any(
        term in value
        for term in (
            "studio light", "studio lighting", "video light", "continuous light",
            "portable lighting", "cob light", "摄影灯", "影视灯", "影棚灯", "补光灯",
            "离机闪光", "离机布光", "off-camera flash", "off camera flash",
            "off-camera lighting", "off camera lighting",
        )
    ):
        return (
            "300w studio lighting"
            if wattage and wattage.group("watts") == "300"
            else "studio lighting"
        )
    if any(
        term in value
        for term in ("camera monitor", "field monitor", "external monitor", "监视器", "监看器")
    ):
        return "camera monitor"
    if any(term in value for term in ("teleconverter", "teleplus", "增距镜", "增倍镜")):
        return "teleconverter"
    if any(term in value for term in ("on-camera flash", "on camera flash", "speedlight", "speedlite", "机顶闪光灯", "闪光灯")):
        return "on-camera flash"
    visual_role_context = _has_creator_role(value) or any(
        term in value for term in ("review", "reviewer", "评测", "测评", "photography", "摄影")
    )
    if visual_role_context and (
        re.search(r"(?<![a-z0-9])(?:flash|strobe)(?![a-z0-9])", value)
        or "闪光" in value
    ):
        return "on-camera flash"
    if re.search(r"(?<![a-z0-9])(?:lens|lenses)(?![a-z0-9])", value) or "镜头" in value:
        return _prospective_lens_capability(value, operator_segments=operator_segments)
    return ""


def _product_evidence_context(
    *,
    query: Any,
    product: Any,
    product_focus: Iterable[Any],
    objective: str,
    operator_segments: Iterable[Any],
) -> tuple[str, str]:
    if isinstance(product, dict) and product:
        return (
            _product_capability(
                product,
                product_focus,
                objective=objective,
                operator_segments=operator_segments,
            ),
            "resolved_product",
        )
    operator_capability = _operator_product_capability(
        query,
        operator_segments=operator_segments,
    )
    if operator_capability:
        return operator_capability, "operator_capability"
    return "", "none"


def _without_brand_model(value: Any, product: Any, *, drop_focal: bool = False) -> str:
    text = _text(value)
    item = product if isinstance(product, dict) else {}
    drop: set[str] = {"viltrox"}
    identity = " ".join(
        _text(item.get(key)).lower() for key in ("sku", "model_name", "marketing_name", "series")
    )
    for family in ("vintage", "pro", "evo", "epic", "lab", "air"):
        if re.search(rf"(?<![a-z0-9]){family}(?![a-z0-9])", identity):
            drop.add(family)
    for key in ("sku", "model_name", "marketing_name"):
        for token in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", _text(item.get(key)).lower()):
            if any(char.isdigit() for char in token) or token == "viltrox":
                drop.add(token)
    # A focal length is product identity only after the catalog resolver has
    # actually produced a product/family.  With no product, phrases such as
    # ``50mm equivalent`` are operator-owned scene/format context and must not
    # disappear from the fallback query.
    if drop_focal and item:
        text = _FOCAL_LENGTH_RE.sub(" ", text)
        text = _APERTURE_RE.sub(" ", text)
    kept = [token for token in text.split() if token.lower().strip(",") not in drop]
    return _text(" ".join(kept))


def build_query_cells(
    *,
    query: Any,
    body: Any,
    product: Any,
    product_focus: Iterable[Any],
    platforms: Iterable[Any],
    legacy_queries: Iterable[Any] = (),
) -> list[dict[str, Any]]:
    """Build independent first-round cells; fallbacks never run in round one."""

    objective = normalize_objective(body)
    follower_filter = parse_follower_range(query, body)
    explicit = extract_explicit_segments(query, body)
    focus_values = list(product_focus or [])
    capability, product_evidence_basis = _product_evidence_context(
        query=query,
        product=product,
        product_focus=focus_values,
        objective=objective,
        operator_segments=explicit,
    )
    product_evidence_required = product_evidence_basis != "none"
    # Empty is an intentional "no operator platform restriction" value.  The
    # provider resolves it to all supported discovery legs; silently choosing
    # YouTube here would turn an optional facet into an unrequested hard gate.
    platform_values = _dedupe(_text(value).lower() for value in platforms)
    legacy = _dedupe(legacy_queries)

    # The explicit legacy mode keeps the already-normalized anchored queries.
    # This branch is intentionally bypassed when the operator named segments:
    # those still need one independent anchored cell per segment.
    if objective == EXISTING_EVIDENCE and not explicit and legacy:
        selected_legacy = legacy[:4]
        raw_limit = _first_round_raw_limit(body, cell_count=len(selected_legacy))
        return [
            _cell(
                index=index,
                key=f"existing_{index}",
                label="existing product evidence",
                source="legacy_existing_evidence",
                locked=True,
                primary=query_value,
                fallbacks=(),
                objective=objective,
                platforms=platform_values,
                raw_limit=raw_limit,
                follower_filter=follower_filter,
                capability=capability,
                product_evidence_required=product_evidence_required,
                product_evidence_basis=product_evidence_basis,
            )
            for index, query_value in enumerate(selected_legacy, start=1)
        ]

    seeds = explicit
    if not seeds:
        inferred = [
            _without_brand_model(
                value,
                product,
                drop_focal=objective == PROSPECTIVE_GROWTH,
            )
            for value in focus_values
        ]
        inferred = [value for value in _dedupe(inferred) if value]
        seeds = [
            {
                "key": f"persona_{index}",
                "label": value,
                "query_term": value,
                "source": "planner_inferred",
                "locked": False,
            }
            for index, value in enumerate(inferred[:3], start=1)
        ]
    if not seeds:
        fallback = _without_brand_model(
            next(iter(legacy_queries), ""),
            product,
            drop_focal=objective == PROSPECTIVE_GROWTH,
        ) or "content creator"
        seeds = [{
            "key": "general_creator",
            "label": fallback,
            "query_term": fallback,
            "source": "rule_fallback",
            "locked": False,
        }]

    raw_limit = _first_round_raw_limit(body, cell_count=len(seeds))

    anchor = ""
    if objective == EXISTING_EVIDENCE and isinstance(product, dict):
        anchor = _text(product.get("marketing_name") or product.get("model_name") or product.get("sku"))

    cells: list[dict[str, Any]] = []
    for index, segment in enumerate(seeds, start=1):
        segment_term = _without_brand_model(
            segment.get("query_term"),
            product,
            drop_focal=objective == PROSPECTIVE_GROWTH,
        ) or "content creator"
        if segment.get("key") == "review" and product_evidence_required:
            segment_term = {
                "300w studio lighting": "300W studio lighting reviewer",
                "studio lighting": "studio lighting reviewer",
                "camera monitor": "camera monitor reviewer",
                "on-camera flash": "flash reviewer",
                "teleconverter": "teleconverter reviewer",
                "camera lens": "camera lens reviewer",
                "cinema lens": "cinema lens reviewer",
            }.get(capability, segment_term)
        elif segment.get("key") == "photography_role" and capability == "on-camera flash":
            segment_term = (
                "strobe photographer"
                if re.search(r"(?<![a-z0-9])strobe(?![a-z0-9])", _text(query).lower())
                else "flash photographer"
            )
        if objective == PROSPECTIVE_GROWTH:
            # First discover creators with segment-specific educational and
            # gear-decision content.  Product capability remains authoritative
            # below in ``locked_term_groups`` and is verified against public
            # profile/content evidence after retrieval.
            primary = _prospective_primary_query(segment_term)
            fallbacks = _dedupe([
                _text(f"{segment_term} gear review"),
                _text(f"{segment_term} photography tips"),
            ])
        else:
            anchored_query = _text(f"{segment_term} {capability}")
            primary = _text(f"{anchor} {anchored_query}") if anchor else anchored_query
            fallbacks = _dedupe([
                _text(f"{anchor} {segment_term} tutorial"),
                _text(f"{anchor} {segment_term} gear"),
            ])
        cells.append(_cell(
            index=index,
            key=segment["key"],
            label=segment["label"],
            source=segment["source"],
            locked=bool(segment["locked"]),
            primary=primary,
            fallbacks=fallbacks,
            objective=objective,
            platforms=platform_values,
            raw_limit=raw_limit,
            follower_filter=follower_filter,
            capability=capability,
            product_evidence_required=product_evidence_required,
            product_evidence_basis=product_evidence_basis,
            scene_terms=(
                segment.get("component_segments")
                or segment.get("required_scene_terms")
                or [segment["key"]]
            ),
            role_terms=segment.get("required_role_terms") or (),
            role_only=segment.get("role_only") is True,
        ))
    return cells


def apply_targeted_contract(
    plan: dict[str, Any],
    *,
    query: Any,
    body: Any = None,
    product: Any = None,
) -> dict[str, Any]:
    """Attach the V2 contract and make QueryCell primaries authoritative."""

    output = dict(plan or {})
    objective = normalize_objective(body, output)
    follower_filter = parse_follower_range(query, body)
    effective_platforms = _operator_platforms(body, output.get("platforms") or [])
    output.update({
        "search_spec_version": SEARCH_SPEC_VERSION,
        "objective": objective,
        "follower_filter": follower_filter,
        "explicit_segments": extract_explicit_segments(query, body),
        "product_anchor_required": objective == EXISTING_EVIDENCE,
        "brand_or_model_ranking_weight": 0 if objective == PROSPECTIVE_GROWTH else None,
        "ranking_claim_status": "descriptive_only",
        "platforms": effective_platforms,
    })
    if objective == PROSPECTIVE_GROWTH:
        # Compatibility fields must not accidentally reintroduce the old
        # brand-owner objective while QueryCell is being adopted downstream.
        output["search_query"] = _without_brand_model(
            output.get("search_query"),
            product,
            drop_focal=True,
        )
        output["search_queries"] = [
            value
            for value in (
                _without_brand_model(query_value, product, drop_focal=True)
                for query_value in (output.get("search_queries") or [])
            )
            if value
        ]
    if output.get("status") != "needs_clarification":
        cells = build_query_cells(
            query=query,
            body=body,
            product=product,
            product_focus=output.get("product_focus") or [],
            platforms=effective_platforms,
            legacy_queries=output.get("search_queries") or [output.get("search_query")],
        )
        output["query_cells"] = cells
        if cells:
            output["first_round_strategy"] = "independent_query_cells"
            output["authoritative_query_field"] = "query_cells"
            # Compatibility fields must describe the same people as the
            # authoritative cells.  Keeping provider/fallback prose here can
            # resurrect a negated role (for example "不要摄影师") in persisted
            # sessions or older local-recall paths.
            if objective == PROSPECTIVE_GROWTH:
                people_queries = _dedupe(cell.get("primary_query") for cell in cells)
                output["search_query"] = people_queries[0]
                output["search_queries"] = people_queries
    else:
        output["query_cells"] = []

    if not follower_filter["valid"]:
        output.update({
            "status": "needs_clarification",
            "reason": follower_filter["error"],
            "include_new_discovery": False,
            "new_discovery_limit": 0,
            "query_cells": [],
            "clarification": {
                "reason": follower_filter["error"],
                "message": "粉丝范围无效，请确认下限不高于上限后再搜索。",
            },
        })
    cells = output.get("query_cells") if isinstance(output.get("query_cells"), list) else []
    capability, product_evidence_basis = _product_evidence_context(
        query=query,
        product=product,
        product_focus=output.get("product_focus") or [],
        objective=objective,
        operator_segments=output.get("explicit_segments") or [],
    )
    resolved = product if isinstance(product, dict) else {}
    product_evidence_required = product_evidence_basis != "none"
    output["search_brief"] = {
        "search_spec_version": SEARCH_SPEC_VERSION,
        "objective": objective,
        "product": {
            "resolved_sku": _text(resolved.get("sku")),
            "capability": capability if product_evidence_required else None,
            "evidence_required": product_evidence_required,
            "evidence_basis": product_evidence_basis,
            "brand_or_model_required": objective == EXISTING_EVIDENCE,
        },
        "explicit_segments": list(output.get("explicit_segments") or []),
        "follower_filter": dict(follower_filter),
        "platforms": effective_platforms,
        "claim_status": "descriptive_only",
        "first_round_strategy": "independent_query_cells",
        "fallback_policy": "shortfall_only",
        "authoritative_query_field": "query_cells",
        "query_cells": cells,
    }
    return output


__all__ = [
    "DEFAULT_OBJECTIVE",
    "PROSPECTIVE_GROWTH",
    "EXISTING_EVIDENCE",
    "SUPPORTED_OBJECTIVES",
    "SEARCH_SPEC_VERSION",
    "LOCKED_TERM_GROUPS_SCHEMA",
    "LOCKED_TERM_GROUPS_VERSION",
    "LOCKED_TERM_GROUPS_SOURCE",
    "normalize_objective",
    "parse_follower_range",
    "extract_explicit_segments",
    "build_target_persona",
    "build_query_cells",
    "build_locked_term_groups",
    "project_locked_term_groups",
    "rebuild_locked_term_groups_for_cell",
    "controlled_aliases_for",
    "controlled_capability_use_terms_for",
    "canonical_controlled_term",
    "apply_targeted_contract",
]
