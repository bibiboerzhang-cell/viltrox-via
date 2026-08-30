"""Pure request-to-recall projections for the KOL smart-search route."""
from __future__ import annotations

import app.domains.kol.profile_recall_qualification as kol_profile_recall_qualification


def smart_url_search_response(
    body: dict,
    query_text: str,
    staff: dict,
    *,
    run_url_deep_crawl,
    url_response_status,
    smart_query_type,
) -> dict:
    """Project URL-search output while keeping route-owned effects injectable."""
    result = run_url_deep_crawl(
        {**body, "url": query_text},
        staff=staff,
        default_defer_profile=True,
        default_create_session=True,
        default_source="kol_smart_search",
    )
    return {
        "status": url_response_status(result),
        "mode": "url",
        "query_type": smart_query_type(branch="url", result=result),
        "branch": "kol_url_deep_crawl",
        "result": result,
        "search_session": result.get("search_session"),
        "provider_calls": bool(
            result.get("provider_calls_performed") or result.get("llm_calls_performed")
        ),
        "viltrox_fit_score_untouched": result.get("viltrox_fit_score_untouched"),
    }


def smart_local_recall_kwargs(
    *,
    body: dict,
    plan: dict,
    context: dict,
    recall_filters: dict,
    effective_query: str,
    recall_query: str,
    resolved_product: dict,
) -> dict:
    """Build the provider-free recall contract without mutating its inputs."""
    return {
        "query_text": effective_query,
        "product_sku": str(body.get("product_sku") or ""),
        "candidate_limit": kol_profile_recall_qualification.SMART_LOCAL_CANDIDATE_LIMIT,
        "limit": kol_profile_recall_qualification.SMART_LOCAL_TARGET,
        "creator_quota": int(body.get("creator_quota") or plan.get("creator_quota") or 15),
        "reviewer_quota": int(body.get("reviewer_quota") or plan.get("reviewer_quota") or 15),
        "ratio_policy": str(body.get("ratio_policy") or "soft"),
        "mixed_policy": str(body.get("mixed_policy") or "dominant"),
        "dedupe": True,
        "vector_weight": float(
            body.get("vector_weight")
            if body.get("vector_weight") is not None
            else kol_profile_recall_qualification.SMART_LOCAL_VECTOR_WEIGHT
        ),
        "type_weight": float(
            body.get("type_weight")
            if body.get("type_weight") is not None
            else kol_profile_recall_qualification.SMART_LOCAL_TYPE_WEIGHT
        ),
        "type_boost_enabled": bool(body.get("type_boost_enabled", True)),
        "exclude_chinese": bool(body.get("exclude_chinese", True)),
        "product_focus": plan.get("product_focus"),
        "target_persona": str(plan.get("target_persona") or ""),
        "provider_free": True,
        "filters": recall_filters,
        "search_strategy": str(body.get("search_strategy") or "balanced"),
        "bucket_policy": (
            body.get("bucket_policy")
            if isinstance(body.get("bucket_policy"), dict)
            else body.get("bucketPolicy")
            if isinstance(body.get("bucketPolicy"), dict)
            else None
        ),
        "allow_backfill": False,
        "operator_query_text": recall_query,
        "required_product_evidence_terms": (
            resolved_product if context["objective"] == "existing_evidence" else None
        ),
        "local_qualification_policy": context["local_qualification_policy"],
    }
