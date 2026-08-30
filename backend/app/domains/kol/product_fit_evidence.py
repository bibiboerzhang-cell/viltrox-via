"""Evidence projections for deterministic KOL product-fit candidates."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.shared.product_fit_policy import (
    evidence,
    fact_payload,
    market_detail,
    source_payload,
    text,
)


def append_catalog_fit_evidence(
    family: dict[str, Any],
    scores: dict[str, Any],
    evidence_pro: list[dict[str, Any]],
    *,
    catalog_products_for_match: Callable[..., list[dict[str, Any]]],
    catalog_product_for_sku: Callable[[Any], dict[str, Any] | None],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], bool]:
    """Attach the 11D-to-catalog lineage without owning catalog I/O."""
    dimensions11_match = scores["dimensions11_match"]
    if not dimensions11_match:
        products = catalog_products_for_match(None, family)
        return (products[0] if products else None), products, False

    products = catalog_products_for_match(dimensions11_match, family)
    product = (
        products[0]
        if products
        else catalog_product_for_sku(dimensions11_match.get("sku"))
    )
    evidence_pro.append(
        evidence(
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
                "source_ref": (
                    f"vkpi_kol_profile_deep:{dimensions11_match.get('profile_deep_id')}"
                ),
                "source_sheet": "dimensions_11_json.block4_specialty.product_fit",
                "source_id": dimensions11_match.get("sku"),
                "catalog_product": product or {},
                "catalog_products": products,
            },
        )
    )
    return product, products, True


def append_activity_fit_evidence(
    family_id: int,
    scores: dict[str, Any],
    context: dict[str, Any],
    evidence_pro: list[dict[str, Any]],
) -> None:
    links = context["links"]
    cooperation_count = context["cooperation_count"]
    if cooperation_count:
        evidence_pro.append(
            evidence(
                evidence_type="cooperation_depth",
                polarity="pro",
                severity="info",
                detail=f"{cooperation_count} unique historical cooperation records for this KOL",
                score_component="cooperation_depth",
                row=links[0] if links else {},
                payload=source_payload(links[0]) if links else {},
            )
        )
    market_evidence = scores["market_evidence"]
    for signal in market_evidence[:2]:
        payload = fact_payload(signal)
        evidence_pro.append(
            evidence(
                evidence_type=text(payload.get("signal_type") or signal.get("fact_type")),
                polarity="pro",
                severity="info",
                detail=market_detail(signal),
                score_component="market_activity",
                row=signal,
                payload=payload,
            )
        )
    if not market_evidence and context["official_links"].get(family_id):
        row = context["official_links"][family_id][0]
        evidence_pro.append(
            evidence(
                evidence_type="official_account_activity",
                polarity="pro",
                severity="info",
                detail="Official account published content linked to this family",
                score_component="market_activity",
                row=row,
                payload=source_payload(row),
            )
        )


def append_readiness_fit_evidence(
    scores: dict[str, Any],
    context: dict[str, Any],
    evidence_pro: list[dict[str, Any]],
    evidence_con: list[dict[str, Any]],
) -> None:
    contact_fact = context["contact_fact"]
    if context["contact_score"] > 0:
        evidence_pro.append(
            evidence(
                evidence_type="contact_available",
                polarity="pro",
                severity="info",
                detail=f"Contact availability: {context['contact_label']}",
                score_component="contact_readiness",
                row=contact_fact,
                payload=fact_payload(contact_fact) if contact_fact else {},
            )
        )
    else:
        evidence_con.append(
            evidence(
                evidence_type="contact_missing",
                polarity="con",
                severity="medium",
                detail="No usable contact status in Memory",
                score_component="contact_readiness",
                row=contact_fact,
                payload=fact_payload(contact_fact) if contact_fact else {},
            )
        )

    country_fact = context["country_fact"]
    if context["country"]:
        evidence_pro.append(
            evidence(
                evidence_type="region_relevance",
                polarity="pro",
                severity="info",
                detail=f"{context['country']} scored as {scores['region_reason']}",
                score_component="region_relevance",
                row=country_fact,
                payload=fact_payload(country_fact) if country_fact else {},
            )
        )
    else:
        evidence_con.append(
            evidence(
                evidence_type="missing_country",
                polarity="con",
                severity="low",
                detail="KOL country is missing",
                score_component="region_relevance",
                row=country_fact,
                payload=fact_payload(country_fact) if country_fact else {},
            )
        )

    evidence_fact = context["evidence_fact"]
    if context["evidence_count"]:
        evidence_pro.append(
            evidence(
                evidence_type="data_quality",
                polarity="pro",
                severity="info",
                detail=f"{context['evidence_count']} legacy evidence rows for this KOL",
                score_component="data_quality",
                row=evidence_fact,
                payload=fact_payload(evidence_fact) if evidence_fact else {},
            )
        )
    else:
        evidence_con.append(
            evidence(
                evidence_type="missing_evidence_count",
                polarity="con",
                severity="low",
                detail="No evidence_count fact found",
                score_component="data_quality",
            )
        )
