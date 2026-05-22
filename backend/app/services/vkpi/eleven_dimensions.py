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
GENERIC_PRODUCT_TERMS = {
    "af",
    "mf",
    "lens",
    "lenses",
    "camera lens",
    "cine lenses",
    "full frame",
    "full-frame",
    "aps-c",
    "default title",
    "official",
    "viltrox",
}
_CATALOG_PRODUCT_FIT_KEYWORDS: dict[str, tuple[str, ...]] | None = None


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


def _confidence(value: float | int | None) -> float:
    try:
        parsed = float(value if value is not None else 0)
    except (TypeError, ValueError):
        parsed = 0.0
    return round(max(0.0, min(1.0, parsed)), 2)


def _mean_confidence(values: list[float | int | None]) -> float:
    clean = [_confidence(value) for value in values if value is not None]
    if not clean:
        return 0.0
    return _confidence(sum(clean) / len(clean))


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


def _product_term(value: Any) -> str:
    return re.sub(r"\s+", " ", _text(value).lower().replace("_", " ")).strip()


def _add_product_term(terms: set[str], value: Any) -> None:
    term = _product_term(value)
    if not term or term in GENERIC_PRODUCT_TERMS or len(term) < 3:
        return
    terms.add(term)


def _focal_terms(text: str) -> set[str]:
    return {f"{match}mm" for match in re.findall(r"\b(\d{1,3})\s*mm\b", text.lower())}


def _aperture_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for raw in re.findall(r"\bf\s*[=/]?\s*(\d(?:[._]\d)?)", text.lower()):
        normalized = raw.replace("_", ".")
        if "." not in normalized and len(normalized) == 2:
            normalized = f"{normalized[0]}.{normalized[1]}"
        terms.add(f"f{normalized}")
    return terms


def _terms_from_product_text(value: Any) -> set[str]:
    text = _product_term(value)
    terms: set[str] = set()
    if not text:
        return terms
    _add_product_term(terms, text)
    focal_terms = _focal_terms(text)
    aperture_terms = _aperture_terms(text)
    terms.update(focal_terms)
    terms.update(aperture_terms)
    for focal in focal_terms:
        for aperture in aperture_terms:
            terms.add(f"{focal} {aperture}")
            terms.add(f"{focal}{aperture}")
    for phrase in re.findall(r"\b(?:af|mf)\s+\d{1,3}mm\s+f\s*[=/]?\s*\d(?:[._]\d)?(?:\s+\w+)?", text):
        _add_product_term(terms, phrase)
    return terms


def _catalog_product_fit_keywords() -> dict[str, tuple[str, ...]]:
    global _CATALOG_PRODUCT_FIT_KEYWORDS
    if _CATALOG_PRODUCT_FIT_KEYWORDS is not None:
        return _CATALOG_PRODUCT_FIT_KEYWORDS
    mapping: dict[str, set[str]] = {sku: set(terms) for sku, terms in PRODUCT_FIT_KEYWORDS.items()}
    if not _table_exists("vkpi_products"):
        _CATALOG_PRODUCT_FIT_KEYWORDS = {sku: tuple(sorted(terms)) for sku, terms in mapping.items()}
        return _CATALOG_PRODUCT_FIT_KEYWORDS
    try:
        rows = get_conn().execute(
            """
            SELECT sku, category_main, category_detail, model_name, marketing_name,
                   series, mount, specs_json, fit_tags_json
            FROM vkpi_products
            WHERE LOWER(COALESCE(category_main, '')) IN ('lens', 'cine lens')
               OR LOWER(COALESCE(category_detail, '')) LIKE '%lens%'
            ORDER BY source_confidence DESC, updated_at DESC, sku ASC
            LIMIT 300
            """
        ).fetchall()
    except Exception:
        rows = []
    for raw in rows:
        row = dict(raw)
        sku = _text(row.get("sku")).upper()
        if not sku:
            continue
        terms = mapping.setdefault(sku, set())
        for key in ("sku", "model_name", "marketing_name", "series", "mount", "category_detail"):
            terms.update(_terms_from_product_text(row.get(key)))
        sku_spaced = sku.replace("-", " ").lower()
        terms.update(_terms_from_product_text(sku_spaced))
        specs = _loads(row.get("specs_json"), {})
        if isinstance(specs, dict):
            for key in (
                "lens_mount",
                "focal_length",
                "aperture",
                "lens_elements",
                "viewing_angle",
                "filter_size",
                "official_handle",
                "variant_title",
            ):
                terms.update(_terms_from_product_text(specs.get(key)))
            for value in specs.get("official_tags") or []:
                terms.update(_terms_from_product_text(value))
            for value in specs.get("highlights") or []:
                terms.update(_terms_from_product_text(value))
        fit_tags = _loads(row.get("fit_tags_json"), [])
        if isinstance(fit_tags, list):
            for value in fit_tags:
                terms.update(_terms_from_product_text(value))
    _CATALOG_PRODUCT_FIT_KEYWORDS = {
        sku: tuple(sorted(term for term in terms if term and term not in GENERIC_PRODUCT_TERMS))
        for sku, terms in mapping.items()
    }
    return _CATALOG_PRODUCT_FIT_KEYWORDS


