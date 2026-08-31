"""Attach-result builders for KOL search sessions.

Behavior-preserving move out of ``search_sessions.py``. Holds the URL / recall /
new-discovery result attachers plus their pure item-shaping helpers and the
apify-job payload linker. These build session items from orchestration results
and persist them via ``record_items`` (lazy-imported to avoid a circular import
with ``search_sessions``).

This module never writes ``viltrox_fit_score`` (no fit writes whatsoever); it
only mirrors the upstream ``viltrox_fit_score_untouched`` flags into summaries.
"""
from __future__ import annotations

import re
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.domains.kol.contact_system import project_public_profile_url
from app.domains.kol.profile_recall_activity_gate import (
    UNKNOWN_ACTIVITY_MODES,
    UNKNOWN_ACTIVITY_POLICY_KEY,
)
from app.domains.kol.profile_recall_match_evidence import query_evidence_terms, why_fit_from_match_evidence
from app.domains.kol import search_sessions_attach_jobs as _attach_jobs
from app.domains.kol.search_session_evidence_projection import (
    _looks_like_contact_value,
    _safe_match_evidence,
)
from app.domains.kol.search_sessions_targeted import (
    project_candidate_query_context,
    project_growth_candidate_context,
    project_targeted_plan,
)
from app.domains.kol.search_sessions_qualification_projection import project_local_qualification

from app.domains.kol.search_sessions_serde import (
    _compact_flow,
    _dict,
    _float_or_none,
    _int_or_none,
    _json_dumps,
    _list,
    _loads,
    _normalize_status,
    _text,
)


logger = get_logger(__name__)

# Confirmed from the real apify_jobs table.  Keep this contract narrow so an
# unknown future state cannot accidentally terminalize a live search session.
_TERMINAL_LINKED_JOB_STATUSES = frozenset({"done", "failed", "blocked", "triage"})
_FACET_NAMES = ("platform", "country", "language", "profile_type", "contact_available", "video_evidence")
_PLAN_STATUS_VALUES = frozenset({"ready", "fallback", "needs_clarification"})
_PLAN_CODE_RE = re.compile(r"^[a-zA-Z0-9_.:/-]{1,120}$")


def _safe_candidate_facets(value: Any) -> dict[str, str]:
    raw = _dict(value)
    output: dict[str, str] = {}
    for name in _FACET_NAMES:
        text = _text(raw.get(name)).lower()[:40]
        if name in {"contact_available", "video_evidence"}:
            if text in {"yes", "no", "unknown"}:
                output[name] = text
        elif text and re.fullmatch(r"[a-z][a-z0-9 _-]{0,39}|unknown", text):
            output[name] = text
    return output


def _safe_non_negative_int(value: Any, *, maximum: int = 1_000_000) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if 0 <= parsed <= maximum else None


def _safe_non_negative_float(value: Any, *, maximum: float = 1_000_000.0) -> float | None:
    parsed = _float_or_none(value)
    return parsed if parsed is not None and 0 <= parsed <= maximum else None


def _safe_public_code(value: Any, *, limit: int = 160) -> str:
    text = _text(value).strip()[:limit]
    if not text or _looks_like_contact_value(text):
        return ""
    return text if _PLAN_CODE_RE.fullmatch(text) else ""


