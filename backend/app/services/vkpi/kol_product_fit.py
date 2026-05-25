"""P4-6 deterministic KOL-to-product-family fit dry-run."""
from __future__ import annotations

import json
import math
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.services.vkpi import llm_gateway
from app.services.vkpi import memory
from app.services.vkpi.budget_guard import check_budget, get_budget_status
from app.services.vkpi.kol_product_fit_helpers import *  # noqa: F403
from app.services.vkpi.new_launch_match import (
    BUDGET_SCOPE,
    FORBIDDEN_WRITE_FLAGS,
    _contact_score,
    _country_key,
    _distribution,
    _entity_payload,
    _evidence,
    _evidence_count,
    _fact_payload,
    _family_tokens,
    _first_fact,
    _freshness_score,
    _json_default,
    _kol_entities,
    _kol_facts,
    _latest_fact_value,
    _legacy_entities_by_uid,
    _load_json,
    _lower,
    _market_detail,
    _market_signal_score,
    _median_score,
    _percentile,
    _pool_by_source_ref,
    _product_family_maps,
    _risk_count,
    _safe_float,
    _row_to_dict,
    _safe_int,
    _safe_limit,
    _source_payload,
    _split_csv,
    _target_market_signals,
    _text,
    _worked_links,
)

logger = get_logger(__name__)


SCENARIO = "kol_product_fit"
REASON_BUDGET_SCOPE = "cron:p4_recommendation_reasons"
_CATALOG_PRODUCT_BY_SKU: dict[str, dict[str, Any] | None] = {}
_CATALOG_PRODUCTS: list[dict[str, Any]] | None = None


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


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)


def _last_by_uid(table: str, column: str, uid: str) -> dict[str, Any]:
    row = get_conn().execute(f"SELECT * FROM {table} WHERE {column}=?", (uid,)).fetchone()
    return _row_to_dict(row) if row else {}