def _content_specialty(text: str) -> tuple[dict[str, int], int]:
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
        return {}, 0
    total = sum(counts.values()) or 1
    return {key: _clamp(value / total * 100) for key, value in counts.most_common(3)}, total


def compute_block1_content(kol_pool_id: int) -> dict[str, Any]:
    row = _kol_pool_row(kol_pool_id)
    posts = _posts(row)
    text = _text_blob(row, posts)
    content_count = int(row.get("posts_count") or len(posts) or 0)
    posting_frequency = _clamp(min(100, math.log10(max(content_count, 1)) * 34)) if content_count else 0
    specialty, specialty_evidence_count = _content_specialty(text)
    diversity = _clamp(35 + min(55, (len(specialty) - 1) * 18 + len(set(specialty)) * 8)) if specialty else 0
    posting_confidence = 0.85 if content_count >= 25 else 0.65 if content_count >= 5 else 0.35 if content_count else 0.0
    specialty_confidence = (
        0.85
        if specialty_evidence_count >= 8
        else 0.65
        if specialty_evidence_count >= 3
        else 0.4
        if specialty_evidence_count
        else 0.0
    )
    return {
        "posting_frequency_score": posting_frequency,
        "content_diversity_score": diversity,
        "content_specialty": specialty,
        "confidence": {
            "posting_frequency_score": _confidence(posting_confidence),
            "content_diversity_score": _confidence(specialty_confidence),
            "content_specialty": _confidence(specialty_confidence),
        },
        "evidence": {
            "posts_count": content_count,
            "specialty_keyword_hits": specialty_evidence_count,
        },
        "source": "vkpi_kol_pool.raw_platform_data",
    }


def compute_block2_performance(kol_pool_id: int) -> dict[str, Any]:
    row = _kol_pool_row(kol_pool_id)
    followers = int(row.get("followers") or 0)
    avg_views = int(row.get("avg_views") or 0)
    engagement = float(row.get("engagement_rate") or 0)
    followers_tier = _clamp(math.log10(max(followers, 1)) * 16)
    engagement_quality = _clamp((engagement * 100) if engagement <= 1 else engagement)
    engagement_confidence = 0.75 if engagement_quality else 0.0
    if not engagement_quality and followers and avg_views:
        engagement_quality = _clamp((avg_views / max(followers, 1)) * 100)
        engagement_confidence = 0.6
    growth_velocity = 50 if row.get("last_seen_at") else 30
    return {
        "followers_tier_score": followers_tier,
        "engagement_quality_score": engagement_quality,
        "growth_velocity_score": growth_velocity,
        "confidence": {
            "followers_tier_score": _confidence(0.9 if followers else 0.0),
            "engagement_quality_score": _confidence(engagement_confidence),
            "growth_velocity_score": _confidence(0.35 if row.get("last_seen_at") else 0.0),
        },
        "evidence": {
            "followers": followers,
            "avg_views": avg_views,
            "engagement_rate": engagement,
            "last_seen_at": _text(row.get("last_seen_at")),
        },
        "source": "vkpi_kol_pool.followers_avg_views",
    }


def compute_block3_business(kol_pool_id: int) -> dict[str, Any]:
    row = _kol_pool_row(kol_pool_id)
    collaborations = _loads(row.get("brand_collaborations_json"), [])
    contact_links = _loads(row.get("other_contacts_json"), [])
    has_email = bool(_text(row.get("email")))
    cooperation_history = _clamp(45 + min(45, len(collaborations) * 12)) if collaborations else 0
    contact_reachability = 0
    if has_email:
        contact_reachability += 70
    if isinstance(contact_links, list) and contact_links:
        contact_reachability += min(30, len(contact_links) * 10)
    competitor_result = evaluate_kol_competitors(kol_pool_id, prefer_persisted=True)
    competitor_summary = competitor_result.get("summary") or {}
    competitor_risk = _clamp(float(competitor_summary.get("risk_score") or 0) * 10)
    contact_count = (1 if has_email else 0) + (len(contact_links) if isinstance(contact_links, list) else 0)
    competitor_relations = competitor_result.get("relations") or []
    return {
        "cooperation_history_score": cooperation_history,
        "contact_reachability_score": _clamp(contact_reachability),
        "competitor_risk_score": competitor_risk,
        "competitor_risk_tier": competitor_summary.get("risk_tier") or "opportunity",
        "confidence": {
            "cooperation_history_score": _confidence(0.85 if collaborations else 0.0),
            "contact_reachability_score": _confidence(0.9 if contact_count else 0.25 if row.get("profile_url") else 0.0),
            "competitor_risk_score": _confidence(0.9 if competitor_result.get("persisted") and competitor_relations else 0.55 if competitor_relations else 0.0),
        },
        "evidence": {
            "collaboration_count": len(collaborations) if isinstance(collaborations, list) else 0,
            "contact_count": contact_count,
            "competitor_relation_count": len(competitor_relations) if isinstance(competitor_relations, list) else 0,
            "competitor_relation_persisted": bool(competitor_result.get("persisted")),
        },
        "source": "vkpi_kol_pool + rule_competitor_detector",
    }


