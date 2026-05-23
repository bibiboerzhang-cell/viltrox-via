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


def build_kol_pool_intelligence_card(kol_pool_id: int, *, include_product_fit: bool = True) -> dict[str, Any]:
    item = kol_pool.get_item(kol_pool_id)["item"]
    sections = {
        "freshness": _freshness(kol_pool_id),
        "dimensions11": _dimensions11(kol_pool_id),
        "competitors": _competitors(kol_pool_id),
        "brand_signal": _brand_signals(item),
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
        "evidence_index": [
            {"section": "freshness", "source": "vkpi_kol_refresh_tier", "status": sections["freshness"].get("status") or "ready"},
            {"section": "dimensions11", "source": "vkpi_kol_pool + cached posts", "status": sections["dimensions11"].get("status")},
            {"section": "competitors", "source": "vkpi_competitor_relation or cached posts", "status": sections["competitors"].get("status")},
            {"section": "brand_signal", "source": "vkpi_kol_pool.raw_platform_data", "status": sections["brand_signal"].get("status")},
            {"section": "product_fit", "source": "vkpi_memory_entities/product families", "status": sections["product_fit"].get("status")},
        ],
    }
