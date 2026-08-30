"""P4-6 deterministic KOL-to-product-family fit dry-run."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.connection import get_conn
from app.domains import memory
from app.domains.costs.budget_guard import check_budget, get_budget_status
from app.domains.kol.product_fit_evidence import (
    append_activity_fit_evidence as _append_activity_fit_evidence,
    append_catalog_fit_evidence as _append_catalog_fit_evidence,
    append_readiness_fit_evidence as _append_readiness_fit_evidence,
)
from app.domains.kol.product_fit_persistence import persist_product_fit_preview_run
from app.domains.kol.product_fit_reason_adapter import attach_reason as _attach_reason
from app.domains.kol.product_fit_repository import (
    SqlProductFitRepository,
    resolve_kol,
)
from app.shared.product_fit_contracts import (
    ProductFitRepository,
    RecommendationReasonPort,
    copy_reason_result,
)
from app.shared.product_fit_policy import (
    PRODUCT_FIT_REASON_BUDGET_SCOPE as REASON_BUDGET_SCOPE,
    RECOMMENDATION_BUDGET_SCOPE as BUDGET_SCOPE,
    adjacent_fit as _adjacent_fit,
    append_history_fit_evidence as _append_history_fit_evidence,
    append_penalty_fit_evidence as _append_penalty_fit_evidence,
    as_row_dict as _row_to_dict,
    contact_score as _contact_score,
    cooperation_depth as _cooperation_depth,
    country_key as _country_key,
    dimensions11_fit_for_family as _dimensions11_product_fit_for_family,
    distribution as _distribution,
    entity_payload as _entity_payload,
    evidence as _evidence,
    evidence_count as _evidence_count,
    fact_payload as _fact_payload,
    family_product_ids as _family_product_ids,
    family_tokens as _family_tokens,
    first_fact as _first_fact,
    freshness_score as _freshness_score,
    historical_fit as _historical_fit,
    json_default as _json_default,
    latest_fact_value as _latest_fact_value,
    load_json as _load_json,
    lower as _lower,
    market_detail as _market_detail,
    market_signal_score as _market_signal_score,
    median_score as _median_score,
    member_counts as _member_counts,
    normalize_product_fit_key as _normalize_product_fit_key,
    percentile as _percentile,
    proved_family_ids as _proved_family_ids,
    rank_product_fit_candidates,
    region_relevance as _region_relevance,
    render_family_detail as _render_family_detail,
    risk_count as _risk_count,
    safe_float as _safe_float,
    safe_int as _safe_int,
    safe_limit as _safe_limit,
    source_payload as _source_payload,
    split_csv as _split_csv,
    text as _text,
)
from app.shared.product_fit_rendering import format_preview_summary, render_markdown

SCENARIO = "kol_product_fit"
_CATALOG_PRODUCT_BY_SKU: dict[str, dict[str, Any] | None] = {}
_CATALOG_PRODUCTS: list[dict[str, Any]] | None = None
_DEFAULT_PRODUCT_FIT_REPOSITORY = SqlProductFitRepository()


def _kol_entities() -> list[dict[str, Any]]:
    return _DEFAULT_PRODUCT_FIT_REPOSITORY.list_kol_entities()


def _pool_by_source_ref() -> dict[str, dict[str, Any]]:
    return _DEFAULT_PRODUCT_FIT_REPOSITORY.pools_by_source_ref()


def _legacy_entities_by_uid() -> dict[str, dict[str, Any]]:
    return _DEFAULT_PRODUCT_FIT_REPOSITORY.legacy_entities_by_uid()


def _kol_facts() -> dict[int, list[dict[str, Any]]]:
    return _DEFAULT_PRODUCT_FIT_REPOSITORY.facts_by_kol()


def _worked_links() -> dict[int, list[dict[str, Any]]]:
    return _DEFAULT_PRODUCT_FIT_REPOSITORY.worked_links_by_kol()


def _product_family_maps(
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    return _DEFAULT_PRODUCT_FIT_REPOSITORY.product_family_maps()


def _target_market_signals(family_id: int) -> list[dict[str, Any]]:
    return _DEFAULT_PRODUCT_FIT_REPOSITORY.target_market_signals(family_id)


def _candidate_product_families() -> list[dict[str, Any]]:
    return _DEFAULT_PRODUCT_FIT_REPOSITORY.candidate_families()


def _official_family_links() -> dict[int, list[dict[str, Any]]]:
    return _DEFAULT_PRODUCT_FIT_REPOSITORY.official_family_links()


def _load_dimensions11_product_fit(
    kol_pool_id: int,
) -> dict[str, dict[str, Any]]:
    return _DEFAULT_PRODUCT_FIT_REPOSITORY.dimensions11_fit(kol_pool_id)


def _resolve_kol(
    *,
    kol_entity_uid: str = "",
    kol_pool_id: int = 0,
    platform: str = "",
    handle: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    return resolve_kol(
        _DEFAULT_PRODUCT_FIT_REPOSITORY,
        kol_entity_uid=kol_entity_uid,
        kol_pool_id=kol_pool_id,
        platform=platform,
        handle=handle,
    )


def _rank_product_fit_candidates(
    eligible: list[dict[str, Any]],
    *,
    safe_limit: int,
) -> tuple[list[dict[str, Any]], float, list[dict[str, Any]]]:
    return rank_product_fit_candidates(eligible, safe_limit_value=safe_limit)


def _preview_execution_policy(
    *,
    with_llm_reasons: bool,
    reason_limit: int,
    returned_count: int,
) -> dict[str, Any]:
    """Describe the bounded provider work this preview will actually attempt.

    Ranking and business actions remain deterministic/read-only.  The only
    provider-capable step is the optional explanation attached to a bounded
    number of already-ranked candidates.
    """

    planned = (
        max(0, min(int(reason_limit or 0), int(returned_count or 0)))
        if with_llm_reasons
        else 0
    )
    provider_calls_allowed = planned > 0
    return {
        "mode": "ai_enriched_preview" if provider_calls_allowed else "dry_run",
        "provider_calls_allowed": provider_calls_allowed,
        "provider_calls_planned": planned,
        "provider_call_scope": (
            "recommendation_reason_only" if provider_calls_allowed else "none"
        ),
        "deterministic_ranking": True,
        "business_actions_executed": False,
    }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_write(path: str, payload: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n", encoding="utf-8")


def _markdown_write(path: str, payload: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_markdown(payload), encoding="utf-8")


def _compact_catalog_product(row: dict[str, Any]) -> dict[str, Any]:
    item = _row_to_dict(row)
    specs = _load_json(item.get("specs_json") or "{}", {})
    if not isinstance(specs, dict):
        specs = {}
    compact_specs = {
        key: specs.get(key)
        for key in (
            "lens_mount",
            "lens_elements",
            "focal_length",
            "viewing_angle",
            "aperture",
            "aperture_blades",
            "shooting_distance",
            "focus_mechanism",
            "focus_motor",
            "focus_mode",
            "max_magnification",
            "lens_size",
            "weight",
            "filter_size",
        )
        if _text(specs.get(key))
    }
    result = {
        "sku": _text(item.get("sku")),
        "model_name": _text(item.get("model_name")),
        "marketing_name": _text(item.get("marketing_name")),
        "category_main": _text(item.get("category_main")),
        "category_detail": _text(item.get("category_detail")),
        "series": _text(item.get("series")),
        "mount": _text(item.get("mount")),
        "price_usd": _safe_float(item.get("price_usd"), 0.0) or None,
        "product_url": _text(item.get("product_url")),
        "source_confidence": _safe_float(item.get("source_confidence"), 0.0),
        "specs": compact_specs,
    }
    return result


def _catalog_product_for_sku(sku: Any) -> dict[str, Any] | None:
    sku_text = _text(sku).upper()
    if not sku_text:
        return None
    if sku_text in _CATALOG_PRODUCT_BY_SKU:
        return _CATALOG_PRODUCT_BY_SKU[sku_text]
    try:
        row = get_conn().execute(
            """
            SELECT sku, category_main, category_detail, model_name, marketing_name,
                   price_usd, series, mount, product_url, specs_json, source_confidence
            FROM vkpi_products
            WHERE sku=?
            LIMIT 1
            """,
            (sku_text,),
        ).fetchone()
    except Exception:
        row = None
    if not row:
        _CATALOG_PRODUCT_BY_SKU[sku_text] = None
        return None
    result = _compact_catalog_product(_row_to_dict(row))
    _CATALOG_PRODUCT_BY_SKU[sku_text] = result
    return result


def _catalog_products() -> list[dict[str, Any]]:
    global _CATALOG_PRODUCTS
    if _CATALOG_PRODUCTS is not None:
        return _CATALOG_PRODUCTS
    try:
        rows = get_conn().execute(
            """
            SELECT sku, category_main, category_detail, model_name, marketing_name,
                   price_usd, series, mount, product_url, specs_json, source_confidence
            FROM vkpi_products
            ORDER BY source_confidence DESC, sku ASC
            LIMIT 500
            """
        ).fetchall()
    except Exception:
        rows = []
    products: list[dict[str, Any]] = []
    for row in rows:
        product = _compact_catalog_product(_row_to_dict(row))
        normalized = _normalize_product_fit_key(
            f"{product.get('sku')} {product.get('model_name')} {product.get('marketing_name')}"
        )
        products.append({**product, "normalized": normalized})
    _CATALOG_PRODUCTS = products
    return products


def _catalog_products_for_match(match: dict[str, Any] | None, family: dict[str, Any], *, limit: int = 6) -> list[dict[str, Any]]:
    needles = [
        _normalize_product_fit_key((match or {}).get("sku")),
        _normalize_product_fit_key(_render_family_detail(family)),
        _normalize_product_fit_key(family.get("identity_key")),
    ]
    needles = [needle for needle in needles if needle]
    if not needles:
        return []
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for product in _catalog_products():
        haystack = _text(product.get("normalized"))
        sku = _text(product.get("sku"))
        if not haystack or not sku or sku in seen:
            continue
        if any(needle in haystack or haystack.startswith(needle) for needle in needles):
            clean = {key: value for key, value in product.items() if key != "normalized"}
            results.append(clean)
            seen.add(sku)
        if len(results) >= limit:
            break
    return results


def _product_fit_context(
    *,
    kol_entity_uid: str,
    kol_pool_id: int,
    platform: str,
    handle: str,
    primary_markets: str,
    secondary_markets: str,
    now: datetime,
    repository: ProductFitRepository | None = None,
) -> dict[str, Any]:
    if repository is None:
        kol, pool = _resolve_kol(
            kol_entity_uid=kol_entity_uid,
            kol_pool_id=kol_pool_id,
            platform=platform,
            handle=handle,
        )
    else:
        kol, pool = resolve_kol(
            repository,
            kol_entity_uid=kol_entity_uid,
            kol_pool_id=kol_pool_id,
            platform=platform,
            handle=handle,
        )
    kol_id = int(kol["id"])
    identity = _entity_payload(kol, "identity_json")
    metadata = _entity_payload(kol, "metadata_json")
    legacy_uid = _text(metadata.get("legacy_entity_uid"))
    legacy_map = (
        _legacy_entities_by_uid()
        if repository is None
        else repository.legacy_entities_by_uid()
    )
    facts_map = _kol_facts() if repository is None else repository.facts_by_kol()
    links_map = (
        _worked_links() if repository is None else repository.worked_links_by_kol()
    )
    legacy = legacy_map.get(legacy_uid, {})
    facts = facts_map.get(kol_id, [])
    links = links_map.get(kol_id, [])
    product_to_family, family_by_id = (
        _product_family_maps()
        if repository is None
        else repository.product_family_maps()
    )
    proved_ids = _proved_family_ids(links, product_to_family)
    weak_label = _text(
        legacy.get("weak_label")
        or metadata.get("weak_label")
        or _latest_fact_value(facts, "weak_label")
    )
    decision = _text(legacy.get("resolution_decision") or "")
    sync_status = _text(
        (pool or {}).get("sync_status")
        or _latest_fact_value(facts, "sync_status")
        or kol.get("status")
    )
    review_state = _text(
        metadata.get("review_state") or _latest_fact_value(facts, "review_state")
    )
    if weak_label == "blocked_risk" or decision == "drop":
        raise RuntimeError("selected KOL is blocked_risk or dropped")

    source_refs = {
        link.get("source_ref") for link in links if _text(link.get("source_ref"))
    }
    contact_status = _latest_fact_value(facts, "contact_status")
    country = _text(
        identity.get("country")
        or (pool or {}).get("country")
        or _latest_fact_value(facts, "country")
    )
    contact_score, contact_label = _contact_score(contact_status, pool)
    pool_id = _safe_int((pool or {}).get("id"))
    return {
        "now": now,
        "kol": kol,
        "pool": pool,
        "identity": identity,
        "legacy_uid": legacy_uid,
        "weak_label": weak_label,
        "decision": decision,
        "sync_status": sync_status,
        "review_state": review_state,
        "facts": facts,
        "links": links,
        "product_to_family": product_to_family,
        "family_products": _family_product_ids(product_to_family),
        "member_counts": _member_counts(product_to_family),
        "official_links": (
            _official_family_links()
            if repository is None
            else repository.official_family_links()
        ),
        "proved_families": [
            family_by_id[family_id]
            for family_id in proved_ids
            if family_id in family_by_id
        ],
        "cooperation_count": len(source_refs),
        "evidence_count": _evidence_count(facts),
        "risk_count": _risk_count(facts),
        "country_fact": _first_fact(facts, "country"),
        "contact_fact": _first_fact(facts, "contact_status"),
        "evidence_fact": _first_fact(facts, "evidence_count"),
        "country": country,
        "contact_score": contact_score,
        "contact_label": contact_label,
        "primary": _split_csv(primary_markets),
        "secondary": _split_csv(secondary_markets),
        "kol_pool_id": pool_id,
        "dimensions11_product_fit": (
            _load_dimensions11_product_fit(pool_id)
            if repository is None
            else repository.dimensions11_fit(pool_id)
        ),
    }


def _family_fit_scores(
    family: dict[str, Any],
    context: dict[str, Any],
    *,
    repository: ProductFitRepository | None = None,
) -> dict[str, Any]:
    family_id = int(family["id"])
    historical_score, historical_type, historical_link = _historical_fit(
        family,
        context["links"],
        context["product_to_family"],
    )
    adjacent_score, adjacent_type, adjacent_family = _adjacent_fit(
        family,
        context["proved_families"],
    )
    dimensions11_score, dimensions11_match = _dimensions11_product_fit_for_family(
        family,
        context["dimensions11_product_fit"],
    )
    cooperation_score = _cooperation_depth(context["cooperation_count"])
    signals = (
        _target_market_signals(family_id)
        if repository is None
        else repository.target_market_signals(family_id)
    )
    market_score, market_evidence = _market_signal_score(
        signals,
        now=context["now"],
    )
    region_score, region_reason = _region_relevance(
        context["country"],
        context["primary"],
        context["secondary"],
    )
    freshness_score = _freshness_score(context["evidence_count"])
    base_score = (
        historical_score
        + adjacent_score
        + dimensions11_score
        + cooperation_score
        + market_score
        + context["contact_score"]
        + region_score
        + freshness_score
    )
    penalty_factors = {
        "needs_human_review": (
            0.85
            if context["sync_status"] == "needs_human_review"
            or context["review_state"] == "needs_human_review"
            else 1.0
        ),
        "risk_flag": 0.70 if context["risk_count"] > 0 else 1.0,
        "resolution_escalate": 0.90 if context["decision"] == "escalate" else 1.0,
        "contact_missing": 0.90 if context["contact_score"] == 0 else 1.0,
    }
    penalty_factor = math.prod(penalty_factors.values())
    return {
        "historical_score": historical_score,
        "historical_type": historical_type,
        "historical_link": historical_link,
        "adjacent_score": adjacent_score,
        "adjacent_type": adjacent_type,
        "adjacent_family": adjacent_family,
        "dimensions11_score": dimensions11_score,
        "dimensions11_match": dimensions11_match,
        "cooperation_score": cooperation_score,
        "market_score": market_score,
        "market_evidence": market_evidence,
        "region_score": region_score,
        "region_reason": region_reason,
        "freshness_score": freshness_score,
        "base_score": base_score,
        "penalty_factors": penalty_factors,
        "penalty_factor": penalty_factor,
        "final_score": round(base_score * penalty_factor, 1),
    }


def _product_fit_candidate(
    family: dict[str, Any],
    context: dict[str, Any],
    *,
    include_low_evidence: bool,
    repository: ProductFitRepository | None = None,
) -> tuple[dict[str, Any] | None, bool]:
    family_id = int(family["id"])
    scores = _family_fit_scores(family, context, repository=repository)
    evidence_pro: list[dict[str, Any]] = []
    evidence_con: list[dict[str, Any]] = []
    _append_history_fit_evidence(family, scores, evidence_pro, evidence_con)
    product, products, dimensions11_matched = _append_catalog_fit_evidence(
        family,
        scores,
        evidence_pro,
        catalog_products_for_match=_catalog_products_for_match,
        catalog_product_for_sku=_catalog_product_for_sku,
    )
    _append_activity_fit_evidence(family_id, scores, context, evidence_pro)
    _append_readiness_fit_evidence(scores, context, evidence_pro, evidence_con)
    _append_penalty_fit_evidence(context, evidence_con)
    if len(evidence_pro) + len(evidence_con) < 3 and not include_low_evidence:
        return None, dimensions11_matched
    return {
        "rank": 0,
        "percentile_rank": 0,
        "product_family_uid": family.get("entity_uid"),
        "product_family_name": _render_family_detail(family),
        "product_member_count": context["member_counts"].get(family_id, 0),
        "score": scores["final_score"],
        "score_breakdown": {
            "historical_fit": scores["historical_score"],
            "adjacent_product_fit": scores["adjacent_score"],
            "dimensions11_product_fit": scores["dimensions11_score"],
            "cooperation_depth": scores["cooperation_score"],
            "market_activity": scores["market_score"],
            "contact_readiness": context["contact_score"],
            "region_relevance": scores["region_score"],
            "data_quality": scores["freshness_score"],
            "base": scores["base_score"],
            "penalty_factors": scores["penalty_factors"],
            "penalty_factor": round(scores["penalty_factor"], 4),
            "final": scores["final_score"],
        },
        "evidence_pro": evidence_pro,
        "evidence_con": evidence_con,
        "links": {"open_in_vkpi": f"/products/{family.get('entity_uid')}"},
        "matched_catalog_product": product,
        "matched_catalog_products": products,
        "debug": {
            "family_id": family_id,
            "product_ids": sorted(context["family_products"].get(family_id, set()))[:20],
        },
    }, dimensions11_matched


def _collect_product_fit_candidates(
    context: dict[str, Any],
    *,
    include_low_evidence: bool,
    repository: ProductFitRepository | None = None,
) -> tuple[list[dict[str, Any]], int, int, int]:
    eligible: list[dict[str, Any]] = []
    hard_excluded = 0
    low_evidence = 0
    dimensions11_matched = 0
    families = (
        _candidate_product_families()
        if repository is None
        else repository.candidate_families()
    )
    for family in families:
        family_id = int(family["id"])
        if context["member_counts"].get(family_id, 0) <= 0:
            hard_excluded += 1
            continue
        if repository is None:
            candidate, matched = _product_fit_candidate(
                family,
                context,
                include_low_evidence=include_low_evidence,
            )
        else:
            candidate, matched = _product_fit_candidate(
                family,
                context,
                include_low_evidence=include_low_evidence,
                repository=repository,
            )
        dimensions11_matched += int(matched)
        if candidate is None:
            low_evidence += 1
            continue
        eligible.append(candidate)
    return eligible, hard_excluded, low_evidence, dimensions11_matched


def build_kol_product_fit_preview(
    *,
    kol_entity_uid: str = "",
    kol_pool_id: int = 0,
    platform: str = "",
    handle: str = "",
    limit: int = 50,
    primary_markets: str = "",
    secondary_markets: str = "",
    include_low_evidence: bool = False,
    json_out: str = "",
    md_out: str = "",
    with_llm_reasons: bool = False,
    reason_limit: int = 10,
    persist_run: bool = False,
    repository: ProductFitRepository | None = None,
    reason_port: RecommendationReasonPort | None = None,
) -> dict[str, Any]:
    safe_limit = _safe_limit(limit)
    readiness = memory.readiness()
    if readiness.get("status") != "ready_for_p4_dry_run":
        raise RuntimeError(f"memory readiness blocked P4 dry-run: {readiness.get('status')}")
    if bool(readiness.get("provider_calls_allowed")):
        raise RuntimeError("P4 dry-run requires provider_calls_allowed=false")

    cost_ok = check_budget(BUDGET_SCOPE, 0.0)
    budget_status = get_budget_status(BUDGET_SCOPE, estimated_cost=0.0)
    if not cost_ok:
        raise RuntimeError("budget_guard_blocked")

    now = _utcnow()
    context = _product_fit_context(
        kol_entity_uid=kol_entity_uid,
        kol_pool_id=kol_pool_id,
        platform=platform,
        handle=handle,
        primary_markets=primary_markets,
        secondary_markets=secondary_markets,
        now=now,
        repository=repository,
    )
    eligible, hard_excluded, low_evidence, dimensions11_matched = (
        _collect_product_fit_candidates(
            context,
            include_low_evidence=include_low_evidence,
            repository=repository,
        )
    )
    returned, median, markdown_display = _rank_product_fit_candidates(
        eligible,
        safe_limit=safe_limit,
    )
    execution_policy = _preview_execution_policy(
        with_llm_reasons=with_llm_reasons,
        reason_limit=reason_limit,
        returned_count=len(returned),
    )
    reason_items = returned[: execution_policy["provider_calls_planned"]]
    kol = context["kol"]
    pool = context["pool"]
    identity = context["identity"]
    kol_payload = {
        "kol_entity_uid": kol.get("entity_uid"),
        "legacy_entity_uid": context["legacy_uid"],
        "kol_pool_id": _safe_int((pool or {}).get("id")),
        "platform": _text((pool or {}).get("platform") or identity.get("platform")),
        "handle": _text((pool or {}).get("handle") or identity.get("handle")),
        "display_name": _text((pool or {}).get("display_name") or kol.get("display_name")),
        "country": context["country"],
        "weak_label": context["weak_label"],
        "resolution_decision": context["decision"],
        "sync_status": context["sync_status"],
        "review_state": context["review_state"],
        "dimensions11_product_fit_ready": bool(context["dimensions11_product_fit"]),
    }
    summary = {
        "total_families_evaluated": len(
            _candidate_product_families()
            if repository is None
            else repository.candidate_families()
        ),
        "eligible_after_hard_filters": len(eligible),
        "excluded_inactive_or_empty_family": hard_excluded,
        "excluded_low_evidence": low_evidence,
        "dimensions11_product_fit_candidates": len(context["dimensions11_product_fit"]),
        "dimensions11_product_fit_matched": dimensions11_matched,
        "returned": len(returned),
        "markdown_display_count": len(markdown_display),
        "top_score": returned[0]["score"] if returned else 0,
        "median_score": median,
        "llm_reasons_requested": bool(with_llm_reasons),
        "llm_reason_calls_planned": execution_policy["provider_calls_planned"],
        "reasons_attached": 0,
    }
    payload = {
        "scenario": SCENARIO,
        "mode": execution_policy["mode"],
        "generated_at": _iso(now),
        "provider_calls_allowed": execution_policy["provider_calls_allowed"],
        "execution_policy": execution_policy,
        "budget_guard": {
            "scope": BUDGET_SCOPE,
            "estimated_cost_usd": 0.0,
            "allowed": bool(cost_ok),
            "recorded_cost": False,
            "configured": bool(budget_status.get("configured")),
            "llm_reason_scope": REASON_BUDGET_SCOPE,
            "llm_reason_calls_planned": execution_policy["provider_calls_planned"],
            "llm_reason_atomic_reservation_per_call": True,
            "llm_reason_requires_configured_budget": True,
        },
        "kol": kol_payload,
        "summary": summary,
        "score_distribution": _distribution(returned),
        "items": returned,
        "markdown_items": markdown_display,
    }
    if reason_items:
        reasons_attached = 0
        for item in reason_items:
            if reason_port is None:
                _attach_reason(payload, item)
            else:
                item["recommendation_reason"] = copy_reason_result(
                    reason_port.generate_reason(
                        item,
                        binding=payload,
                        token_limit=220,
                        budget_scope=REASON_BUDGET_SCOPE,
                    )
                )
            reasons_attached += 1
        summary["reasons_attached"] = reasons_attached
    if persist_run:
        payload["persistence"] = persist_product_fit_preview_run(payload, scenario=SCENARIO, generated_at=_iso(_utcnow()))
    else:
        payload["persistence"] = {"enabled": False}
    _json_write(json_out, {key: value for key, value in payload.items() if key != "markdown_items"})
    _markdown_write(md_out, payload)
    return payload