def compute_block4_specialty(kol_pool_id: int) -> dict[str, Any]:
    row = _kol_pool_row(kol_pool_id)
    posts = _posts(row)
    text = _text_blob(row, posts)
    cluster_counts = _keyword_counts(text, _load_clusters())
    clusters = [key for key, _ in cluster_counts.most_common(3)]
    product_keywords = _catalog_product_fit_keywords()
    product_counts = _keyword_counts(text, product_keywords)
    product_fit: dict[str, int] = {}
    product_fit_confidence: dict[str, float] = {}
    for sku, _terms in product_keywords.items():
        hit_count = product_counts.get(sku, 0)
        if not hit_count:
            continue
        base = 45 + min(45, hit_count * 14)
        if "review" in clusters and sku.startswith("AF-"):
            base += 5
        if "filmmaking" in clusters and sku.startswith(("EPIC", "NEXUS")):
            base += 15
        product_fit[sku] = _clamp(base)
        product_fit_confidence[sku] = _confidence(0.45 + min(0.45, hit_count * 0.15))
    product_fit = dict(sorted(product_fit.items(), key=lambda item: item[1], reverse=True)[:8])
    product_fit_confidence = {sku: product_fit_confidence[sku] for sku in product_fit}
    cluster_hits = sum(cluster_counts.values())
    return {
        "industry_cluster": clusters,
        "product_fit": product_fit,
        "product_fit_confidence": product_fit_confidence,
        "confidence": {
            "industry_cluster": _confidence(0.85 if cluster_hits >= 8 else 0.65 if cluster_hits >= 3 else 0.35 if cluster_hits else 0.0),
            "product_fit": _mean_confidence(list(product_fit_confidence.values())),
        },
        "evidence": {
            "industry_keyword_hits": cluster_hits,
            "product_keyword_hits": dict(product_counts),
        },
        "source": "industry_clusters.json + vkpi_products + cached text",
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
    confidence = {
        "block1_content": _mean_confidence(list((payload["block1_content"].get("confidence") or {}).values())),
        "block2_performance": _mean_confidence(list((payload["block2_performance"].get("confidence") or {}).values())),
        "block3_business": _mean_confidence(list((payload["block3_business"].get("confidence") or {}).values())),
        "block4_specialty": _mean_confidence(list((payload["block4_specialty"].get("confidence") or {}).values())),
    }
    score_items = [
        (payload["block1_content"]["posting_frequency_score"], payload["block1_content"]["confidence"]["posting_frequency_score"]),
        (payload["block1_content"]["content_diversity_score"], payload["block1_content"]["confidence"]["content_diversity_score"]),
        (payload["block2_performance"]["followers_tier_score"], payload["block2_performance"]["confidence"]["followers_tier_score"]),
        (payload["block2_performance"]["engagement_quality_score"], payload["block2_performance"]["confidence"]["engagement_quality_score"]),
        (payload["block2_performance"]["growth_velocity_score"], payload["block2_performance"]["confidence"]["growth_velocity_score"]),
        (payload["block3_business"]["cooperation_history_score"], payload["block3_business"]["confidence"]["cooperation_history_score"]),
        (payload["block3_business"]["contact_reachability_score"], payload["block3_business"]["confidence"]["contact_reachability_score"]),
        (100 - payload["block3_business"]["competitor_risk_score"], payload["block3_business"]["confidence"]["competitor_risk_score"]),
    ]
    weighted = [(float(score), _confidence(conf)) for score, conf in score_items if _confidence(conf) > 0]
    payload["confidence"] = {**confidence, "overall": _mean_confidence(list(confidence.values()))}
    if weighted:
        payload["overall_score"] = _clamp(sum(score * conf for score, conf in weighted) / sum(conf for _score, conf in weighted))
    else:
        payload["overall_score"] = 0
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
