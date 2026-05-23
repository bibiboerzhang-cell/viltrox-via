"""Read-only single KOL intelligence card aggregation.

P2 starts with an API shape that gathers existing evidence into one response.
This module intentionally does not call providers, enqueue jobs, call LLMs, or
write derived rows.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.services.vkpi import (
    brand_signal_detector,
    eleven_dimensions,
    kol_competitor_detector,
    kol_history_match,
    kol_pool,
    kol_product_fit,
    refresh_tier,
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip()))
    except (TypeError, ValueError):
        return int(default or 0)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value or "").replace(",", "").strip())
    except (TypeError, ValueError):
        return float(default or 0.0)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except Exception:
        return fallback
    return parsed if parsed is not None else fallback


def _as_list(value: Any) -> list[Any]:
    parsed = _loads(value, [])
    return parsed if isinstance(parsed, list) else []


def _brief_list(value: Any, *, limit: int = 8) -> list[Any]:
    rows = _as_list(value)
    return rows[: max(0, min(50, int(limit or 8)))]


def _status_payload(status: str, *, reason: str = "", error: Exception | None = None, **extra: Any) -> dict[str, Any]:
    payload = {"status": status, **extra}
    if reason:
        payload["reason"] = reason
    if error is not None:
        payload["error"] = f"{type(error).__name__}: {str(error)[:240]}"
    return payload


def _item_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _safe_int(item.get("id")),
        "pool_uid": _text(item.get("pool_uid")),
        "platform": _text(item.get("platform")).lower(),
        "handle": _text(item.get("handle")),
        "profile_url": _text(item.get("profile_url")),
        "display_name": _text(item.get("display_name")),
        "avatar_url": _text(item.get("avatar_url")),
        "bio": _text(item.get("bio")),
        "country": _text(item.get("country")),
        "email": _text(item.get("email")),
        "followers": _safe_int(item.get("followers")),
        "posts_count": _safe_int(item.get("posts_count")),
        "avg_views": _safe_int(item.get("avg_views")),
        "avg_likes": _safe_int(item.get("avg_likes")),
        "avg_comments": _safe_int(item.get("avg_comments")),
        "engagement_rate": _safe_float(item.get("engagement_rate")),
        "primary_topic": _text(item.get("primary_topic")),
        "content_style": _text(item.get("content_style")),
        "viltrox_fit_score": _safe_int(item.get("viltrox_fit_score")),
        "viltrox_fit_reason": _text(item.get("viltrox_fit_reason")),
        "linked_main_kol_id": item.get("linked_main_kol_id"),
        "source_type": _text(item.get("source_type")),
        "source_ref": _text(item.get("source_ref")),
        "sync_status": _text(item.get("sync_status")),
        "last_seen_at": _text(item.get("last_seen_at")),
        "updated_at": _text(item.get("updated_at")),
    }


def _dimensions11(kol_pool_id: int) -> dict[str, Any]:
    try:
        payload = eleven_dimensions.compose_dimensions_11(kol_pool_id)
    except Exception as exc:
        return _status_payload("unavailable", reason="dimensions11_unavailable", error=exc)
    blocks = {
        "block1_content": payload.get("block1_content"),
        "block2_performance": payload.get("block2_performance"),
        "block3_business": payload.get("block3_business"),
        "block4_specialty": payload.get("block4_specialty"),
    }
    return {
        "status": "ready",
        "method": payload.get("method"),
        "overall_score": _safe_int(payload.get("overall_score")),
        "confidence": payload.get("confidence") if isinstance(payload.get("confidence"), dict) else {},
        "blocks": blocks,
        "provider_calls": False,
        "llm_calls": False,
    }


def _competitors(kol_pool_id: int) -> dict[str, Any]:
    try:
        payload = kol_competitor_detector.evaluate_kol_competitors(kol_pool_id, prefer_persisted=True)
    except Exception as exc:
        return _status_payload("unavailable", reason="competitor_relation_unavailable", error=exc)
    relations = payload.get("relations") if isinstance(payload.get("relations"), list) else []
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    tier_counts = Counter(_text(item.get("risk_tier")) or "unknown" for item in relations if isinstance(item, dict))
    return {
        "status": "ready" if relations else "empty",
        "summary": summary,
        "relations": relations,
        "tier_counts": dict(tier_counts),
        "provider_calls": False,
        "write_db": bool(payload.get("write_db")),
        "source": "vkpi_competitor_relation_or_cached_raw",
    }


def _cached_posts(item: dict[str, Any], *, limit: int = 50) -> list[dict[str, Any]]:
    raw = _loads(item.get("raw_platform_data"), {})
    posts = [
        kol_competitor_detector._row_profile_post(item),  # local cached profile fields
        *kol_competitor_detector._extract_posts(raw if isinstance(raw, dict) else {}),
    ]
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, post in enumerate(posts):
        if not isinstance(post, dict):
            continue
        uid = _text(post.get("id") or post.get("post_uid") or post.get("shortCode") or post.get("url") or post.get("title"))
        if not uid:
            uid = f"cached:{index}"
        if uid in seen:
            continue
        seen.add(uid)
        result.append(post)
        if len(result) >= max(1, min(200, int(limit or 50))):
            break
    return result


def _brand_signals(item: dict[str, Any]) -> dict[str, Any]:
    posts = _cached_posts(item, limit=50)
    context = {
        "kol_entity_uid": f"kol_pool:{_safe_int(item.get('id'))}",
        "source_table": "vkpi_kol_pool",
        "source_id": _safe_int(item.get("id")),
        "platform": _text(item.get("platform")),
    }
    signals: dict[str, dict[str, Any]] = {}
    for post in posts:
        for signal in brand_signal_detector.detect_viltrox_signals(post, context=context):
            uid = _text(signal.get("signal_uid"))
            if uid:
                signals[uid] = signal
    rows = list(signals.values())
    type_counts = Counter(_text(row.get("signal_type")) or "unknown" for row in rows)
    role_counts = Counter(_text(row.get("brand_role")) or "unknown" for row in rows)
    return {
        "status": "ready" if rows else "empty",
        "cached_post_count": len(posts),
        "signal_count": len(rows),
        "type_counts": dict(type_counts),
        "role_counts": dict(role_counts),
        "signals": rows[:12],
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "source": "vkpi_kol_pool.raw_platform_data",
    }


def _cached_post_summaries(item: dict[str, Any], *, limit: int = 6) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for post in _cached_posts(item, limit=limit):
        if not isinstance(post, dict):
            continue
        snippet = post.get("snippet") if isinstance(post.get("snippet"), dict) else {}
        stats = post.get("statistics") if isinstance(post.get("statistics"), dict) else {}
        post_uid = _text(post.get("id") or post.get("post_uid") or post.get("shortCode") or post.get("shortcode"))
        url = _text(post.get("post_url") or post.get("url") or post.get("webVideoUrl") or post.get("permalink"))
        if not url and _text(post.get("kind")).lower() == "youtube#video" and post_uid:
            url = f"https://www.youtube.com/watch?v={post_uid}"
        title = _text(post.get("title") or post.get("caption") or post.get("text") or snippet.get("title") or url)
        if not title and not url:
            continue
        rows.append(
            {
                "id": post_uid or url or f"cached-post-{len(rows) + 1}",
                "title": title[:280],
                "url": url,
                "post_url": url,
                "published_at": _text(post.get("published_at") or post.get("publishedAt") or post.get("timestamp") or snippet.get("publishedAt")),
                "views": _safe_int(post.get("views") or post.get("view_count") or post.get("playCount") or stats.get("viewCount")),
                "likes": _safe_int(post.get("likes") or post.get("like_count") or post.get("diggCount") or stats.get("likeCount")),
                "comments": _safe_int(post.get("comments") or post.get("comment_count") or post.get("commentCount") or stats.get("commentCount")),
                "source_kind": "kol_pool_cached_post",
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _memory_card(item: dict[str, Any], competitors: dict[str, Any]) -> dict[str, Any]:
    raw = _loads(item.get("raw_platform_data"), {})
    evidence_summary = raw.get("evidence_summary") if isinstance(raw, dict) and isinstance(raw.get("evidence_summary"), dict) else {}
    try:
        history = kol_history_match.find_history_match(item, platform=_text(item.get("platform"))) or {}
        history_status = "ready" if history else "empty"
    except Exception as exc:
        history = {}
        history_status = "unavailable"
        history_error = f"{type(exc).__name__}: {str(exc)[:240]}"
    else:
        history_error = ""

    recent_posts = history.get("recent_posts") if isinstance(history.get("recent_posts"), list) else []
    if not recent_posts:
        recent_posts = _cached_post_summaries(item, limit=6)
    recent_cooperations = history.get("recent_cooperations") if isinstance(history.get("recent_cooperations"), list) else []
    brand_collaborations = _brief_list(item.get("brand_collaborations_json"))
    recommended_products = _brief_list(item.get("recommended_product_lines_json"))
    potential_concerns = _brief_list(item.get("potential_concerns_json"))
    relations = competitors.get("relations") if isinstance(competitors.get("relations"), list) else []
    competitor_summary = competitors.get("summary") if isinstance(competitors.get("summary"), dict) else {}
    cooperation_count = max(
        _safe_int(history.get("cooperation_count")),
        len(brand_collaborations),
        _safe_int(evidence_summary.get("cooperation_rows")),
    )
    evidence_count = max(_safe_int(history.get("evidence_count")), _safe_int(evidence_summary.get("evidence_count")))
    has_memory = any(
        [
            _text(item.get("source_type")),
            _text(item.get("source_ref")),
            item.get("linked_main_kol_id") is not None,
            cooperation_count,
            evidence_count,
            recent_posts,
            recent_cooperations,
            brand_collaborations,
            relations,
        ]
    )
    status = "ready" if has_memory else history_status
    payload = {
        "status": status,
        "source_type": _text(item.get("source_type") or history.get("source_type")),
        "source_ref": _text(item.get("source_ref") or history.get("source_ref")),
        "linked_main_kol_id": item.get("linked_main_kol_id") or history.get("linked_main_kol_id"),
        "history_match": {
            "status": history_status,
            "matched": bool(history.get("matched")),
            "match_type": _text(history.get("match_type")),
            "match_confidence": _safe_float(history.get("match_confidence")),
            "kol_pool_id": history.get("kol_pool_id"),
            "cooperation_count": cooperation_count,
            "evidence_count": evidence_count,
            "profile_rows": _safe_int(history.get("profile_rows") or evidence_summary.get("kol_profile_rows")),
            "risk_rows": _safe_int(history.get("risk_rows") or evidence_summary.get("risk_rows")),
        },
        "excel_record": {
            "source_type": _text(item.get("source_type")),
            "source_ref": _text(item.get("source_ref")),
            "brand_collaborations": brand_collaborations,
            "recommended_products": recommended_products,
            "potential_concerns": potential_concerns,
            "raw_evidence_summary": evidence_summary,
        },
        "recent_cooperations": recent_cooperations[:5],
        "recent_posts": recent_posts[:6],
        "competitor_memory": {
            "relation_count": len(relations),
            "strongest_brand": _text(competitor_summary.get("competitor_brand")),
            "risk_tier": _text(competitor_summary.get("risk_tier") or "opportunity"),
            "risk_score": _safe_float(competitor_summary.get("risk_score")),
        },
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "source": "vkpi_kol_pool + legacy memory",
    }
    if history_error:
        payload["history_error"] = history_error
    return payload


def _product_fit(kol_pool_id: int, *, include_product_fit: bool) -> dict[str, Any]:
    if not include_product_fit:
        return _status_payload("skipped", reason="include_product_fit_false")
    try:
        payload = kol_product_fit.build_kol_product_fit_preview(
            kol_pool_id=kol_pool_id,
            limit=10,
            include_low_evidence=False,
            with_llm_reasons=False,
            persist_run=False,
        )
    except Exception as exc:
        return _status_payload("unavailable", reason="product_fit_preview_unavailable", error=exc)
    rows = payload.get("items") or payload.get("eligible") or payload.get("recommendations") or []
    if not isinstance(rows, list):
        rows = []
    return {
        "status": "ready" if rows else "empty",
        "method": payload.get("method") or payload.get("mode") or "kol_product_fit_preview",
        "count": len(rows),
        "top": rows[:5],
        "provider_calls": bool(payload.get("provider_calls", False)),
        "llm_calls": bool(payload.get("llm_calls", False)),
        "write_db": bool(payload.get("write_db", False) or payload.get("persisted", False)),
        "source": "vkpi_memory_entities + vkpi_kol_pool + dimensions11",
    }


def _freshness(kol_pool_id: int) -> dict[str, Any]:
    try:
        return refresh_tier.freshness_for_kol(kol_pool_id)
    except Exception as exc:
        return _status_payload("unavailable", reason="freshness_unavailable", error=exc)


def _decision_support(sections: dict[str, dict[str, Any]]) -> dict[str, Any]:
    gaps: list[str] = []
    ready = 0
    for name, payload in sections.items():
        status = _text(payload.get("status")).lower()
        if status in {"ready", "empty"}:
            ready += 1
        elif status in {"unavailable", "error"}:
            gaps.append(f"{name}_{status}")
        elif status == "skipped":
            gaps.append(f"{name}_skipped")
    if gaps:
        readiness = "partial"
    elif ready == len(sections):
        readiness = "ready"
    else:
        readiness = "unknown"
    return {
        "readiness": readiness,
        "ready_sections": ready,
        "total_sections": len(sections),
        "gaps": gaps,
    }


def _confidence_for_section(section: str, payload: dict[str, Any]) -> float:
    if section == "dimensions11":
        confidence = payload.get("confidence") if isinstance(payload.get("confidence"), dict) else {}
        return _safe_float(confidence.get("overall"))
    if section == "product_fit":
        top = payload.get("top") if isinstance(payload.get("top"), list) else []
        scores = [_safe_float(row.get("confidence") or row.get("fit_confidence")) for row in top if isinstance(row, dict)]
        return max(scores) if scores else 0.0
    if payload.get("status") == "ready":
        return 1.0
    if payload.get("status") == "empty":
        return 0.0
    return 0.0


def _evidence_count_for_section(section: str, payload: dict[str, Any]) -> int:
    if section == "freshness":
        return 1 if payload.get("last_refresh_at") or payload.get("last_refresh_status") else 0
    if section == "dimensions11":
        confidence = payload.get("confidence") if isinstance(payload.get("confidence"), dict) else {}
        return sum(1 for key in ("block1_content", "block2_performance", "block3_business", "block4_specialty") if _safe_float(confidence.get(key)) > 0)
    if section == "competitors":
        relations = payload.get("relations") if isinstance(payload.get("relations"), list) else []
        return len(relations)
    if section == "brand_signal":
        return _safe_int(payload.get("signal_count"))
    if section == "memory_card":
        history = payload.get("history_match") if isinstance(payload.get("history_match"), dict) else {}
        competitor_memory = payload.get("competitor_memory") if isinstance(payload.get("competitor_memory"), dict) else {}
        cooperation_count = _safe_int(history.get("cooperation_count"))
        recent_posts = payload.get("recent_posts") if isinstance(payload.get("recent_posts"), list) else []
        recent_cooperations = payload.get("recent_cooperations") if isinstance(payload.get("recent_cooperations"), list) else []
        return cooperation_count + len(recent_posts) + len(recent_cooperations) + _safe_int(competitor_memory.get("relation_count"))
    if section == "product_fit":
        return _safe_int(payload.get("count"))
    return 0


def _evidence_index(sections: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    sources = {
        "freshness": ("Freshness", "vkpi_kol_refresh_tier"),
        "dimensions11": ("11D Confidence", "vkpi_kol_pool + cached posts"),
        "competitors": ("Competitors", "vkpi_competitor_relation or cached posts"),
        "brand_signal": ("Brand Signal", "vkpi_kol_pool.raw_platform_data"),
        "memory_card": ("Memory Card", "vkpi_kol_pool + legacy memory"),
        "product_fit": ("Product Fit", "vkpi_memory_entities/product families"),
    }
    rows: list[dict[str, Any]] = []
    for section in ("freshness", "dimensions11", "competitors", "brand_signal", "memory_card", "product_fit"):
        payload = sections.get(section, {})
        label, source = sources[section]
        row = {
            "section": section,
            "label": label,
            "source": source,
            "status": payload.get("status") or ("ready" if section == "freshness" else "unknown"),
            "evidence_count": _evidence_count_for_section(section, payload),
            "confidence": _confidence_for_section(section, payload),
        }
        if section == "freshness" and payload.get("days_old") is not None:
            row["freshness_hours"] = _safe_int(payload.get("days_old")) * 24
        rows.append(row)
    return rows


def build_kol_pool_intelligence_card(kol_pool_id: int, *, include_product_fit: bool = True) -> dict[str, Any]:
    item = kol_pool.get_item(kol_pool_id)["item"]
    competitors = _competitors(kol_pool_id)
    sections = {
        "freshness": _freshness(kol_pool_id),
        "dimensions11": _dimensions11(kol_pool_id),
        "competitors": competitors,
        "brand_signal": _brand_signals(item),
        "memory_card": _memory_card(item, competitors),
        "product_fit": _product_fit(kol_pool_id, include_product_fit=include_product_fit),
    }
    return {
        "mode": "read_only_kol_intelligence_card_v0",
        "generated_at": _utcnow(),
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "kol_pool_id": int(kol_pool_id),
        "item": _item_summary(item),
        **sections,
        "decision_support": _decision_support(sections),
        "evidence_index": _evidence_index(sections),
    }
