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

from typing import Any

from app.db.connection import get_conn

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
    recorded = record_items(int(session_id), [item], status=session_status, summary=summary)
    recorded["jobs_linked"] = _link_job_payloads(int(session_id), recorded.get("items") or [])
    return recorded


def attach_recall_result(session_id: int, result: dict[str, Any]) -> dict[str, Any]:
    from app.domains.kol.search_sessions import record_items

    items: list[dict[str, Any]] = []
    rank = 1
    buckets = _dict(result.get("buckets"))
    for bucket_name in ("creator", "reviewer"):
        for raw in _list(buckets.get(bucket_name)):
            if not isinstance(raw, dict):
                continue
            kol_pool_id = _int_or_none(raw.get("kol_pool_id") or raw.get("id"))
            source_url = _text(raw.get("profile_url") or raw.get("url"))
            score = _float_or_none(raw.get("recall_rank_score") or raw.get("vector_score"))
            items.append(
                {
                    "dedupe_key": f"recall:{kol_pool_id or source_url or rank}",
                    "item_type": "recall_candidate",
                    "status": "matched",
                    "stage": "identified",
                    "rank": rank,
                    "score": score,
                    "kol_pool_id": kol_pool_id,
                    "source_url": source_url,
                    "payload": {
                        "bucket": bucket_name,
                        "handle": raw.get("handle"),
                        "display_name": raw.get("display_name"),
                        "platform": raw.get("platform"),
                        "profile_type": raw.get("profile_type"),
                        "followers": raw.get("followers"),
                        # 问题1 头像修:recall_candidate 会话项此前漏写 avatar_url(new_creator/existing_kol 都写了),
                        # 致历史回填掉头像。透传 _build_item 已填的 avatar_url。
                        "avatar_url": raw.get("avatar_url"),
                        "recall_rank_score": raw.get("recall_rank_score"),
                        "vector_score": raw.get("vector_score"),
                        "type_score": raw.get("type_score"),
                        "evidence": raw.get("evidence"),
                    },
                }
            )
            rank += 1
    summary = {
        "kind": "kol_recall",
        "items_written": len(items),
        "diagnostics": result.get("diagnostics"),
        "query": result.get("query"),
    }
    return record_items(int(session_id), items, status="ready", summary=summary)


def attach_new_discovery_result(session_id: int, result: dict[str, Any]) -> dict[str, Any]:
    """Attach platform-discovery candidates to an existing smart-search session."""
    from app.domains.kol.search_sessions import get_session, record_items

    items: list[dict[str, Any]] = []
    rank = 1
    for raw in _list(result.get("existing_matches")):
        if not isinstance(raw, dict):
            continue
        kol_pool_id = _int_or_none(raw.get("history_kol_pool_id") or _dict(raw.get("historical_match")).get("kol_pool_id"))
        source_url = _text(raw.get("channel_url") or raw.get("source_url"))
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
                    "source_url": raw.get("source_url"),
                    "channel_url": raw.get("channel_url"),
                    "avatar_url": raw.get("avatar_url"),
                    "historical_match": raw.get("historical_match"),
                },
            }
        )
        rank += 1
    for raw in _list(result.get("new_creators")):
        if not isinstance(raw, dict):
            continue
        source_url = _text(raw.get("channel_url") or raw.get("source_url"))
        handle = _text(raw.get("handle") or raw.get("channel_name"))
        platform = _text(raw.get("platform") or (result.get("platforms") or [""])[0])
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
                    "source_url": raw.get("source_url"),
                    "channel_url": raw.get("channel_url"),
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
                },
            }
        )
        rank += 1

    existing_summary: dict[str, Any] = {}
    try:
        existing_summary = _dict(get_session(int(session_id)).get("result_summary"))
    except Exception:
        existing_summary = {}
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
    status = "ready"
    if result.get("status") in {"partial", "failed"}:
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
    if status in {"already_analyzed", "ready"}:
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
    enqueue_job = _dict(enqueue_result.get("job"))
    normalized_url = _text(url.get("normalized") or url.get("input") or result.get("source_url"))
    url_type = _text(result.get("url_type"))
    item_type = "url_video" if url_type == "video" else "url_profile" if url_type == "profile" else "unknown"
    kol_pool_id = _int_or_none(video_flow.get("kol_pool_id") or profile_flow.get("kol_pool_id") or result.get("matched_kol_pool_id"))
    evidence_id = _int_or_none(video_flow.get("evidence_id") or evidence_result.get("evidence_id"))
    job_id = _int_or_none(enqueue_job.get("id") or enqueue_result.get("id") or enqueue_result.get("job_id"))
    status = _text(video_flow.get("status") or profile_flow.get("status"))
    if not status:
        status = "matched" if result.get("in_pool") else "identified"
    if status == "ready" and not result.get("execute"):
        status = "identified"
    stage = "analysis" if status in {"queued", "already_queued", "already_analyzed"} else "identified"
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
            "in_pool": result.get("in_pool"),
            "matched_kol_pool_id": result.get("matched_kol_pool_id"),
            "viltrox_fit_score_untouched": result.get("viltrox_fit_score_untouched") or video_flow.get("viltrox_fit_score_untouched") or profile_flow.get("viltrox_fit_score_untouched"),
        },
    }


def _link_job_payloads(session_id: int, items: list[dict[str, Any]]) -> int:
    conn = get_conn()
    linked = 0
    for item in items:
        job_id = _int_or_none(item.get("job_id"))
        item_id = _int_or_none(item.get("id"))
        if not job_id:
            continue
        row = conn.execute(
            "SELECT id, payload FROM apify_jobs WHERE id=?",
            (int(job_id),),
        ).fetchone()
        if not row:
            continue
        payload = _loads(dict(row).get("payload"), {})
        if not isinstance(payload, dict):
            payload = {}
        payload["search_session_id"] = int(session_id)
        if item_id:
            payload["search_session_item_id"] = int(item_id)
        payload["search_session_item_status"] = item.get("status")
        payload["search_session_stage"] = item.get("stage")
        conn.execute(
            "UPDATE apify_jobs SET payload=?::jsonb WHERE id=?",
            (_json_dumps(payload), int(job_id)),
        )
        linked += 1
    if linked:
        conn.commit()
    return linked
