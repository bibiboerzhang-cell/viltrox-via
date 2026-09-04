"""Seven-day cache boundary for smart-query plans."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable

from app.core.coerce import _text
from app.domains.kol import smart_query_facets, targeted_search_contract


_ORIGIN_DIAGNOSTIC_KEYS = (
    "provider_calls_performed",
    "provider_response_succeeded",
    "provider_attempts",
    "provider_response_status",
    "planner_parse_status",
    "planner_parse_failed",
    "gateway_cache_hit",
    "gateway_cache_key",
    "gateway_cache_origin_call_uid",
)

_CACHE_UPSERT_SQL = """
    INSERT INTO vkpi_analysis_cache (
      target_type, target_id, model, derive_method, result, cost,
      status, created_at, updated_at
    )
    VALUES (?, ?, ?, ?, ?::jsonb, ?, 'ready', NOW(), NOW())
    ON CONFLICT (target_type, target_id, derive_method)
    DO UPDATE SET result = EXCLUDED.result, status = 'ready', updated_at = NOW()
"""


def _cache_contract(query: str, body: dict[str, Any]) -> dict[str, Any]:
    filters = body.get("filters") if isinstance(body.get("filters"), dict) else {}
    return {
        "product_sku": _text(body.get("product_sku") or body.get("productSku")).lower(),
        "objective": targeted_search_contract.normalize_objective(body),
        "segments": [
            item["key"]
            for item in targeted_search_contract.extract_explicit_segments(query, body)
        ],
        "follower_filter": targeted_search_contract.parse_follower_range(query, body),
        "platforms": body.get("platforms") or filters.get("platforms") or [],
        "countries": body.get("countries") or filters.get("countries") or [],
        "languages": body.get("languages") or filters.get("languages") or [],
        "use_product_persona": body.get("use_product_persona", "true"),
        "use_llm_planner": body.get("use_llm_planner", "true"),
        "llm_provider": body.get("llm_provider") or "",
    }


def plan_cache_key(query: str, body: dict[str, Any]) -> str:
    cache_identity = "|".join((
        _text(query).strip().lower(),
        json.dumps(
            _cache_contract(query, body),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    ))
    return hashlib.md5(cache_identity.encode("utf-8")).hexdigest()


def _fresh_cache_result(entry: Any) -> Any:
    if not entry or entry.get("status") != "ready":
        return None
    updated_at = str(entry.get("updated_at") or "")
    timestamp = datetime.fromisoformat(updated_at.replace("Z", "+00:00")) if updated_at else None
    if timestamp is not None and timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    if not timestamp or (datetime.now(timezone.utc) - timestamp).total_seconds() >= 7 * 86400:
        return None
    result = entry.get("result")
    return json.loads(result) if isinstance(result, str) else result


def _mark_current_request_cache_truth(plan: dict[str, Any]) -> None:
    plan["plan_cache_origin_diagnostics"] = {
        key: plan.get(key)
        for key in _ORIGIN_DIAGNOSTIC_KEYS
        if key in plan
    }
    plan.update({
        "plan_cache": "hit",
        "provider_calls_performed": False,
        "provider_response_succeeded": False,
        "provider_attempts": 0,
        "provider_response_status": "plan_cache_hit",
        "planner_parse_status": "cached_valid",
        "planner_parse_failed": False,
        "gateway_cache_hit": False,
        "gateway_cache_key": "",
        "gateway_cache_origin_call_uid": "",
    })


def _prepare_cached_plan(
    plan: Any,
    *,
    query: str,
    body: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(plan, dict) or not plan.get("search_query"):
        return None
    plan = targeted_search_contract.apply_targeted_contract(
        plan,
        query=query,
        body=body,
        product=plan.get("resolved_product"),
    )
    _mark_current_request_cache_truth(plan)
    if not isinstance(plan.get("filter_proposal"), dict):
        plan["filter_proposal"] = smart_query_facets.propose_facets(query, plan)
    return plan


def _read_cached_plan(
    query: str,
    body: dict[str, Any],
    *,
    cache_key: str,
    derive_method: str,
) -> dict[str, Any] | None:
    from app.domains.analysis.cache_repo import get_analysis_cache_entry

    entry = get_analysis_cache_entry(
        "search_plan", cache_key, derive_method=derive_method
    )
    return _prepare_cached_plan(
        _fresh_cache_result(entry),
        query=query,
        body=body,
    )


def _write_cached_plan(
    plan: Any,
    *,
    cache_key: str,
    derive_method: str,
) -> None:
    if not (
        isinstance(plan, dict)
        and plan.get("search_query")
        and not plan.get("fallback_used")
    ):
        return
    from app.db.connection import get_conn

    connection = get_conn()
    connection.execute(
        _CACHE_UPSERT_SQL,
        (
            "search_plan", cache_key, "plan_cache", derive_method,
            json.dumps(plan, ensure_ascii=False), 0,
        ),
    )
    connection.commit()


def plan_text_query_cached(
    query: str,
    *,
    body: dict[str, Any],
    staff: dict[str, Any] | None,
    derive_method: str,
    build_plan: Callable[..., dict[str, Any]],
    logger: Any,
) -> dict[str, Any]:
    """Read, build, and best-effort persist one query plan."""

    cache_key = plan_cache_key(query, body)
    try:
        cached = _read_cached_plan(
            query, body, cache_key=cache_key, derive_method=derive_method
        )
        if cached is not None:
            return cached
    except Exception:
        logger.debug("plan 缓存读取失败,走实时规划(best-effort)", exc_info=True)
    plan = build_plan(query, body=body, staff=staff)
    try:
        _write_cached_plan(
            plan, cache_key=cache_key, derive_method=derive_method
        )
    except Exception:
        logger.debug("plan 缓存写入失败(best-effort,不影响返回)", exc_info=True)
    return plan