def _safe_gate_evidence(
    value: Any,
    *,
    allowed_terms: set[str],
    controlled_specs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Persist the Smart-local proof, never arbitrary/contact-bearing payload fields."""
    raw = _dict(value)
    if _text(raw.get("schema")) != "smart_local_gate_evidence_v2":
        return {}
    output: dict[str, Any] = {
        "schema": "smart_local_gate_evidence_v2",
        "passed": raw.get("passed") is True,
        # 「活跃度未知·待补抓」是判定结果的一部分,不是可丢的装饰:丢了它,
        # 回放里这一行就与「陈旧被拒」长得一模一样,界面也就没得如实显示。
        "deferred": raw.get("deferred") is True,
        "deferred_reason": _safe_public_code(raw.get("deferred_reason"), limit=80) or None,
        "rejection_reasons": [
            reason
            for entry in _list(raw.get("rejection_reasons"))[:12]
            if (reason := _safe_public_code(entry, limit=80))
        ],
    }
    kol_pool_id = _int_or_none(raw.get("kol_pool_id"))
    if kol_pool_id:
        output["kol_pool_id"] = kol_pool_id
    fingerprint = _safe_public_code(raw.get("canonical_fingerprint"), limit=64)
    if re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        output["canonical_fingerprint"] = fingerprint
    snapshot_id = _safe_public_code(raw.get("snapshot_id"), limit=64)
    if snapshot_id:
        output["snapshot_id"] = snapshot_id
    for binding_key, maximum in (
        ("snapshot_revision", 1_000_000),
        ("server_rank", 30),
        ("global_unique_rank", 60),
    ):
        binding_value = _safe_non_negative_int(raw.get(binding_key), maximum=maximum)
        if binding_value is not None:
            output[binding_key] = binding_value

    account_quality = _dict(raw.get("account_quality"))
    output["account_quality"] = {
        "verdict": _safe_public_code(account_quality.get("verdict"), limit=80),
        "excluded_types": [
            item
            for entry in _list(account_quality.get("excluded_types"))[:12]
            if (item := _safe_public_code(entry, limit=80))
        ],
        "passed": account_quality.get("passed") is True,
        "source": _safe_public_code(account_quality.get("source"), limit=120),
    }

    followers = _dict(raw.get("followers"))
    follower_value = _safe_non_negative_int(followers.get("value"), maximum=5_000_000_000)
    follower_minimum = _safe_non_negative_int(followers.get("minimum"), maximum=5_000_000_000)
    follower_maximum = _safe_non_negative_int(followers.get("maximum"), maximum=5_000_000_000)
    output["followers"] = {
        "value": follower_value,
        "minimum": follower_minimum,
        "maximum": follower_maximum,
        "known": followers.get("known") is True,
        "filter_requested": followers.get("filter_requested") is True,
        "filter_source": _safe_public_code(followers.get("filter_source"), limit=80),
        "unknown_policy": _safe_public_code(followers.get("unknown_policy"), limit=40),
        "status": _safe_public_code(followers.get("status"), limit=40),
        "reason": _safe_public_code(followers.get("reason"), limit=80) or None,
        "passed": followers.get("passed") is True,
        "source": _safe_public_code(followers.get("source")),
    }

    activity = _dict(raw.get("activity"))
    posted_at = _text(activity.get("posted_at")).strip()[:40]
    if posted_at and not re.fullmatch(r"[0-9T:+.Z-]{8,40}", posted_at):
        posted_at = ""
    output["activity"] = {
        "posted_at": posted_at or None,
        "age_days": _safe_non_negative_float(activity.get("age_days"), maximum=10_000),
        "fresh_priority": activity.get("fresh_priority") is True,
        "maximum_age_days": _safe_non_negative_int(activity.get("maximum_age_days"), maximum=3650),
        "identity_kind": _safe_public_code(activity.get("identity_kind"), limit=40) or None,
        "identity_present": bool(_text(activity.get("identity"))),
        "passed": activity.get("passed") is True,
        # known=False 是「从没抓过」,与 passed=False 的「抓过但不合格」是两件事。
        "known": activity.get("known") is True,
        "deferred": activity.get("deferred") is True,
        "status": _safe_public_code(activity.get("status"), limit=80),
        "deferred_reason": _safe_public_code(activity.get("deferred_reason"), limit=80) or None,
        "source": _safe_public_code(activity.get("source")),
    }

    market = _dict(raw.get("market"))
    output["market"] = {
        "value": _safe_public_code(market.get("value"), limit=40) or None,
        "target": _safe_public_code(market.get("target"), limit=40) or None,
        "method": _safe_public_code(market.get("method"), limit=80),
        "confidence": _safe_non_negative_float(market.get("confidence"), maximum=1.0),
        "source": _safe_public_code(market.get("source"), limit=120) or None,
        "rejected_source": _safe_public_code(market.get("rejected_source"), limit=120) or None,
        "passed": market.get("passed") is True,
    }

    for field_name in ("language", "profile_type"):
        facet = _dict(raw.get(field_name))
        output[field_name] = {
            "values": [
                item
                for entry in _list(facet.get("values"))[:12]
                if (item := _safe_public_code(entry, limit=40))
            ],
            "targets": [
                item
                for entry in _list(facet.get("targets"))[:12]
                if (item := _safe_public_code(entry, limit=40))
            ],
            "filter_requested": facet.get("filter_requested") is True,
            "invalid_targets": [
                item
                for entry in _list(facet.get("invalid_targets"))[:12]
                if (item := _safe_public_code(entry, limit=80))
            ],
            "passed": facet.get("passed") is True,
            "source": _safe_public_code(facet.get("source"), limit=120),
        }

    platform = _dict(raw.get("platform"))
    output["platform"] = {
        "value": _safe_public_code(platform.get("value"), limit=40) or None,
        "targets": [
            target
            for entry in _list(platform.get("targets"))[:8]
            if (target := _safe_public_code(entry, limit=40))
        ],
        "passed": platform.get("passed") is True,
        "source": _safe_public_code(platform.get("source"), limit=120),
    }

    relevance = _dict(raw.get("relevance"))
    safe_relevance_evidence = _safe_match_evidence(
        relevance.get("evidence"),
        allowed_terms=allowed_terms,
        controlled_specs=controlled_specs,
    )
    relevance_passed = relevance.get("passed") is True and bool(safe_relevance_evidence)
    output["relevance"] = {
        "passed": relevance_passed,
        "evidence": safe_relevance_evidence,
        "source": _safe_public_code(relevance.get("source"), limit=120),
    }
    if relevance.get("passed") is True and not safe_relevance_evidence:
        if "low_relevance" not in output["rejection_reasons"]:
            output["rejection_reasons"].append("low_relevance")
    output["passed"] = bool(
        raw.get("passed") is True
        and not output["rejection_reasons"]
        and all(
            _dict(output.get(field)).get("passed") is True
            for field in (
                "account_quality", "followers", "activity", "market",
                "language", "profile_type", "platform", "relevance",
            )
        )
    )
    return output


def _safe_local_qualification(value: Any) -> dict[str, Any]:
    """Compact aggregate Smart-local contract for polling/history replay."""
    return project_local_qualification(
        value,
        text_value=_text,
        dict_value=_dict,
        list_value=_list,
        safe_int=_safe_non_negative_int,
        safe_float=_safe_non_negative_float,
        safe_code=_safe_public_code,
        unknown_policy_key=UNKNOWN_ACTIVITY_POLICY_KEY,
        unknown_modes=UNKNOWN_ACTIVITY_MODES,
    )


def _safe_candidate_distribution(value: Any) -> dict[str, Any]:
    raw = _dict(value)
    if raw.get("claim_status") != "descriptive_only":
        return {}
    denominator = _int_or_none(raw.get("denominator")) if raw.get("denominator") else 0
    if denominator is None or denominator > 30:
        return {}
    facets: dict[str, dict[str, int]] = {}
    raw_facets = _dict(raw.get("facets"))
    for name in _FACET_NAMES:
        counts: dict[str, int] = {}
        for label, count in list(_dict(raw_facets.get(name)).items())[:32]:
            safe = _safe_candidate_facets({name: label}).get(name)
            try:
                number = int(count)
            except (TypeError, ValueError):
                continue
            if safe and 0 <= number <= denominator:
                counts[safe] = number
        if sum(counts.values()) != denominator:
            return {}
        facets[name] = counts
    return {
        "claim_status": "descriptive_only",
        "denominator": denominator,
        "denominator_definition": "returned_canonical_candidates",
        "facets": facets,
    }


def _safe_plan_text(value: Any, *, limit: int) -> str:
    """Return bounded UI copy, rejecting contact-like or control-bearing values."""
    text = _text(value).strip()
    if not text or _looks_like_contact_value(text) or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", text):
        return ""
    return text[:limit]


def _safe_plan_list(value: Any, *, limit: int = 10, item_limit: int = 120) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in _list(value)[:limit]:
        text = _safe_plan_text(raw, limit=item_limit)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            output.append(text)
    return output


def _safe_plan_code(value: Any) -> str:
    text = _text(value).strip()
    return text if _PLAN_CODE_RE.fullmatch(text) else ""


def _safe_llm_query_plan(value: Any) -> dict[str, Any]:
    """Persist only the bounded, public plan projection needed for history replay."""
    raw = _dict(value)
    if not raw:
        return {}
    output: dict[str, Any] = project_targeted_plan(raw)
    status = _text(raw.get("status")).lower()
    if status in _PLAN_STATUS_VALUES:
        output["status"] = status

    for name, limit in (
        ("search_query", 500),
        ("target_persona", 1000),
        ("product_positioning", 1000),
        ("market", 80),
    ):
        text = _safe_plan_text(raw.get(name), limit=limit)
        if text:
            output[name] = text
    for name, count in (("product_focus", 10), ("avoid_types", 8), ("platforms", 8)):
        values = _safe_plan_list(raw.get(name), limit=count)
        if values:
            output[name] = values
    for name, lower, upper in (
        ("creator_quota", 0, 50),
        ("reviewer_quota", 0, 50),
        ("new_discovery_limit", 0, 50),
    ):
        try:
            number = int(raw.get(name))
        except (TypeError, ValueError):
            number = None
        if number is not None and lower <= number <= upper:
            output[name] = number
    for name in ("include_new_discovery", "fallback_used", "provider_calls_performed"):
        if isinstance(raw.get(name), bool):
            output[name] = raw[name]
    # plan_cache:规划器唯一的缓存留痕(命中 7 天缓存置 "hit",未命中不带此键)。
    # 此前不在白名单里被整个丢掉,「plan 缓存命中过几次」在会话历史里答不出来。
    for name in ("reason", "provider", "model", "persona_source", "plan_cache"):
        code = _safe_plan_code(raw.get(name))
        if code:
            output[name] = code

    product = _dict(raw.get("resolved_product"))
    safe_product: dict[str, Any] = {}
    for name in ("sku", "model_name", "marketing_name", "category_main", "series"):
        text = _safe_plan_text(product.get(name), limit=240)
        if text:
            safe_product[name] = text
    price = _float_or_none(product.get("price_usd"))
    if price is not None and 0 <= price <= 1_000_000:
        safe_product["price_usd"] = price
    if safe_product:
        output["resolved_product"] = safe_product

    clarification = _dict(raw.get("clarification"))
    safe_clarification: dict[str, Any] = {}
    for name, limit in (
        ("reason", 120),
        ("requested_series", 80),
        ("requested_model_code", 120),
        ("requested_mount", 80),
        ("message", 500),
    ):
        text = _safe_plan_text(clarification.get(name), limit=limit)
        if text:
            safe_clarification[name] = text
    focals = [
        number for raw_number in _list(clarification.get("requested_focals"))[:8]
        if (number := _int_or_none(raw_number)) is not None and 1 <= number <= 1000
    ]
    if focals:
        safe_clarification["requested_focals"] = focals
    suggestions: list[dict[str, str]] = []
    for suggestion in _list(clarification.get("suggestions"))[:6]:
        row = _dict(suggestion)
        safe_row = {
            name: text
            for name in ("sku", "name", "mount", "series")
            if (text := _safe_plan_text(row.get(name), limit=240))
        }
        if safe_row:
            suggestions.append(safe_row)
    if suggestions:
        safe_clarification["suggestions"] = suggestions
    if safe_clarification:
        output["clarification"] = safe_clarification
    return output


from app.domains.kol.search_sessions_recall_fields import (
    _RECALL_SESSION_PAYLOAD_SCHEMA,
    _RECALL_SESSION_PAYLOAD_FIELDS,
    _RECALL_SESSION_SOURCE_FIELDS,
)


def _recall_source_items(result: dict[str, Any]) -> tuple[list[dict[str, Any]], str, int]:
    """Return replay rows in the server's canonical order.

    New responses carry ``items`` in the exact selected/ranked order.  Older
    callers only supplied buckets; keep those readable, include every bucket,
    and mark the resulting replay as legacy/incomplete because the original
    cross-bucket order cannot be recovered.
    """

    buckets = _dict(result.get("buckets"))
    if isinstance(result.get("items"), list):
        raw_items = result.get("items") or []
        # Rolling-upgrade responses may expose an empty compatibility ``items``
        # list while the real legacy rows still live in buckets.  Only accept an
        # empty list as canonical when the buckets are empty as well.
        has_bucket_rows = any(bool(_list(value)) for value in buckets.values())
        if raw_items or not has_bucket_rows:
            return [dict(raw) for raw in raw_items if isinstance(raw, dict)], "canonical_items", len(raw_items)

    names = [name for name in ("creator", "reviewer", "unknown") if name in buckets]
    names.extend(name for name in buckets if name not in names)
    rows: list[dict[str, Any]] = []
    source_count = 0
    for bucket_name in names:
        bucket_rows = _list(buckets.get(bucket_name))
        source_count += len(bucket_rows)
        for raw in bucket_rows:
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            row.setdefault("bucket", bucket_name)
            rows.append(row)
    return rows, "legacy_buckets", source_count


def _first_recall_score(raw: dict[str, Any]) -> float | None:
    """Choose the persisted summary score without converting missing to zero."""

    for key in (
        "robust_rank_score",
        "display_rank_score",
        "recall_rank_score",
        "retrieval_score",
        "vector_score",
    ):
        value = raw.get(key)
        if value in (None, ""):
            continue
        parsed = _float_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _recall_session_payload(
    raw: dict[str, Any],
    *,
    bucket: str,
    replay_complete: bool,
    replay_source: str,
) -> dict[str, Any]:
    payload = {key: raw.get(key) for key in _RECALL_SESSION_PAYLOAD_FIELDS if key in raw}
    if "profile_url" in payload:
        payload["profile_url"] = project_public_profile_url(payload.get("profile_url"))
    source_fields = _dict(raw.get("source_fields"))
    safe_source_fields = {
        key: source_fields.get(key)
        for key in _RECALL_SESSION_SOURCE_FIELDS
        if key in source_fields
    }
    if safe_source_fields:
        payload["source_fields"] = safe_source_fields
    payload.update(project_candidate_query_context(raw))
    payload.update(project_growth_candidate_context(raw))
    payload.update(
        {
            "bucket": bucket,
            "session_payload_schema": _RECALL_SESSION_PAYLOAD_SCHEMA,
            "session_replay_complete": bool(replay_complete),
            "session_replay_source": replay_source,
        }
    )
    return payload


def attach_url_result(session_id: int, result: dict[str, Any]) -> dict[str, Any]:
    from app.domains.kol.search_sessions import record_items

    item = _url_result_item(int(session_id), result)
    session_status = _session_status_from_url_result(result)
    summary = {
        "kind": "url_deep_crawl",
        "url_type": result.get("url_type"),
        "platform": result.get("platform"),
        "execute": bool(result.get("execute")),
        "in_pool": bool(result.get("in_pool")),
        "matched_kol_pool_id": result.get("matched_kol_pool_id"),
        "item_status": item.get("status"),
        "viltrox_fit_score_untouched": result.get("viltrox_fit_score_untouched"),
    }
    # 已解析出的创作者/视频信息随摘要落库(紧凑公开字段),历史列表不用翻 items 也有据可查。
    video_flow = _dict(result.get("video_flow"))
    identity = _dict(result.get("creator_identity") or video_flow.get("creator_identity"))
    if any(_text(identity.get(key)) for key in ("handle", "channel_id", "display_name")):
        summary["creator_identity"] = {
            "platform": identity.get("platform"),
            "handle": identity.get("handle"),
            "channel_id": identity.get("channel_id"),
            "display_name": identity.get("display_name"),
        }
    metadata = _dict(result.get("video_metadata") or video_flow.get("video_metadata"))
    if _text(metadata.get("title")) or _text(metadata.get("channel_name")):
        summary["video_title"] = metadata.get("title")
        summary["video_channel"] = metadata.get("channel_name")
    if result.get("url_type") not in {"profile", "video"}:
        summary["message"] = "暂不支持该平台的链接，目前支持 YouTube / Instagram / TikTok。"
    recorded = record_items(int(session_id), [item], status=session_status, summary=summary)
    recorded["jobs_linked"] = _link_job_payloads(int(session_id), recorded.get("items") or [])
    return recorded


def attach_recall_result(session_id: int, result: dict[str, Any]) -> dict[str, Any]:
    from app.domains.kol.search_sessions import record_items

    # Smart-local results may be attached by internal callers as well as the
    # HTTP/worker orchestration.  Re-apply its boundary projection here so raw
    # profile/contact values cannot enter durable session history.
    local_contract = _dict(result.get("local_qualification"))
    if _text(local_contract.get("schema")) == "smart_local_qualified_v1":
        from app.domains.kol.profile_recall_qualification import project_smart_local_result

        result = project_smart_local_result(result)
        local_contract = _dict(result.get("local_qualification"))

    items: list[dict[str, Any]] = []
    query = _dict(result.get("query"))
    query_text = _text(query.get("query_text") or result.get("query"))
    allowed_terms = set(query_evidence_terms(query_text))
    required_product_terms = _list(query.get("required_product_evidence_terms"))
    allowed_terms.update(query_evidence_terms(" ".join(_text(term) for term in required_product_terms)))
    source_items, replay_source, source_count = _recall_source_items(result)
    replay_complete = replay_source == "canonical_items" and len(source_items) == source_count
    for server_rank, raw in enumerate(source_items, start=1):
        bucket_name = _text(raw.get("bucket")) or "unknown"
        kol_pool_id = _int_or_none(
            raw.get("kol_pool_id") if raw.get("kol_pool_id") is not None else raw.get("id")
        )
        source_url = project_public_profile_url(raw.get("profile_url") or raw.get("url"))
        payload = _recall_session_payload(
            raw,
            bucket=bucket_name,
            replay_complete=replay_complete,
            replay_source=replay_source,
        )
        # A stored explanation must be reproducible from the exact bounded
        # evidence stored beside it; never persist a free-form upstream reason.
        legacy_why_fit = payload.pop("why_fit", None)
        payload.pop("evidence", None)
        query_context = project_candidate_query_context(raw)
        item_allowed_terms = set(allowed_terms)
        item_allowed_terms.update(query_evidence_terms(query_context.get("query_cell_query")))
        for cell in _list(query_context.get("matched_query_cells")):
            item_allowed_terms.update(query_evidence_terms(_dict(cell).get("primary_query")))
        controlled_specs = [
            spec
            for cell in _list(query_context.get("matched_query_cells"))
            if isinstance(cell, dict)
            and isinstance((spec := cell.get("locked_term_groups")), dict)
        ]
        match_evidence = _safe_match_evidence(
            raw.get("match_evidence"),
            allowed_terms=item_allowed_terms,
            controlled_specs=controlled_specs,
        )
        candidate_facets = _safe_candidate_facets(raw.get("candidate_facets"))
        qualification_evidence = _safe_gate_evidence(
            raw.get("qualification_evidence"),
            allowed_terms=item_allowed_terms,
            controlled_specs=controlled_specs,
        )
        payload["server_rank"] = server_rank
        payload["global_rank"] = server_rank
        if match_evidence:
            payload["match_evidence"] = match_evidence
            payload["why_fit"] = why_fit_from_match_evidence(match_evidence)
        elif safe_why_fit := _safe_plan_text(legacy_why_fit, limit=1000):
            payload["why_fit"] = safe_why_fit
        if candidate_facets:
            payload["candidate_facets"] = candidate_facets
        if qualification_evidence:
            payload["qualification_evidence"] = qualification_evidence
        items.append(
            {
                "dedupe_key": f"recall:{kol_pool_id or source_url or server_rank}",
                "item_type": "recall_candidate",
                "status": "matched",
                "stage": "identified",
                "rank": server_rank,
                "score": _first_recall_score(raw),
                "kol_pool_id": kol_pool_id,
                "source_url": source_url,
                "payload": payload,
            }
        )
    pipeline_running = bool(result.get("_session_pipeline_running"))
    pipeline_progress = _dict(result.get("_session_progress"))
    summary = {
        "_authoritative_snapshot_lane": "recall",
        "kind": "kol_recall",
        "method": result.get("method"),
        "recall_snapshot_attached": True,
        "recall_snapshot_complete": True,
        "items_written": len(items),
        "diagnostics": result.get("diagnostics"),
        "query": result.get("query"),
        "ratio": result.get("ratio"),
        "filters": result.get("filters"),
        "bucket_policy": result.get("bucket_policy"),
        "ranking": result.get("ranking"),
        "evaluation_status": result.get("evaluation_status"),
        "replay_contract": {
            "schema": _RECALL_SESSION_PAYLOAD_SCHEMA,
            "source": replay_source,
            "complete": replay_complete,
            "source_count": source_count,
            "persisted_count": len(items),
            "missing_count": max(0, source_count - len(items)),
        },
    }
    match_status = _text(result.get("match_status")).lower()
    distribution = _safe_candidate_distribution(result.get("candidate_set_distribution"))
    llm_query_plan = _safe_llm_query_plan(result.get("llm_query_plan"))
    local_qualification = _safe_local_qualification(local_contract)
    if match_status in {"matched", "empty"}:
        summary["match_status"] = match_status
    if distribution:
        summary["candidate_set_distribution"] = distribution
    if llm_query_plan:
        summary["llm_query_plan"] = llm_query_plan
    if local_qualification:
        summary["local_qualification"] = local_qualification
    if pipeline_running:
        summary.update({"phase": "base", "progress": pipeline_progress})
    return record_items(
        int(session_id),
        items,
        status="running" if pipeline_running else "ready",
        summary=summary,
    )
def _discovery_enrichment_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """发现项富化字段 → 会话项 payload(纯透传,零评分触碰)。

    followers:subscriber_count/follower_count 等族名归一,仅 >0 才带键(隐藏订阅数/未知诚实缺席);
    bio:≤500 字;channel_id:YT UC id(池行 handle 口径);fast_path:True 时才带键,读端触达判据
    据此豁免「views/comments 填充 0」的互动全零误判(与发现侧 _reach_floor_reason 同一口径)。
    """
    out: dict[str, Any] = {}
    followers = _int_or_none(
        raw.get("followers") or raw.get("subscriber_count") or raw.get("follower_count") or raw.get("subscribers")
    )
    if followers:
        out["followers"] = followers
    bio = _text(raw.get("bio"))
    if bio:
        out["bio"] = bio[:500]
    channel_id = _text(raw.get("channel_id"))
    if channel_id:
        out["channel_id"] = channel_id[:120]
    if raw.get("fast_path"):
        out["fast_path"] = True
    return out


def attach_new_discovery_result(session_id: int, result: dict[str, Any]) -> dict[str, Any]:
    """Attach platform-discovery candidates to an existing smart-search session."""
    from app.domains.kol.search_sessions import get_session, record_items

    items: list[dict[str, Any]] = []
    rank = 1
    # 重复卡修(2026-07-21):同批内按归一身份键(小写)去重——多路检索变体合并/大小写差异
    # 不再产出重复会话项(DB 的 dedupe_key upsert 是最后防线,这里保证返回列表本身无重复行)。
    seen_batch_keys: set[str] = set()
    for raw in _list(result.get("existing_matches")):
        if not isinstance(raw, dict):
            continue
        kol_pool_id = _int_or_none(raw.get("history_kol_pool_id") or _dict(raw.get("historical_match")).get("kol_pool_id"))
        source_url = project_public_profile_url(raw.get("channel_url") or raw.get("source_url"))
        batch_key = f"existing:{kol_pool_id or source_url.lower() or rank}"
        if batch_key in seen_batch_keys:
            continue
        seen_batch_keys.add(batch_key)
        items.append(
            {
                "dedupe_key": f"existing:{kol_pool_id or source_url or rank}",
                "item_type": "existing_kol",
                "status": "matched",
                "stage": "identified",
                "rank": rank,
                "score": _float_or_none(raw.get("history_match_confidence") or _dict(raw.get("historical_match")).get("match_confidence")),
                "kol_pool_id": kol_pool_id,
                "source_url": source_url,
                "payload": {
                    "source": "platform_discovery",
                    "platform": raw.get("platform"),
                    "handle": raw.get("handle"),
                    "channel_name": raw.get("channel_name"),
                    "sample_title": raw.get("sample_title"),
                    "source_url": source_url,
                    "channel_url": source_url,
                    "avatar_url": raw.get("avatar_url"),
                    "historical_match": raw.get("historical_match"),
                },
            }
        )
        rank += 1
    for raw in _list(result.get("new_creators")):
        if not isinstance(raw, dict):
            continue
        source_url = project_public_profile_url(raw.get("channel_url") or raw.get("source_url"))
        handle = _text(raw.get("handle") or raw.get("channel_name"))
        platform = _text(raw.get("platform") or (result.get("platforms") or [""])[0])
        batch_key = f"new:{platform.lower()}:{handle.lstrip('@').lower() or source_url.lower() or rank}"
        if batch_key in seen_batch_keys:
            continue
        seen_batch_keys.add(batch_key)
        items.append(
            {
                "dedupe_key": f"new:{platform}:{handle or source_url or rank}",
                "item_type": "new_creator",
                "status": "identified",
                "stage": "identified",
                "rank": rank,
                "score": _float_or_none(raw.get("score") or raw.get("relevance_score") or raw.get("vector_score")),
                "source_url": source_url,
                "payload": {
                    "source": "platform_discovery",
                    "platform": platform,
                    "handle": raw.get("handle"),
                    "channel_name": raw.get("channel_name"),
                    "sample_title": raw.get("sample_title"),
                    "source_url": source_url,
                    "channel_url": source_url,
                    "avatar_url": raw.get("avatar_url"),
                    "thumbnail_url": raw.get("thumbnail_url"),
                    "views": raw.get("views"),
                    "likes": raw.get("likes"),
                    "comments": raw.get("comments"),
                    "avg_views": raw.get("avg_views"),
                    "published": raw.get("published"),
                    "search_query": raw.get("search_query") or result.get("query"),
                    "market": raw.get("market") or result.get("market"),
                    # 发现富化契约(2026-08-22 会话 1106 案):发现侧补齐的 followers/bio/channel_id/
                    # fast_path 此前只进 pool 行不进会话项 → 读端回落 payload 误判(详见 helper)。
                    **_discovery_enrichment_payload(raw),
                    # 独立展示信号(绝不并入 viltrox_fit_score):persona 相关度 + 可解释命中。
                    "relevance_score": raw.get("relevance_score"),
                    "relevance_tier": raw.get("relevance_tier"),
                    "relevance_hits": raw.get("relevance_hits"),
                    # 触达三态(2026-07-12 第二道闸):analyzing=followers 未知、已入库点火补全,
                    # 读端(get_session 展示闸)折叠为「分析中 ×N」;仅观测透传,展示以读端实时判据为准。
                    "reach_status": raw.get("reach_status"),
                },
            }
        )
        rank += 1

    existing_summary: dict[str, Any] = {}
    try:
        existing_summary = _dict(get_session(int(session_id)).get("result_summary"))
    except Exception:
        existing_summary = {}
    pipeline_running = bool(result.get("_session_pipeline_running"))
    pipeline_progress = _dict(result.get("_session_progress"))
    discovery_summary = {
        "kind": "platform_discovery",
        "query": result.get("query"),
        "status": result.get("status"),
        "platforms": result.get("platforms"),
        "counts": result.get("counts"),
        "provider_calls": result.get("provider_calls"),
        "platform_results": result.get("platform_results"),
        "errors": result.get("errors"),
        "viltrox_fit_score_untouched": True,
    }
    summary = {
        **existing_summary,
        "new_discovery": discovery_summary,
    }
    if pipeline_running:
        summary.update({"phase": "base", "progress": pipeline_progress})
    status = "running" if pipeline_running else "ready"
    if not pipeline_running and result.get("status") in {"partial", "failed"}:
        status = "partial"
    recorded = record_items(int(session_id), items, status=status, summary=summary)
    recorded["new_discovery"] = discovery_summary
    return recorded


def _session_status_from_url_result(result: dict[str, Any]) -> str:
    if not result.get("execute"):
        return "ready"
    video_flow = _dict(result.get("video_flow"))
    profile_flow = _dict(result.get("profile_flow"))
    status = _text(video_flow.get("status") or profile_flow.get("status") or result.get("status")).lower()
    if status == "queued":
        return "running"
    if status in {"already_queued"}:
        return "running"
    if status in {"already_analyzed", "ready", "ai_disabled", "not_requested", "official_channel_video", "cn_platform_video"}:
        return "ready"
    if status in {"failed", "creator_unresolved", "profile_crawl_failed", "crawl_failed"}:
        return "failed"
    return "partial" if result.get("execute") else "ready"


def _url_result_item(session_id: int, result: dict[str, Any]) -> dict[str, Any]:
    return _attach_jobs.url_result_item(
        session_id,
        result,
        dict_value=_dict,
        int_or_none=_int_or_none,
        text=_text,
        normalize_status=_normalize_status,
        compact_flow=_compact_flow,
    )


def _link_job_payloads(session_id: int, items: list[dict[str, Any]]) -> int:
    return _attach_jobs.link_job_payloads(
        session_id,
        items,
        get_connection=get_conn,
        postgres_runtime=is_postgres_runtime,
        int_or_none=_int_or_none,
        text=_text,
        loads=_loads,
        json_dumps=_json_dumps,
        terminal_statuses=_TERMINAL_LINKED_JOB_STATUSES,
        sync_linked_terminal_job=_sync_linked_terminal_job,
    )


def _sync_linked_terminal_job(
    conn: Any,
    *,
    job_id: int,
    status: str,
    last_error: str = "",
) -> bool:
    return _attach_jobs.sync_linked_terminal_job(
        conn,
        job_id=job_id,
        status=status,
        last_error=last_error,
    )
