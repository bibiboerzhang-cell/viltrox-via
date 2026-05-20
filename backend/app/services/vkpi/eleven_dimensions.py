"""Rule-only 11-dimension KOL profile scoring.

This is the v2.1 dry-run layer. It reads cached KOL Pool data and existing
rule detectors only; it does not crawl providers and does not call LLMs.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi.kol_competitor_detector import _extract_posts, _post_date, _post_text_blob, _row_profile_post, evaluate_kol_competitors


CLUSTERS_PATH = Path(__file__).with_name("industry_clusters.json")
PRODUCT_FIT_KEYWORDS = {
    "AF-35MM-F12-LAB": ("35mm", "street", "portrait", "review", "photography", "街拍", "人像", "评测"),
    "AF-16MM-F18": ("16mm", "wide", "landscape", "travel", "vlog", "广角", "风光"),
    "AF-27MM-F12": ("27mm", "street", "travel", "creator", "街拍", "旅行"),
    "AF-56MM-F17": ("56mm", "portrait", "wedding", "人像", "婚礼"),
    "AF-75MM-F12": ("75mm", "portrait", "wedding", "人像", "婚礼"),
    "AF-85MM-F18": ("85mm", "portrait", "commercial", "人像", "商业"),
    "AF-90MM-F35-DL": ("90mm", "macro", "product", "commercial", "微距", "产品"),
    "AF-135MM-F18-LAB": ("135mm", "portrait", "sports", "event", "人像", "活动"),
    "EPIC-MAESTRO": ("cinema", "filmmaking", "anamorphic", "commercial", "电影", "视频"),
    "NEXUSFOCUS-F1": ("adapter", "pl mount", "cinema", "filmmaking", "转接", "电影"),
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value or "")
    except Exception:
        return default
    return parsed if parsed is not None else default


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _clamp(value: float | int, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(round(value))))


def _table_exists(table_name: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
        return bool(row)
    except Exception:
        row = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return bool(row)


def _table_columns(table_name: str) -> set[str]:
    if not _table_exists(table_name):
        return set()
    try:
        rows = get_conn().execute(f"PRAGMA table_info({table_name})").fetchall()
    except Exception:
        return set()
    columns: set[str] = set()
    for row in rows:
        item = dict(row)
        name = item.get("name")
        if name:
            columns.add(str(name))
    return columns


def _kol_pool_row(kol_pool_id: int) -> dict[str, Any]:
    row = get_conn().execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    if not row:
        raise LookupError("kol pool item not found")
    return dict(row)


def _parse_date(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(raw[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _posts(row: dict[str, Any]) -> list[dict[str, Any]]:
    raw = _loads(row.get("raw_platform_data"), {})
    posts = [_row_profile_post(row), *_extract_posts(raw if isinstance(raw, dict) else {})]
    return posts


def _text_blob(row: dict[str, Any], posts: list[dict[str, Any]]) -> str:
    pieces = [
        _text(row.get("display_name")),
        _text(row.get("handle")),
        _text(row.get("bio")),
        _text(row.get("primary_topic")),
        _text(row.get("secondary_topics_json")),
        _text(row.get("content_style")),
        _text(row.get("recommended_product_lines_json")),
    ]
    pieces.extend(_post_text_blob(post) for post in posts[:80])
    return " ".join(piece for piece in pieces if piece).lower()


def _load_clusters() -> dict[str, list[str]]:
    try:
        parsed = json.loads(CLUSTERS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(name): [str(term).lower() for term in terms if str(term).strip()]
        for name, terms in parsed.items()
        if isinstance(terms, list)
    }


def _keyword_counts(text: str, mapping: dict[str, list[str] | tuple[str, ...]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for key, terms in mapping.items():
        for term in terms:
            needle = str(term).lower()
            if not needle:
                continue
            if needle in text:
                counts[key] += max(1, text.count(needle))
    return counts


def _content_specialty(text: str) -> dict[str, int]:
    mapping = {
        "review": ("review", "hands-on", "test", "comparison", " vs ", "评测", "测试", "对比"),
        "tutorial": ("tutorial", "how to", "guide", "tips", "教程", "技巧"),
        "streetphoto": ("street", "35mm", "街拍"),
        "vlog": ("vlog", "travel", "iphone", "mobile", "旅行", "手机"),
        "commercial": ("commercial", "brand", "campaign", "广告", "商业"),
        "filmmaking": ("cinema", "filmmaking", "video", "cinematic", "电影", "视频"),
    }
    counts = _keyword_counts(text, mapping)
    if not counts:
        return {"review": 34, "photography": 33, "creator": 33}
    total = sum(counts.values()) or 1
    return {key: _clamp(value / total * 100) for key, value in counts.most_common(3)}


def compute_block1_content(kol_pool_id: int) -> dict[str, Any]:
    row = _kol_pool_row(kol_pool_id)
    posts = _posts(row)
    text = _text_blob(row, posts)
    content_count = int(row.get("posts_count") or len(posts) or 0)
    posting_frequency = _clamp(min(100, math.log10(max(content_count, 1)) * 34))
    specialty = _content_specialty(text)
    diversity = _clamp(35 + min(55, (len(specialty) - 1) * 18 + len(set(specialty)) * 8))
    return {
        "posting_frequency_score": posting_frequency,
        "content_diversity_score": diversity,
        "content_specialty": specialty,
        "source": "vkpi_kol_pool.raw_platform_data",
    }


def compute_block2_performance(kol_pool_id: int) -> dict[str, Any]:
    row = _kol_pool_row(kol_pool_id)
    followers = int(row.get("followers") or 0)
    avg_views = int(row.get("avg_views") or 0)
    engagement = float(row.get("engagement_rate") or 0)
    followers_tier = _clamp(math.log10(max(followers, 1)) * 16)
    engagement_quality = _clamp((engagement * 100) if engagement <= 1 else engagement)
    if not engagement_quality and followers and avg_views:
        engagement_quality = _clamp((avg_views / max(followers, 1)) * 100)
    growth_velocity = 50 if row.get("last_seen_at") else 30
    return {
        "followers_tier_score": followers_tier,
        "engagement_quality_score": engagement_quality,
        "growth_velocity_score": growth_velocity,
        "source": "vkpi_kol_pool.followers_avg_views",
    }


def compute_block3_business(kol_pool_id: int) -> dict[str, Any]:
    row = _kol_pool_row(kol_pool_id)
    collaborations = _loads(row.get("brand_collaborations_json"), [])
    contact_links = _loads(row.get("other_contacts_json"), [])
    has_email = bool(_text(row.get("email")))
    cooperation_history = _clamp(45 + min(45, len(collaborations) * 12)) if collaborations else 35
    contact_reachability = 0
    if has_email:
        contact_reachability += 70
    if isinstance(contact_links, list) and contact_links:
        contact_reachability += min(30, len(contact_links) * 10)
    competitor_summary = evaluate_kol_competitors(kol_pool_id, prefer_persisted=True).get("summary") or {}
    competitor_risk = _clamp(float(competitor_summary.get("risk_score") or 0) * 10)
    return {
        "cooperation_history_score": cooperation_history,
        "contact_reachability_score": _clamp(contact_reachability),
        "competitor_risk_score": competitor_risk,
        "competitor_risk_tier": competitor_summary.get("risk_tier") or "opportunity",
        "source": "vkpi_kol_pool + rule_competitor_detector",
    }


def compute_block4_specialty(kol_pool_id: int) -> dict[str, Any]:
    row = _kol_pool_row(kol_pool_id)
    posts = _posts(row)
    text = _text_blob(row, posts)
    cluster_counts = _keyword_counts(text, _load_clusters())
    clusters = [key for key, _ in cluster_counts.most_common(3)] or ["creator"]
    product_counts = _keyword_counts(text, PRODUCT_FIT_KEYWORDS)
    product_fit: dict[str, int] = {}
    for sku, _terms in PRODUCT_FIT_KEYWORDS.items():
        base = 35
        if sku in product_counts:
            base += min(55, product_counts[sku] * 14)
        if "review" in clusters and sku.startswith("AF-"):
            base += 5
        if "filmmaking" in clusters and sku.startswith(("EPIC", "NEXUS")):
            base += 15
        product_fit[sku] = _clamp(base)
    product_fit = dict(sorted(product_fit.items(), key=lambda item: item[1], reverse=True)[:8])
    return {
        "industry_cluster": clusters,
        "product_fit": product_fit,
        "source": "industry_clusters.json + cached text",
    }


def compose_dimensions_11(kol_pool_id: int) -> dict[str, Any]:
    payload = {
        "kol_pool_id": int(kol_pool_id),
        "provider_calls": False,
        "llm_calls": False,
        "computed_at": _utcnow(),
        "method": "rule_dimensions_11_v0",
        "block1_content": compute_block1_content(kol_pool_id),
        "block2_performance": compute_block2_performance(kol_pool_id),
        "block3_business": compute_block3_business(kol_pool_id),
        "block4_specialty": compute_block4_specialty(kol_pool_id),
    }
    scores = [
        payload["block1_content"]["posting_frequency_score"],
        payload["block1_content"]["content_diversity_score"],
        payload["block2_performance"]["followers_tier_score"],
        payload["block2_performance"]["engagement_quality_score"],
        payload["block2_performance"]["growth_velocity_score"],
        payload["block3_business"]["cooperation_history_score"],
        payload["block3_business"]["contact_reachability_score"],
        100 - payload["block3_business"]["competitor_risk_score"],
    ]
    payload["overall_score"] = _clamp(sum(scores) / len(scores))
    return payload


def batch_preview_dimensions11(*, limit: int = 20, source_type: str = "legacy_excel_p2d") -> dict[str, Any]:
    safe_limit = max(1, min(200, int(limit or 20)))
    params: list[Any] = []
    where = "WHERE 1=1"
    if source_type:
        where += " AND source_type=?"
        params.append(source_type)
    rows = get_conn().execute(
        f"""
        SELECT id
        FROM vkpi_kol_pool
        {where}
        ORDER BY COALESCE(last_seen_at, updated_at, created_at) DESC, id DESC
        LIMIT ?
        """,
        (*params, safe_limit),
    ).fetchall()
    items = [compose_dimensions_11(int(row["id"])) for row in rows]
    return {
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "profile_deep_ready": _table_exists("vkpi_kol_profile_deep"),
        "source_type": source_type,
        "count": len(items),
        "items": items,
    }


def _profile_deep_match(row: dict[str, Any], columns: set[str]) -> tuple[str, tuple[Any, ...], str] | None:
    if "kol_pool_id" in columns:
        return "kol_pool_id=?", (int(row["id"]),), "kol_pool_id"
    if "kol_entity_uid" in columns and _text(row.get("pool_uid")):
        return "kol_entity_uid=?", (_text(row.get("pool_uid")),), "kol_entity_uid"
    if {"platform", "handle"}.issubset(columns):
        return "LOWER(platform)=LOWER(?) AND LOWER(handle)=LOWER(?)", (
            _text(row.get("platform")),
            _text(row.get("handle")),
        ), "platform_handle"
    return None


def backfill_existing_profile_deep_dimensions11(*, limit: int = 200, source_type: str = "legacy_excel_p2d") -> dict[str, Any]:
    columns = _table_columns("vkpi_kol_profile_deep")
    if not columns:
        return {
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "skipped": True,
            "reason": "vkpi_kol_profile_deep table is not available",
        }
    if "dimensions_11_json" not in columns:
        return {
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "skipped": True,
            "reason": "vkpi_kol_profile_deep.dimensions_11_json column is not available",
            "columns": sorted(columns),
        }
    safe_limit = max(1, min(1200, int(limit or 200)))
    params: list[Any] = []
    where = "WHERE 1=1"
    if source_type:
        where += " AND source_type=?"
        params.append(source_type)
    rows = get_conn().execute(
        f"""
        SELECT id, pool_uid, platform, handle
        FROM vkpi_kol_pool
        {where}
        ORDER BY COALESCE(last_seen_at, updated_at, created_at) DESC, id DESC
        LIMIT ?
        """,
        (*params, safe_limit),
    ).fetchall()
    conn = get_conn()
    attempted = 0
    updated = 0
    skipped = 0
    match_modes: Counter[str] = Counter()
    now = _utcnow()
    set_clause = "dimensions_11_json=?"
    include_updated_at = "updated_at" in columns
    if include_updated_at:
        set_clause += ", updated_at=?"
    for raw_row in rows:
        row = dict(raw_row)
        match = _profile_deep_match(row, columns)
        if not match:
            skipped += 1
            continue
        attempted += 1
        where_clause, where_params, match_mode = match
        match_modes[match_mode] += 1
        payload = compose_dimensions_11(int(row["id"]))
        update_params: list[Any] = [_json(payload)]
        if include_updated_at:
            update_params.append(now)
        cursor = conn.execute(
            f"UPDATE vkpi_kol_profile_deep SET {set_clause} WHERE {where_clause}",
            (*update_params, *where_params),
        )
        updated += max(0, int(getattr(cursor, "rowcount", 0) or 0))
    conn.commit()
    return {
        "provider_calls": False,
        "llm_calls": False,
        "write_db": True,
        "skipped": False,
        "source_type": source_type,
        "limit": safe_limit,
        "kol_rows": len(rows),
        "attempted_updates": attempted,
        "updated_rows": updated,
        "skipped_rows": skipped,
        "match_modes": dict(match_modes),
    }