def _persist_preview_run(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") or {}
    items = payload.get("items") or []
    kol = payload.get("kol") or {}
    now = _iso(_utcnow())
    run_uid = f"p4kpf-{secrets.token_hex(8)}"
    conn = get_conn()
    filters = {
        "scenario": SCENARIO,
        "source_mode": "kol_product_fit_preview",
        "dry_run": True,
        "kol": kol,
        "llm_reasons_requested": bool(summary.get("llm_reasons_requested")),
        "reason_count": int(summary.get("reasons_attached") or 0),
    }
    try:
        conn.execute(
            """
            INSERT INTO vkpi_kol_recommendation_runs
                (run_uid, launch_id, strategy_version, status, candidate_count, recommendation_count,
                 filters_json, created_by_staff_id, created_at, completed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_uid,
                None,
                "kol_product_fit_v1",
                "previewed",
                int(summary.get("total_families_evaluated") or 0),
                len(items),
                _json(filters),
                None,
                now,
                now,
            ),
        )
        run = _last_by_uid("vkpi_kol_recommendation_runs", "run_uid", run_uid)
        run_id = int(run.get("id") or 0)
        recommendation_ids: list[int] = []
        for item in items:
            rec_uid = f"p4kpf-rec-{secrets.token_hex(8)}"
            reason = item.get("recommendation_reason") or {}
            feature_snapshot = {
                "scenario": SCENARIO,
                "kol": kol,
                "product_family_uid": item.get("product_family_uid"),
                "product_family_name": item.get("product_family_name"),
                "product_member_count": item.get("product_member_count"),
                "matched_catalog_product": item.get("matched_catalog_product"),
                "matched_catalog_products": item.get("matched_catalog_products") or [],
                "links": item.get("links") or {},
            }
            explanation = {
                "evidence_pro": item.get("evidence_pro") or [],
                "evidence_con": item.get("evidence_con") or [],
                "recommendation_reason": reason,
                "source": "p4_kol_product_fit",
            }
            conn.execute(
                """
                INSERT INTO vkpi_kol_recommendations
                    (recommendation_uid, run_id, launch_id, kol_pool_id, linked_main_kol_id, platform, handle,
                     display_name, score, rank, status, feature_snapshot_json, scoring_breakdown_json,
                     explanation_json, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    rec_uid,
                    run_id,
                    None,
                    kol.get("kol_pool_id") or None,
                    None,
                    kol.get("platform") or "",
                    kol.get("handle") or "",
                    item.get("product_family_name") or "",
                    item.get("score"),
                    item.get("rank"),
                    "previewed",
                    _json(feature_snapshot),
                    _json(item.get("score_breakdown") or {}),
                    _json(explanation),
                    now,
                    now,
                ),
            )
            rec = _last_by_uid("vkpi_kol_recommendations", "recommendation_uid", rec_uid)
            rec_id = int(rec.get("id") or 0)
            recommendation_ids.append(rec_id)
            conn.execute(
                """
                INSERT INTO vkpi_recommendation_explanations
                    (recommendation_id, explanation_type, explanation_text, strengths_json, concerns_json,
                     model_version, created_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    rec_id,
                    "p4_kol_product_fit",
                    reason.get("short_reason") or "Explainable KOL product-fit preview.",
                    _json(item.get("evidence_pro") or []),
                    _json(item.get("evidence_con") or []),
                    reason.get("model") or "rule_v1",
                    now,
                ),
            )
        conn.commit()
        return {
            "enabled": True,
            "run_uid": run_uid,
            "run_id": run_id,
            "recommendation_count": len(recommendation_ids),
            "recommendation_ids": recommendation_ids,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception as rollback_exc:
            logger.warning("kol product fit rollback failed: %s", rollback_exc)
        raise


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
    kol, pool = _resolve_kol(kol_entity_uid=kol_entity_uid, kol_pool_id=kol_pool_id, platform=platform, handle=handle)
    kol_id = int(kol["id"])
    identity = _entity_payload(kol, "identity_json")
    metadata = _entity_payload(kol, "metadata_json")
    legacy_uid = _text(metadata.get("legacy_entity_uid"))
    legacy = _legacy_entities_by_uid().get(legacy_uid, {})
    facts = _kol_facts().get(kol_id, [])
    links = _worked_links().get(kol_id, [])
    product_to_family, family_by_id = _product_family_maps()
    family_products = _family_product_ids(product_to_family)
    member_counts = _member_counts(product_to_family)
    official_links = _official_family_links()
    proved_ids = _proved_family_ids(links, product_to_family)
    proved_families = [family_by_id[family_id] for family_id in proved_ids if family_id in family_by_id]

    weak_label = _text(legacy.get("weak_label") or metadata.get("weak_label") or _latest_fact_value(facts, "weak_label"))
    decision = _text(legacy.get("resolution_decision") or "")
    sync_status = _text((pool or {}).get("sync_status") or _latest_fact_value(facts, "sync_status") or kol.get("status"))
    review_state = _text(metadata.get("review_state") or _latest_fact_value(facts, "review_state"))
    if weak_label == "blocked_risk" or decision == "drop":
        raise RuntimeError("selected KOL is blocked_risk or dropped")

    source_refs = {link.get("source_ref") for link in links if _text(link.get("source_ref"))}
    cooperation_count = len(source_refs)
    evidence_count = _evidence_count(facts)
    risk_count = _risk_count(facts)
    country_fact = _first_fact(facts, "country")
    contact_fact = _first_fact(facts, "contact_status")
    evidence_fact = _first_fact(facts, "evidence_count")
    contact_status = _latest_fact_value(facts, "contact_status")
    country = _text(identity.get("country") or (pool or {}).get("country") or _latest_fact_value(facts, "country"))
    contact_score, contact_label = _contact_score(contact_status, pool)
    primary = _split_csv(primary_markets)
    secondary = _split_csv(secondary_markets)
    kol_pool_id_value = _safe_int((pool or {}).get("id"))
    dimensions11_product_fit = _load_dimensions11_product_fit(kol_pool_id_value)

    eligible: list[dict[str, Any]] = []
    hard_excluded = 0
    low_evidence = 0
    dimensions11_matched = 0
    for family in _candidate_product_families():
        family_id = int(family["id"])
        if member_counts.get(family_id, 0) <= 0:
            hard_excluded += 1
            continue

        historical_score, historical_type, historical_link = _historical_fit(family, links, product_to_family)
        adjacent_score, adjacent_type, adjacent_family = _adjacent_fit(family, proved_families)
        dimensions11_score, dimensions11_match = _dimensions11_product_fit_for_family(family, dimensions11_product_fit)
        cooperation_score = _cooperation_depth(cooperation_count)
        signals = _target_market_signals(family_id)
        market_score, market_evidence = _market_signal_score(signals, now=now)
        region_score, region_reason = _region_relevance(country, primary, secondary)
        freshness_score = _freshness_score(evidence_count)
        base_score = (
            historical_score
            + adjacent_score
            + dimensions11_score
            + cooperation_score
            + market_score
            + contact_score
            + region_score
            + freshness_score
        )
        penalty_factors = {
            "needs_human_review": 0.85 if sync_status == "needs_human_review" or review_state == "needs_human_review" else 1.0,
            "risk_flag": 0.70 if risk_count > 0 else 1.0,
            "resolution_escalate": 0.90 if decision == "escalate" else 1.0,
            "contact_missing": 0.90 if contact_score == 0 else 1.0,
        }
        penalty_factor = math.prod(penalty_factors.values())
        final_score = round(base_score * penalty_factor, 1)

        evidence_pro: list[dict[str, Any]] = []
        evidence_con: list[dict[str, Any]] = []
        if historical_link:
            evidence_pro.append(
                _evidence(
                    evidence_type=historical_type,
                    polarity="pro",
                    severity="info",
                    detail=f"{historical_type.replace('_', ' ')} via {_text(historical_link.get('product_name'))}",
                    score_component="historical_fit",
                    row=historical_link,
                    payload=_source_payload(historical_link),
                )
            )
        else:
            evidence_con.append(
                _evidence(
                    evidence_type="no_direct_history",
                    polarity="con",
                    severity="low",
                    detail=f"No direct KOL cooperation found for {_render_family_detail(family)}",
                    score_component="historical_fit",
                )
            )
        if adjacent_family:
            evidence_pro.append(
                _evidence(
                    evidence_type=adjacent_type,
                    polarity="pro",
                    severity="info",
                    detail=f"{adjacent_type.replace('_', ' ')} from {_render_family_detail(adjacent_family)}",
                    score_component="adjacent_product_fit",
                    row=adjacent_family,
                )
            )
        if dimensions11_match:
            dimensions11_matched += 1
            matched_catalog_products = _catalog_products_for_match(dimensions11_match, family)
            matched_catalog_product = matched_catalog_products[0] if matched_catalog_products else _catalog_product_for_sku(dimensions11_match.get("sku"))
            evidence_pro.append(
                _evidence(
                    evidence_type="dimensions11_product_fit",
                    polarity="pro",
                    severity="info",
                    detail=(
                        f"11D product fit matched {dimensions11_match.get('sku')} "
                        f"score={dimensions11_match.get('score')}/100 "
                        f"confidence={dimensions11_match.get('confidence')}"
                    ),
                    score_component="dimensions11_product_fit",
                    row={
                        "source_table": "vkpi_kol_profile_deep",
                        "source_id": dimensions11_match.get("profile_deep_id"),
                        "confidence_score": dimensions11_match.get("confidence"),
                    },
                    payload={
                        "source_ref": f"vkpi_kol_profile_deep:{dimensions11_match.get('profile_deep_id')}",
                        "source_sheet": "dimensions_11_json.block4_specialty.product_fit",
                        "source_id": dimensions11_match.get("sku"),
                        "catalog_product": matched_catalog_product or {},
                        "catalog_products": matched_catalog_products,
                    },
                )
            )
        else:
            matched_catalog_products = _catalog_products_for_match(None, family)
            matched_catalog_product = matched_catalog_products[0] if matched_catalog_products else None
        if cooperation_count:
            evidence_pro.append(
                _evidence(
                    evidence_type="cooperation_depth",
                    polarity="pro",
                    severity="info",
                    detail=f"{cooperation_count} unique historical cooperation records for this KOL",
                    score_component="cooperation_depth",
                    row=links[0] if links else {},
                    payload=_source_payload(links[0]) if links else {},
                )
            )
        for signal in market_evidence[:2]:
            payload = _fact_payload(signal)
            evidence_pro.append(
                _evidence(
                    evidence_type=_text(payload.get("signal_type") or signal.get("fact_type")),
                    polarity="pro",
                    severity="info",
                    detail=_market_detail(signal),
                    score_component="market_activity",
                    row=signal,
                    payload=payload,
                )
            )
        if not market_evidence and official_links.get(family_id):
            row = official_links[family_id][0]
            evidence_pro.append(
                _evidence(
                    evidence_type="official_account_activity",
                    polarity="pro",
                    severity="info",
                    detail="Official account published content linked to this family",
                    score_component="market_activity",
                    row=row,
                    payload=_source_payload(row),
                )
            )
        if contact_score > 0:
            evidence_pro.append(
                _evidence(
                    evidence_type="contact_available",
                    polarity="pro",
                    severity="info",
                    detail=f"Contact availability: {contact_label}",
                    score_component="contact_readiness",
                    row=contact_fact,
                    payload=_fact_payload(contact_fact) if contact_fact else {},
                )
            )
        else:
            evidence_con.append(
                _evidence(
                    evidence_type="contact_missing",
                    polarity="con",
                    severity="medium",
                    detail="No usable contact status in Memory",
                    score_component="contact_readiness",
                    row=contact_fact,
                    payload=_fact_payload(contact_fact) if contact_fact else {},
                )
            )
        if country:
            evidence_pro.append(
                _evidence(
                    evidence_type="region_relevance",
                    polarity="pro",
                    severity="info",
                    detail=f"{country} scored as {region_reason}",
                    score_component="region_relevance",
                    row=country_fact,
                    payload=_fact_payload(country_fact) if country_fact else {},
                )
            )
        else:
            evidence_con.append(
                _evidence(
                    evidence_type="missing_country",
                    polarity="con",
                    severity="low",
                    detail="KOL country is missing",
                    score_component="region_relevance",
                    row=country_fact,
                    payload=_fact_payload(country_fact) if country_fact else {},
                )
            )
        if evidence_count:
            evidence_pro.append(
                _evidence(
                    evidence_type="data_quality",
                    polarity="pro",
                    severity="info",
                    detail=f"{evidence_count} legacy evidence rows for this KOL",
                    score_component="data_quality",
                    row=evidence_fact,
                    payload=_fact_payload(evidence_fact) if evidence_fact else {},
                )
            )
        else:
            evidence_con.append(
                _evidence(
                    evidence_type="missing_evidence_count",
                    polarity="con",
                    severity="low",
                    detail="No evidence_count fact found",
                    score_component="data_quality",
                )
            )
        if sync_status == "needs_human_review" or review_state == "needs_human_review":
            evidence_con.append(
                _evidence(
                    evidence_type="sync_needs_review",
                    polarity="con",
                    severity="medium",
                    detail=f"sync_status={sync_status}, review_state={review_state}",
                    score_component="penalty",
                )
            )
        if decision == "escalate":
            evidence_con.append(
                _evidence(
                    evidence_type="resolution_escalate",
                    polarity="con",
                    severity="low",
                    detail="P2C resolution decision is escalate",
                    score_component="penalty",
                )
            )
        if risk_count:
            risk_fact = next((fact for fact in facts if _text(fact.get("fact_type")) == "risk_flag"), {})
            evidence_con.append(
                _evidence(
                    evidence_type="risk_flag",
                    polarity="con",
                    severity="high",
                    detail=f"{risk_count} risk flag(s) attached",
                    score_component="penalty",
                    row=risk_fact,
                    payload=_fact_payload(risk_fact) if risk_fact else {},
                )
            )

        evidence_total = len(evidence_pro) + len(evidence_con)
        if evidence_total < 3 and not include_low_evidence:
            low_evidence += 1
            continue

        eligible.append(
            {
                "rank": 0,
                "percentile_rank": 0,
                "product_family_uid": family.get("entity_uid"),
                "product_family_name": _render_family_detail(family),
                "product_member_count": member_counts.get(family_id, 0),
                "score": final_score,
                "score_breakdown": {
                    "historical_fit": historical_score,
                    "adjacent_product_fit": adjacent_score,
                    "dimensions11_product_fit": dimensions11_score,
                    "cooperation_depth": cooperation_score,
                    "market_activity": market_score,
                    "contact_readiness": contact_score,
                    "region_relevance": region_score,
                    "data_quality": freshness_score,
                    "base": base_score,
                    "penalty_factors": penalty_factors,
                    "penalty_factor": round(penalty_factor, 4),
                    "final": final_score,
                },
                "evidence_pro": evidence_pro,
                "evidence_con": evidence_con,
                "links": {"open_in_vkpi": f"/products/{family.get('entity_uid')}"},
                "matched_catalog_product": matched_catalog_product,
                "matched_catalog_products": matched_catalog_products,
                "debug": {
                    "family_id": family_id,
                    "product_ids": sorted(family_products.get(family_id, set()))[:20],
                },
            }
        )

    eligible.sort(
        key=lambda item: (
            float(item["score"]),
            int(item["score_breakdown"]["historical_fit"]),
            int(item["score_breakdown"]["adjacent_product_fit"]),
            int(item["score_breakdown"]["market_activity"]),
        ),
        reverse=True,
    )
    for idx, item in enumerate(eligible):
        item["rank"] = idx + 1
        item["percentile_rank"] = _percentile(idx, len(eligible))
    returned = eligible[:safe_limit]
    median = _median_score(returned)
    markdown_display = [item for item in returned if float(item["score"]) >= median]
    kol_payload = {
        "kol_entity_uid": kol.get("entity_uid"),
        "legacy_entity_uid": legacy_uid,
        "kol_pool_id": _safe_int((pool or {}).get("id")),
        "platform": _text((pool or {}).get("platform") or identity.get("platform")),
        "handle": _text((pool or {}).get("handle") or identity.get("handle")),
        "display_name": _text((pool or {}).get("display_name") or kol.get("display_name")),
        "country": country,
        "weak_label": weak_label,
        "resolution_decision": decision,
        "sync_status": sync_status,
        "review_state": review_state,
        "dimensions11_product_fit_ready": bool(dimensions11_product_fit),
    }
    summary = {
        "total_families_evaluated": len(_candidate_product_families()),
        "eligible_after_hard_filters": len(eligible),
        "excluded_inactive_or_empty_family": hard_excluded,
        "excluded_low_evidence": low_evidence,
        "dimensions11_product_fit_candidates": len(dimensions11_product_fit),
        "dimensions11_product_fit_matched": dimensions11_matched,
        "returned": len(returned),
        "markdown_display_count": len(markdown_display),
        "top_score": returned[0]["score"] if returned else 0,
        "median_score": median,
        "llm_reasons_requested": bool(with_llm_reasons),
        "reasons_attached": 0,
    }
    payload = {
        "scenario": SCENARIO,
        "mode": "dry_run",
        "generated_at": _iso(now),
        "provider_calls_allowed": False,
        "budget_guard": {
            "scope": BUDGET_SCOPE,
            "estimated_cost_usd": 0.0,
            "allowed": bool(cost_ok),
            "recorded_cost": False,
            "configured": bool(budget_status.get("configured")),
        },
        "kol": kol_payload,
        "summary": summary,
        "score_distribution": _distribution(returned),
        "items": returned,
        "markdown_items": markdown_display,
    }
    if with_llm_reasons:
        reasons_attached = 0
        for item in returned[: max(0, min(int(reason_limit or 0), len(returned)))]:
            _attach_reason(payload, item)
            reasons_attached += 1
        summary["reasons_attached"] = reasons_attached
    if persist_run:
        payload["persistence"] = _persist_preview_run(payload)
    else:
        payload["persistence"] = {"enabled": False}
    _json_write(json_out, {key: value for key, value in payload.items() if key != "markdown_items"})
    _markdown_write(md_out, payload)
    return payload


