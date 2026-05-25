"""Rule-only competitor relation detection for KOL pool rows.

This module reads already-cached local KOL data. It does not call providers,
does not call LLMs, and does not write by default.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.services.cache import cache_clear, cache_get, cache_set
from app.domains.kol.competitor_text import (
    _extract_posts,
    _first_text,
    _loads,
    _post_date,
    _post_text_blob,
    _post_title,
    _post_url,
    _row_profile_post,
    _text,
    detect_competitor_mentions,
    load_competitor_brands,
)


COMPETITOR_DASHBOARD_CACHE_TTL_SEC = 300
CACHE_PREFIX_COMPETITOR_DASHBOARD = "vkpi:competitors:dashboard:"
_RELATION_SCHEMA_READY = False


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


def _relation_table_exists() -> bool:
    try:
        row = get_conn().execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            ("vkpi_competitor_relation",),
        ).fetchone()
    except Exception:
        return False
    return bool(row)


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
    if committed:
        cache_clear(CACHE_PREFIX_COMPETITOR_DASHBOARD)
    return committed


def _relation_from_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    return {
        "kol_pool_id": int(item.get("kol_pool_id") or 0),
        "kol_entity_uid": _text(item.get("kol_entity_uid")),
        "platform": _text(item.get("platform")),
        "handle": _text(item.get("handle")),
        "display_name": _text(item.get("display_name")),
        "competitor_brand": _text(item.get("competitor_brand")),
        "collaboration_depth": _text(item.get("collaboration_depth")) or "none",
        "collaboration_recency_days": item.get("collaboration_recency_days"),
        "collaboration_count_90d": int(item.get("collaboration_count_90d") or 0),
        "collaboration_count_total": int(item.get("collaboration_count_total") or 0),
        "sentiment": _text(item.get("sentiment")) or "neutral",
        "risk_score": float(item.get("risk_score") or 0),
        "risk_tier": _text(item.get("risk_tier")) or "opportunity",
        "evidence_post_uids": _loads(item.get("evidence_post_uids_json"), []),
        "evidence": _loads(item.get("evidence_json"), []),
        "last_evidence_at": _text(item.get("last_evidence_at")),
        "computed_at": _text(item.get("computed_at")),
        "source": "vkpi_competitor_relation",
        "provider_calls": False,
    }


def get_persisted_kol_competitors(kol_pool_id: int) -> dict[str, Any]:
    if not _relation_table_exists():
        return {"persisted": False, "relations": []}
    rows = get_conn().execute(
        """
        SELECT *
        FROM vkpi_competitor_relation
        WHERE kol_pool_id=?
        ORDER BY risk_score DESC, competitor_brand ASC
        """,
        (int(kol_pool_id),),
    ).fetchall()
    relations = [_relation_from_row(row) for row in rows]
    strongest = max(relations, key=lambda item: float(item.get("risk_score") or 0), default=None)
    return {
        "kol_pool_id": int(kol_pool_id),
        "provider_calls": False,
        "write_db": False,
        "persisted": bool(relations),
        "relations": relations,
        "summary": strongest or {},
    }


def evaluate_kol_competitors(kol_pool_id: int, *, write_db: bool = False, prefer_persisted: bool = False) -> dict[str, Any]:
    if prefer_persisted and not write_db:
        persisted = get_persisted_kol_competitors(kol_pool_id)
        if persisted.get("persisted"):
            return persisted
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


def persisted_competitor_dashboard(*, brand: str = "", limit: int = 1200) -> dict[str, Any]:
    safe_limit = max(1, min(1200, int(limit or 1200)))
    target_brand = brand.strip().lower()
    cache_key = f"{CACHE_PREFIX_COMPETITOR_DASHBOARD}{target_brand or 'all'}:limit:{safe_limit}"
    cached = cache_get(cache_key)
    if isinstance(cached, dict):
        return {
            **cached,
            "cache": {"hit": True, "ttl_sec": COMPETITOR_DASHBOARD_CACHE_TTL_SEC},
        }
    if not _relation_table_exists():
        return {"persisted": False, "relations_evaluated": 0}
    rows = get_conn().execute(
        """
        SELECT *
        FROM vkpi_competitor_relation
        WHERE (? = '' OR competitor_brand = ?)
        ORDER BY risk_score DESC, computed_at DESC, id DESC
        LIMIT ?
        """,
        (target_brand, target_brand, safe_limit * max(1, len(load_competitor_brands()))),
    ).fetchall()
    tier_counts = {"avoid": 0, "caution": 0, "safe": 0, "opportunity": 0}
    brand_counts: dict[str, dict[str, int]] = {}
    samples: list[dict[str, Any]] = []
    for row in rows:
        relation = _relation_from_row(row)
        relation_brand = str(relation.get("competitor_brand") or "")
        tier = str(relation.get("risk_tier") or "opportunity")
        brand_counts.setdefault(relation_brand, dict(tier_counts))
        brand_counts[relation_brand][tier] = brand_counts[relation_brand].get(tier, 0) + 1
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        if relation.get("risk_score") and len(samples) < 20:
            samples.append(relation)
    result = {
        "provider_calls": False,
        "write_db": False,
        "persisted": bool(rows),
        "source_type": "vkpi_competitor_relation",
        "kol_rows": len({int(row["kol_pool_id"]) for row in rows if row["kol_pool_id"]}),
        "relations_evaluated": len(rows),
        "brands": sorted(brand_counts),
        "tier_counts": tier_counts,
        "brand_counts": brand_counts,
        "samples": samples,
        "cache": {"hit": False, "ttl_sec": COMPETITOR_DASHBOARD_CACHE_TTL_SEC},
    }
    cache_set(cache_key, result, ttl=COMPETITOR_DASHBOARD_CACHE_TTL_SEC)
    return result


def batch_evaluate_kol_pool(
    *,
    brand: str = "",
    limit: int = 100,
    source_type: str = "legacy_excel_p2d",
    write_db: bool = False,
    prefer_persisted: bool = False,
) -> dict[str, Any]:
    if prefer_persisted and not write_db:
        persisted = persisted_competitor_dashboard(brand=brand, limit=limit)
        if persisted.get("persisted"):
            return persisted
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
