"""Rule-only competitor relation detection for KOL pool rows.

This module reads already-cached local KOL data. It does not call providers,
does not call LLMs, and does not write by default.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime


CONFIG_PATH = Path(__file__).with_name("competitor_brands.json")
DEFAULT_BRANDS: dict[str, dict[str, Any]] = {
    "sigma": {"keywords": ["sigma", "sigmaphoto", "@sigmaphoto"], "priority": "tier1"},
    "tamron": {"keywords": ["tamron", "@tamronusa", "tamronlens"], "priority": "tier1"},
    "samyang": {"keywords": ["samyang", "samyanglens", "@samyangoptics", "rokinon"], "priority": "tier2"},
    "yongnuo": {"keywords": ["yongnuo", "@yongnuo", "yn lens", "yn "], "priority": "tier3"},
    "meike": {"keywords": ["meike", "@meike_global"], "priority": "tier3"},
    "godox": {"keywords": ["godox", "@godox_photo"], "priority": "tier2"},
}

SPONSORED_PATTERNS = (
    "sponsored by",
    "paid partnership",
    "partnered with",
    "in partnership with",
    "#ad",
    " ad ",
)
GIFTED_PATTERNS = (
    "gifted",
    "sent me",
    "sent over",
    "thanks to",
    "provided by",
    "loaned",
)
EVALUATED_PATTERNS = (
    "review",
    "hands-on",
    "hands on",
    "test",
    "comparison",
    "compare",
    "versus",
    " vs ",
    "评测",
    "测试",
    "对比",
)
POSITIVE_PATTERNS = ("best", "love", "amazing", "excellent", "great", "10/10", "sharp", "favorite")
NEGATIVE_PATTERNS = ("disappointed", "poor", "issue", "issues", "bad", "worst", "broken", "terrible")
_RELATION_SCHEMA_READY = False


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
    return json.dumps(value or [], ensure_ascii=False, default=str)


def ensure_competitor_relation_schema() -> None:
    global _RELATION_SCHEMA_READY
    if _RELATION_SCHEMA_READY:
        return
    conn = get_conn()
    if is_postgres_runtime():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_competitor_relation (
                id BIGSERIAL PRIMARY KEY,
                kol_pool_id BIGINT REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
                kol_entity_uid TEXT NOT NULL DEFAULT '',
                platform TEXT NOT NULL DEFAULT '',
                handle TEXT NOT NULL DEFAULT '',
                display_name TEXT DEFAULT '',
                competitor_brand TEXT NOT NULL,
                collaboration_depth TEXT NOT NULL DEFAULT 'none',
                collaboration_recency_days INTEGER,
                collaboration_count_90d INTEGER NOT NULL DEFAULT 0,
                collaboration_count_total INTEGER NOT NULL DEFAULT 0,
                sentiment TEXT NOT NULL DEFAULT 'neutral',
                risk_score NUMERIC(3,1) NOT NULL DEFAULT 0,
                risk_tier TEXT NOT NULL DEFAULT 'opportunity',
                evidence_post_uids_json TEXT NOT NULL DEFAULT '[]',
                evidence_json TEXT NOT NULL DEFAULT '[]',
                last_evidence_at TIMESTAMPTZ,
                computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(kol_pool_id, competitor_brand)
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_competitor_relation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kol_pool_id INTEGER,
                kol_entity_uid TEXT NOT NULL DEFAULT '',
                platform TEXT NOT NULL DEFAULT '',
                handle TEXT NOT NULL DEFAULT '',
                display_name TEXT DEFAULT '',
                competitor_brand TEXT NOT NULL,
                collaboration_depth TEXT NOT NULL DEFAULT 'none',
                collaboration_recency_days INTEGER,
                collaboration_count_90d INTEGER NOT NULL DEFAULT 0,
                collaboration_count_total INTEGER NOT NULL DEFAULT 0,
                sentiment TEXT NOT NULL DEFAULT 'neutral',
                risk_score NUMERIC NOT NULL DEFAULT 0,
                risk_tier TEXT NOT NULL DEFAULT 'opportunity',
                evidence_post_uids_json TEXT NOT NULL DEFAULT '[]',
                evidence_json TEXT NOT NULL DEFAULT '[]',
                last_evidence_at TEXT,
                computed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(kol_pool_id, competitor_brand)
            )
            """
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_competitor_relation_kol ON vkpi_competitor_relation(kol_pool_id, risk_score DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_competitor_relation_risk ON vkpi_competitor_relation(risk_tier, risk_score DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_competitor_relation_brand ON vkpi_competitor_relation(competitor_brand, risk_tier, risk_score DESC)")
    conn.commit()
    _RELATION_SCHEMA_READY = True


def _parse_datetime(value: Any) -> datetime | None:
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


def _days_since(value: Any) -> int | None:
    parsed = _parse_datetime(value)
    if not parsed:
        return None
    return max(0, (datetime.now(timezone.utc) - parsed).days)


def _last_evidence_at(relation: dict[str, Any]) -> str | None:
    dates: list[datetime] = []
    for item in relation.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        parsed = _parse_datetime(item.get("published_at"))
        if parsed:
            dates.append(parsed)
    if not dates:
        return None
    return max(dates).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_competitor_brands() -> dict[str, dict[str, Any]]:
    try:
        parsed = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_BRANDS
    if not isinstance(parsed, dict):
        return DEFAULT_BRANDS
    result: dict[str, dict[str, Any]] = {}
    for brand, config in parsed.items():
        if not isinstance(config, dict):
            continue
        keywords = config.get("keywords")
        if not isinstance(keywords, list) or not keywords:
            continue
        result[str(brand).strip().lower()] = {
            "keywords": [str(item).strip().lower() for item in keywords if str(item).strip()],
            "priority": str(config.get("priority") or "tier3"),
        }
    return result or DEFAULT_BRANDS


def _keyword_match(text: str, keyword: str) -> bool:
    lowered = text.lower()
    key = keyword.lower().strip()
    if not key:
        return False
    if key.startswith("@") or " " in key:
        return key in lowered
    return re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", lowered) is not None


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _post_title(post: dict[str, Any]) -> str:
    snippet = post.get("snippet") if isinstance(post.get("snippet"), dict) else {}
    return _first_text(post.get("title"), snippet.get("title"), post.get("caption"), post.get("text"), post.get("description"))


def _post_date(post: dict[str, Any]) -> str:
    snippet = post.get("snippet") if isinstance(post.get("snippet"), dict) else {}
    return _first_text(post.get("publishedAt"), post.get("published_at"), post.get("createdAt"), post.get("timestamp"), snippet.get("publishedAt"))


def _post_url(post: dict[str, Any], platform: str) -> str:
    snippet = post.get("snippet") if isinstance(post.get("snippet"), dict) else {}
    video_id = _first_text(post.get("id"), post.get("videoId"), snippet.get("resourceId", {}).get("videoId") if isinstance(snippet.get("resourceId"), dict) else "")
    if _text(post.get("url")):
        return _text(post.get("url"))
    if platform == "youtube" and video_id:
        if _text(post.get("kind")) == "youtube#channel":
            return f"https://www.youtube.com/channel/{video_id}"
        return f"https://www.youtube.com/watch?v={video_id}"
    return ""


def _post_text_blob(post: dict[str, Any]) -> str:
    snippet = post.get("snippet") if isinstance(post.get("snippet"), dict) else {}
    branding = post.get("brandingSettings") if isinstance(post.get("brandingSettings"), dict) else {}
    branding_channel = branding.get("channel") if isinstance(branding.get("channel"), dict) else {}
    values: list[str] = [
        _post_title(post),
        _text(post.get("description")),
        _text(snippet.get("description")),
        _text(post.get("caption")),
        _text(post.get("text")),
        _text(post.get("biography")),
        _text(post.get("bio")),
        _text(post.get("primary_topic")),
        _text(post.get("secondary_topics_json")),
        _text(post.get("brand_collaborations_json")),
        _text(post.get("potential_concerns_json")),
        _text(branding_channel.get("keywords")),
        _text(branding_channel.get("description")),
    ]
    hashtags = post.get("hashtags")
    if isinstance(hashtags, list):
        values.extend(_text(item) for item in hashtags)
    tags = snippet.get("tags")
    if isinstance(tags, list):
        values.extend(_text(item) for item in tags)
    return " ".join(item for item in values if item)


def _row_profile_post(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"kol_pool:{row.get('id')}:profile",
        "title": _first_text(row.get("display_name"), row.get("handle")),
        "description": " ".join(
            item
            for item in (
                _text(row.get("bio")),
                _text(row.get("primary_topic")),
                _text(row.get("secondary_topics_json")),
                _text(row.get("brand_collaborations_json")),
                _text(row.get("potential_concerns_json")),
                _text(row.get("recommended_product_lines_json")),
            )
            if item
        ),
        "published_at": _first_text(row.get("last_seen_at"), row.get("updated_at"), row.get("created_at")),
        "source": "vkpi_kol_pool.profile_fields",
    }


def _depth_for_text(text: str, brand: str) -> str:
    lowered = f" {text.lower()} "
    if any(pattern in lowered for pattern in SPONSORED_PATTERNS) and brand in lowered:
        return "sponsored"
    if any(pattern in lowered for pattern in GIFTED_PATTERNS) and brand in lowered:
        return "gifted"
    if any(pattern in lowered for pattern in EVALUATED_PATTERNS):
        return "evaluated"
    return "mentioned"


def _sentiment_for_text(text: str) -> str:
    lowered = text.lower()
    positive = any(pattern in lowered for pattern in POSITIVE_PATTERNS)
    negative = any(pattern in lowered for pattern in NEGATIVE_PATTERNS)
    if positive and not negative:
        return "positive"
    if negative and not positive:
        return "negative"
    return "neutral"


def _extract_posts(raw_platform_data: dict[str, Any]) -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    videos = raw_platform_data.get("videos")
    if isinstance(videos, list):
        posts.extend(item for item in videos if isinstance(item, dict))
    profile = raw_platform_data.get("profile")
    if isinstance(profile, dict):
        for key in ("items", "posts", "latestPosts", "videos"):
            items = profile.get(key)
            if isinstance(items, list):
                posts.extend(item for item in items if isinstance(item, dict))
        raw = profile.get("raw")
        if isinstance(raw, dict):
            for key in ("items", "posts", "latestPosts"):
                items = raw.get(key)
                if isinstance(items, list):
                    posts.extend(item for item in items if isinstance(item, dict))
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for post in posts:
        key = _first_text(post.get("id"), post.get("url"), post.get("shortCode"), _post_title(post))
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        deduped.append(post)
    return deduped


def detect_competitor_mentions(post: dict[str, Any], *, platform: str = "") -> list[dict[str, Any]]:
    text_blob = _post_text_blob(post)
    if not text_blob:
        return []
    brands = load_competitor_brands()
    mentions: list[dict[str, Any]] = []
    for brand, config in brands.items():
        matched = [keyword for keyword in config["keywords"] if _keyword_match(text_blob, keyword)]
        if not matched:
            continue
        mention = {
            "brand": brand,
            "depth": _depth_for_text(text_blob, brand),
            "sentiment": _sentiment_for_text(text_blob),
            "matched_keywords": matched[:6],
            "post_uid": _first_text(post.get("id"), post.get("shortCode"), post.get("url"), _post_title(post)),
            "title": _post_title(post),
            "url": _post_url(post, platform),
            "published_at": _post_date(post),
            "evidence": text_blob[:600],
        }
        mentions.append(mention)
    return mentions


def _risk_score(depth: str, recency_days: int | None, count_90d: int, count_total: int, sentiment: str) -> float:
    score = 0.0
    if depth == "sponsored":
        score += 4.0
    elif depth == "gifted":
        score += 3.0
    elif depth == "evaluated":
        score += 2.0
    elif depth == "mentioned":
        score += 0.5
    if recency_days is not None:
        if recency_days < 30:
            score += 2.0
        elif recency_days < 90:
            score += 1.0
        elif recency_days < 180:
            score += 0.5
    if count_90d >= 3:
        score += 2.0
    elif count_90d >= 1:
        score += 1.0
    if count_total >= 10:
        score += 2.0
    elif count_total >= 3:
        score += 1.0
    if sentiment == "positive":
        score += 1.0
    elif sentiment == "negative":
        score -= 1.0
    return min(10.0, max(0.0, round(score, 1)))


def _risk_tier(score: float) -> str:
    if score >= 7:
        return "avoid"
    if score >= 4:
        return "caution"
    if score >= 1:
        return "safe"
    return "opportunity"


def _strongest_depth(mentions: list[dict[str, Any]]) -> str:
    order = {"sponsored": 4, "gifted": 3, "evaluated": 2, "mentioned": 1, "none": 0}
    return max((str(item.get("depth") or "mentioned") for item in mentions), key=lambda value: order.get(value, 0), default="none")


def _dominant_sentiment(mentions: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for item in mentions:
        key = str(item.get("sentiment") or "neutral")
        counts[key] = counts.get(key, 0) + 1
    return max(counts, key=counts.get) if counts else "neutral"


def _kol_pool_row(kol_pool_id: int) -> dict[str, Any]:
    row = get_conn().execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    if not row:
        raise LookupError("kol pool item not found")
    return dict(row)


def evaluate_kol_competitor_relation(kol_pool_id: int, brand: str) -> dict[str, Any]:
    row = _kol_pool_row(kol_pool_id)
    platform = _text(row.get("platform")).lower()
    raw = _loads(row.get("raw_platform_data"), {})
    posts = [_row_profile_post(row), *_extract_posts(raw if isinstance(raw, dict) else {})]
    target = _text(brand).lower()
    mentions = [
        mention
        for post in posts
        for mention in detect_competitor_mentions(post, platform=platform)
        if mention.get("brand") == target
    ]
    mention_dates = [_days_since(item.get("published_at")) for item in mentions]
    mention_dates = [days for days in mention_dates if days is not None]
    recency_days = min(mention_dates) if mention_dates else None
    count_90d = sum(1 for days in mention_dates if days is not None and days <= 90)
    depth = _strongest_depth(mentions)
    sentiment = _dominant_sentiment(mentions)
    score = _risk_score(depth, recency_days, count_90d, len(mentions), sentiment)
    return {
        "kol_pool_id": int(kol_pool_id),
        "kol_entity_uid": _first_text(row.get("pool_uid"), f"kol_pool:{kol_pool_id}"),
        "platform": platform,
        "handle": _text(row.get("handle")),
        "display_name": _text(row.get("display_name")),
        "competitor_brand": target,
        "collaboration_depth": depth,
        "collaboration_recency_days": recency_days,
        "collaboration_count_90d": count_90d,
        "collaboration_count_total": len(mentions),
        "sentiment": sentiment,
        "risk_score": score,
        "risk_tier": _risk_tier(score),
        "evidence_post_uids": [item.get("post_uid") for item in mentions[:20]],
        "evidence": mentions[:8],
        "computed_at": _utcnow(),
        "source": "vkpi_kol_pool.raw_platform_data",
        "provider_calls": False,
    }


def persist_competitor_relations(relations: list[dict[str, Any]]) -> int:
    if not relations:
        return 0
    ensure_competitor_relation_schema()
    conn = get_conn()
    committed = 0
    for relation in relations:
        kol_pool_id = int(relation.get("kol_pool_id") or 0)
        brand = _text(relation.get("competitor_brand")).lower()
        if not kol_pool_id or not brand:
            continue
        conn.execute(
            """
            INSERT INTO vkpi_competitor_relation (
                kol_pool_id, kol_entity_uid, platform, handle, display_name,
                competitor_brand, collaboration_depth, collaboration_recency_days,
                collaboration_count_90d, collaboration_count_total, sentiment,
                risk_score, risk_tier, evidence_post_uids_json, evidence_json,
                last_evidence_at, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(kol_pool_id, competitor_brand) DO UPDATE SET
                kol_entity_uid=excluded.kol_entity_uid,
                platform=excluded.platform,
                handle=excluded.handle,
                display_name=excluded.display_name,
                collaboration_depth=excluded.collaboration_depth,
                collaboration_recency_days=excluded.collaboration_recency_days,
                collaboration_count_90d=excluded.collaboration_count_90d,
                collaboration_count_total=excluded.collaboration_count_total,
                sentiment=excluded.sentiment,
                risk_score=excluded.risk_score,
                risk_tier=excluded.risk_tier,
                evidence_post_uids_json=excluded.evidence_post_uids_json,
                evidence_json=excluded.evidence_json,
                last_evidence_at=excluded.last_evidence_at,
                computed_at=excluded.computed_at,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                kol_pool_id,
                _first_text(relation.get("kol_entity_uid"), f"kol_pool:{kol_pool_id}"),
                _text(relation.get("platform")).lower(),
                _text(relation.get("handle")),
                _text(relation.get("display_name")),
                brand,
                _text(relation.get("collaboration_depth")) or "none",
                relation.get("collaboration_recency_days"),
                int(relation.get("collaboration_count_90d") or 0),
                int(relation.get("collaboration_count_total") or 0),
                _text(relation.get("sentiment")) or "neutral",
                float(relation.get("risk_score") or 0),
                _text(relation.get("risk_tier")) or "opportunity",
                _json(relation.get("evidence_post_uids") or []),
                _json(relation.get("evidence") or []),
                _last_evidence_at(relation),
                _text(relation.get("computed_at")) or _utcnow(),
            ),
        )
        committed += 1
    conn.commit()
    return committed


def evaluate_kol_competitors(kol_pool_id: int, *, write_db: bool = False) -> dict[str, Any]:
    brands = sorted(load_competitor_brands())
    relations = [evaluate_kol_competitor_relation(kol_pool_id, brand) for brand in brands]
    committed = persist_competitor_relations(relations) if write_db else 0
    strongest = max(relations, key=lambda item: float(item.get("risk_score") or 0), default=None)
    return {
        "kol_pool_id": int(kol_pool_id),
        "provider_calls": False,
        "write_db": bool(write_db),
        "committed_relations": committed,
        "relations": relations,
        "summary": strongest or {},
    }


def batch_evaluate_kol_pool(*, brand: str = "", limit: int = 100, source_type: str = "legacy_excel_p2d", write_db: bool = False) -> dict[str, Any]:
    safe_limit = max(1, min(1200, int(limit or 100)))
    brands = [brand.strip().lower()] if brand.strip() else sorted(load_competitor_brands())
    rows = get_conn().execute(
        """
        SELECT id
        FROM vkpi_kol_pool
        WHERE COALESCE(raw_platform_data, '') <> ''
          AND (? = '' OR source_type = ?)
        ORDER BY COALESCE(last_seen_at, updated_at, created_at) DESC, id DESC
        LIMIT ?
        """,
        (source_type, source_type, safe_limit),
    ).fetchall()
    tier_counts = {"avoid": 0, "caution": 0, "safe": 0, "opportunity": 0}
    brand_counts: dict[str, dict[str, int]] = {item: dict(tier_counts) for item in brands}
    samples: list[dict[str, Any]] = []
    relations_to_write: list[dict[str, Any]] = []
    evaluated = 0
    for row in rows:
        kol_id = int(row["id"])
        for target_brand in brands:
            relation = evaluate_kol_competitor_relation(kol_id, target_brand)
            if write_db:
                relations_to_write.append(relation)
            evaluated += 1
            tier = str(relation.get("risk_tier") or "opportunity")
            brand_counts.setdefault(target_brand, dict(tier_counts))
            brand_counts[target_brand][tier] = brand_counts[target_brand].get(tier, 0) + 1
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
            if relation.get("risk_score") and len(samples) < 20:
                samples.append(relation)
    committed = persist_competitor_relations(relations_to_write) if write_db else 0
    return {
        "provider_calls": False,
        "write_db": bool(write_db),
        "committed_relations": committed,
        "source_type": source_type,
        "kol_rows": len(rows),
        "relations_evaluated": evaluated,
        "brands": brands,
        "tier_counts": tier_counts,
        "brand_counts": brand_counts,
        "samples": samples,
    }
