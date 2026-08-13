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
from app.domains.kol.profile_recall_match_evidence import query_evidence_terms, why_fit_from_match_evidence
from app.domains.tasks.search_session_lineage import with_search_session_lineage

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
_MATCH_EVIDENCE_FIELDS = frozenset({
    "handle", "display_name", "bio", "primary_topic", "content_style",
    "secondary_topics_json", "profile_text", "type_reason", "representative_evidence.title",
})
_FACET_NAMES = ("platform", "country", "language", "profile_type", "contact_available", "video_evidence")
_PLAN_STATUS_VALUES = frozenset({"ready", "fallback", "needs_clarification"})
_PLAN_CODE_RE = re.compile(r"^[a-zA-Z0-9_.:/-]{1,120}$")


def _looks_like_contact_value(value: str) -> bool:
    text = str(value or "").strip()
    phone_like = re.search(r"(?<!\w)(?:\+?\d[\d().\s-]{5,}\d)(?!\w)", text)
    return bool(
        re.search(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}", text, flags=re.IGNORECASE)
        or re.search(r"(?:https?://|www\.)", text, flags=re.IGNORECASE)
        or (phone_like and len(re.sub(r"\D", "", phone_like.group(0))) >= 7)
    )


def _safe_match_evidence(value: Any, *, allowed_terms: set[str]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for raw in _list(value)[:12]:
        if not isinstance(raw, dict):
            continue
        field = _text(raw.get("field"))[:48]
        term = _text(raw.get("term")).lower()[:80]
        source = _text(raw.get("source"))
        if (
            field in _MATCH_EVIDENCE_FIELDS
            and term in allowed_terms
            and not _looks_like_contact_value(term)
            and source == "server_profile_evidence"
        ):
            output.append({"field": field, "term": term, "source": source})
    return output


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
    output: dict[str, Any] = {}
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
    for name in ("reason", "provider", "model", "persona_source"):
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


_RECALL_SESSION_PAYLOAD_SCHEMA = "kol_recall_candidate_v2"

# Search-session history is a durable replay surface, not a compact card cache.
# Keep this allow-list explicit so the replay preserves search/audit semantics
# without accidentally persisting unrelated future provider payloads.
_RECALL_SESSION_PAYLOAD_FIELDS = (
    "handle",
    "display_name",
    "platform",
    "profile_url",
    "avatar_url",
    "followers",
    "avg_views",
    "avg_likes",
    "avg_comments",
    "engagement_rate",
    "real_er",
    "real_er_sample_n",
    "real_er_computed_at",
    "real_er_method",
    "data_truth",
    "country",
    "language",
    "primary_topic",
    "bio",
    "vector_score",
    "lexical_score",
    "hybrid_rrf_score",
    "retrieval_score",
    "retrieval_method",
    "type_rank_score",
    "type_score",
    "recall_rank_score",
    "recall_rank_score_method",
    "robust_rank_score",
    "robust_rank_method",
    "precision_rank_score",
    "precision_rank_method",
    "ranking_claim_status",
    "ranking_confidence",
    "platform_calibration",
    "display_rank_score",
    "display_relevance_adjust",
    "relevance_flags",
    "relevance_tier_hint",
    "profile_type",
    "provisional_profile_lane",
    "provisional_profile_lane_source",
    "profile_type_confidence",
    "type_label",
    "creator_type_score",
    "reviewer_type_score",
    "type_reason",
    "type_method",
    "match_tier",
    "filter_status",
    "relaxed_filters",
    "unknown_fields",
    "candidate_bucket",
    "candidate_bucket_reason",
    "candidate_bucket_target",
    "business_lane",
    "candidate_lane",
    "recall_reason",
    "why_fit",
    "evidence",
    "sample_title",
    "used_lenses",
    "used_lenses_note",
    "representative_evidence",
    "evidence_confidence",
    "evidence_quality",
)

_RECALL_SESSION_SOURCE_FIELDS = (
    "vector_method",
    "type_method",
    "retrieval_method",
    "retrieval_tier",
    "sufficiency",
    "ranking_method",
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

    items: list[dict[str, Any]] = []
    query = _dict(result.get("query"))
    query_text = _text(query.get("query_text") or result.get("query"))
    allowed_terms = set(query_evidence_terms(query_text))
    required_product_terms = _list(query.get("required_product_evidence_terms"))
    allowed_terms.update(query_evidence_terms(" ".join(_text(term) for term in required_product_terms)))
    source_items, replay_source, source_count = _recall_source_items(result)
    replay_complete = replay_source == "canonical_items" and len(source_items) == source_count
    for rank, raw in enumerate(source_items, start=1):
        bucket_name = _text(raw.get("bucket")) or "unknown"
        kol_pool_id = _int_or_none(raw.get("kol_pool_id") if raw.get("kol_pool_id") is not None else raw.get("id"))
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
        match_evidence = _safe_match_evidence(raw.get("match_evidence"), allowed_terms=allowed_terms)
        candidate_facets = _safe_candidate_facets(raw.get("candidate_facets"))
        if match_evidence:
            payload["match_evidence"] = match_evidence
            payload["why_fit"] = why_fit_from_match_evidence(match_evidence)
        elif safe_why_fit := _safe_plan_text(legacy_why_fit, limit=1000):
            payload["why_fit"] = safe_why_fit
        if len(candidate_facets) == len(_FACET_NAMES):
            payload["candidate_facets"] = candidate_facets
        items.append(
            {
                "dedupe_key": f"recall:{kol_pool_id or source_url or rank}",
                "item_type": "recall_candidate",
                "status": "matched",
                "stage": "identified",
                "rank": rank,
                "score": _first_recall_score(raw),
                "kol_pool_id": kol_pool_id,
                "source_url": source_url,
                "payload": payload,
            }
        )
    pipeline_running = bool(result.get("_session_pipeline_running"))
    pipeline_progress = _dict(result.get("_session_progress"))
    summary = {
        "kind": "kol_recall",
        "method": result.get("method"),
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
    if match_status in {"matched", "empty"}:
        summary["match_status"] = match_status
    if distribution:
        summary["candidate_set_distribution"] = distribution
    if llm_query_plan:
        summary["llm_query_plan"] = llm_query_plan
    if pipeline_running:
        summary.update({"phase": "base", "progress": pipeline_progress})
    return record_items(
        int(session_id),
        items,
        status="running" if pipeline_running else "ready",
        summary=summary,
    )


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
    del session_id
    url = _dict(result.get("url"))
    video_flow = _dict(result.get("video_flow"))
    profile_flow = _dict(result.get("profile_flow"))
    evidence_result = _dict(video_flow.get("evidence_result"))
    enqueue_result = _dict(video_flow.get("enqueue_result"))
    ai_analysis = _dict(video_flow.get("ai_analysis") or enqueue_result.get("ai_analysis"))
    enqueue_job = _dict(enqueue_result.get("job"))
    normalized_url = _text(url.get("normalized") or url.get("input") or result.get("source_url"))
    url_type = _text(result.get("url_type"))
    item_type = "url_video" if url_type == "video" else "url_profile" if url_type == "profile" else "unknown"
    kol_pool_id = _int_or_none(video_flow.get("kol_pool_id") or profile_flow.get("kol_pool_id") or result.get("matched_kol_pool_id"))
    evidence_id = _int_or_none(video_flow.get("evidence_id") or evidence_result.get("evidence_id"))
    job_id = _int_or_none(
        enqueue_job.get("id")
        or enqueue_result.get("id")
        or enqueue_result.get("job_id")
        or video_flow.get("job_id")
        or profile_flow.get("job_id")
    )
    status = _text(video_flow.get("status") or profile_flow.get("status"))
    if not status:
        status = "matched" if result.get("in_pool") else "identified"
    if status == "ready" and not result.get("execute"):
        status = "identified"
    # ``stage`` is the durable pipeline phase and is constrained by migration
    # 103 to identified/profile/evidence/analysis/summary.  AI availability is
    # a terminal status/reason inside the analysis phase, never a new stage.
    stage = (
        "analysis"
        if status in {"queued", "already_queued", "already_analyzed", "ai_disabled", "not_requested"}
        else "identified"
    )
    if _text(video_flow.get("operation")) == "video_url_resolve_queue":
        stage = "identified"
    if profile_flow and item_type == "url_profile":
        stage = "profile"
    return {
        "dedupe_key": f"{item_type}:{normalized_url or result.get('video_id') or result.get('handle') or 'unknown'}",
        "item_type": item_type,
        "status": _normalize_status(status, item=True),
        "stage": stage,
        "rank": 1,
        "kol_pool_id": kol_pool_id,
        "evidence_id": evidence_id,
        "job_id": job_id,
        "source_url": normalized_url,
        "payload": {
            "url_type": result.get("url_type"),
            "platform": result.get("platform"),
            "video_id": result.get("video_id"),
            "handle": result.get("handle"),
            "channel_id": result.get("channel_id"),
            "creator_identity": result.get("creator_identity") or video_flow.get("creator_identity"),
            "video_metadata": result.get("video_metadata") or video_flow.get("video_metadata"),
            "profile_flow": _compact_flow(profile_flow),
            "video_flow": _compact_flow(video_flow),
            "ai_analysis": ai_analysis or None,
            "in_pool": result.get("in_pool"),
            "matched_kol_pool_id": result.get("matched_kol_pool_id"),
            "viltrox_fit_score_untouched": result.get("viltrox_fit_score_untouched") or video_flow.get("viltrox_fit_score_untouched") or profile_flow.get("viltrox_fit_score_untouched"),
        },
    }


def _link_job_payloads(session_id: int, items: list[dict[str, Any]]) -> int:
    conn = get_conn()
    linked = 0
    linked_job_ids: list[int] = []
    for item in items:
        job_id = _int_or_none(item.get("job_id"))
        item_id = _int_or_none(item.get("id"))
        if not job_id:
            continue
        # Production PostgreSQL must serialize read/merge/write of shared-job
        # lineage.  Without the row lock, concurrent session attaches can both
        # read the old payload and the last writer silently drops the other's
        # lineage edge.  SQLite compatibility stays on its native writer lock.
        select_sql = "SELECT id, payload, status, last_error FROM apify_jobs WHERE id=?"
        if is_postgres_runtime():
            select_sql += " FOR UPDATE"
        row = conn.execute(
            select_sql,
            (int(job_id),),
        ).fetchone()
        if not row:
            continue
        row_data = dict(row)
        payload = _loads(row_data.get("payload"), {})
        if not isinstance(payload, dict):
            payload = {}
        if item_id and _text(item.get("item_type")).lower() == "url_video":
            # The video-analysis job may finish before the URL session item is
            # written.  Persist a proper role-bearing lineage edge so the
            # worker reducer can discover this relationship on a replay.
            lineage_role = (
                "resolver"
                if _text(payload.get("derive_method")).lower() == "video_url_resolve_v1"
                or _text(payload.get("target_type")).lower() == "video_url"
                else "video"
            )
            payload = with_search_session_lineage(
                payload,
                search_session_id=int(session_id),
                search_session_item_id=int(item_id),
                role=lineage_role,
            )
            # Older late-linked rows carried scalar session/item ids without a
            # role.  with_search_session_lineage faithfully imports that legacy
            # edge; drop only the empty-role copy now that the explicit video
            # edge exists, otherwise reducers see two aliases for one job.
            legacy_lineages = payload.get("search_session_lineage")
            if isinstance(legacy_lineages, list):
                payload["search_session_lineage"] = [
                    entry
                    for entry in legacy_lineages
                    if isinstance(entry, dict) and _text(entry.get("role"))
                ]
        else:
            # Preserve the legacy scalar link for profile queue jobs.  Their
            # terminal reducer is intentionally the non-progressive path.
            payload["search_session_id"] = int(session_id)
            if item_id:
                payload["search_session_item_id"] = int(item_id)
        payload["search_session_item_status"] = item.get("status")
        payload["search_session_stage"] = item.get("stage")
        conn.execute(
            "UPDATE apify_jobs SET payload=?::jsonb WHERE id=?",
            (_json_dumps(payload), int(job_id)),
        )
        if item_id:
            linked_job_ids.append(int(job_id))
        linked += 1
    if linked:
        conn.commit()
        # Re-read after the lineage commit.  Reading status before the payload
        # update is racy: a worker can finish between that read and this commit,
        # observe no lineage, and leave the session item queued forever.
        terminal_jobs: list[tuple[int, str, str]] = []
        for job_id in dict.fromkeys(linked_job_ids):
            current = conn.execute(
                "SELECT id, status, last_error FROM apify_jobs WHERE id=?",
                (int(job_id),),
            ).fetchone()
            if not current:
                continue
            current_data = dict(current)
            current_status = _text(current_data.get("status")).lower()
            if current_status in _TERMINAL_LINKED_JOB_STATUSES:
                terminal_jobs.append(
                    (
                        int(job_id),
                        current_status,
                        str(current_data.get("last_error") or "")[:2000],
                    )
                )
        # Close the read transaction before the worker synchronizer starts its
        # own transaction/savepoints on the same raw psycopg connection.
        conn.commit()
        for job_id, job_status, last_error in terminal_jobs:
            _sync_linked_terminal_job(
                conn,
                job_id=job_id,
                status=job_status,
                last_error=last_error,
            )
    return linked


def _sync_linked_terminal_job(
    conn: Any,
    *,
    job_id: int,
    status: str,
    last_error: str = "",
) -> bool:
    """Replay an already-terminal job after lineage attach, without providers."""

    try:
        # Lazy import avoids search_sessions -> search_sessions_attach -> worker
        # module import cycles.  The compat connection wraps the same psycopg
        # connection; the worker synchronizer needs the raw cursor API.
        from app.workers.apify_jobs_worker_session import _sync_search_session_job

        sync_conn = getattr(conn, "_raw", conn)
        synced = _sync_search_session_job(
            sync_conn,
            int(job_id),
            raw_status=str(status or "").strip().lower(),
            reason=str(last_error or "")[:2000],
        )
        if synced is not True:
            # The worker-level wrapper deliberately catches sync errors so a
            # provider job is not failed by an observability write.  Its bool
            # result prevents this late-replay caller from falsely committing
            # and reporting reconciliation success after that catch.
            rollback = getattr(sync_conn, "rollback", None)
            if callable(rollback):
                rollback()
            logger.warning(
                "search session terminal replay not applied | job_id=%s status=%s",
                job_id,
                status,
            )
            return False
        # psycopg SELECTs open an implicit outer transaction.  The worker
        # synchronizer's nested transaction blocks are savepoints in that
        # transaction, not a final commit.  Explicitly commit here or a later
        # compat-connection close rolls the replay back (the session-1085 bug).
        sync_conn.commit()
        return True
    except Exception as exc:
        rollback = getattr(locals().get("sync_conn"), "rollback", None)
        if callable(rollback):
            try:
                rollback()
            except Exception:
                logger.debug("search session terminal replay rollback failed", exc_info=True)
        # Session reconciliation is an observability repair and must not roll
        # back the already-valid URL result/linkage write.
        logger.warning(
            "search session terminal replay failed | job_id=%s status=%s error=%s",
            job_id,
            status,
            exc,
        )
        return False
