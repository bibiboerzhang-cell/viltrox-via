"""Rule-only Viltrox and competitor signal detection from cached posts.

This module is intentionally local-data only:
- no provider calls;
- no LLM calls;
- no writes unless the caller explicitly asks for ``write_db=True``.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn
from app.domains.kol.competitor_text import (
    _extract_posts,
    _post_date,
    _post_text_blob,
    _post_title,
    _post_url,
    _row_profile_post,
    detect_competitor_mentions,
)


VILTROX_TERMS = (
    "viltrox",
    "@viltrox",
    "viltrox official",
    "viltroxofficial",
    "viltrox.global",
    "viltrox_global",
    "viltrox studio",
    "weeylite",
)

PRODUCT_TERMS: dict[str, tuple[str, ...]] = {
    "AF-35MM-F12-LAB": ("af 35mm f1.2 lab", "35mm f1.2 lab", "35mm f/1.2 lab", "35mm f1.2", "35mm f/1.2"),
    "AF-16MM-F18": ("af 16mm f1.8", "16mm f1.8", "16mm f/1.8"),
    "AF-20MM-F28": ("af 20mm f2.8", "20mm f2.8", "20mm f/2.8"),
    "AF-27MM-F12": ("af 27mm f1.2", "27mm f1.2", "27mm f/1.2"),
    "AF-56MM-F17": ("af 56mm f1.7", "56mm f1.7", "56mm f/1.7"),
    "AF-75MM-F12": ("af 75mm f1.2", "75mm f1.2", "75mm f/1.2"),
    "AF-85MM-F18": ("af 85mm f1.8", "85mm f1.8", "85mm f/1.8"),
    "AF-90MM-F35-DL": ("af 90mm f3.5 dl", "90mm f3.5 dl", "90mm f/3.5 dl"),
    "AF-135MM-F18-LAB": ("af 135mm f1.8 lab", "135mm f1.8 lab", "135mm f/1.8 lab"),
    "EPIC-MAESTRO": ("epic maestro", "maestro 1.33x"),
    "EPIC-MEMENTO": ("epic memento", "memento 1.33x"),
    "NEXUSFOCUS-F1": ("nexusfocus f1", "nexus focus f1"),
}

COMMENT_KEYS = ("comments", "latestComments", "topComments", "latest_comments", "comment_items")


from app.core.coerce import _loads, _text


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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


def _ensure_brand_signal_table() -> None:
    get_conn().execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_brand_signal (
            id BIGSERIAL PRIMARY KEY,
            signal_uid TEXT NOT NULL UNIQUE,
            kol_entity_uid TEXT NOT NULL DEFAULT '',
            post_uid TEXT NOT NULL DEFAULT '',
            source_table TEXT NOT NULL DEFAULT '',
            source_id BIGINT,
            post_url TEXT DEFAULT '',
            platform TEXT DEFAULT '',
            published_at TIMESTAMPTZ,
            analysis_scope TEXT NOT NULL DEFAULT 'current_year',
            signal_type TEXT NOT NULL,
            brand_name TEXT NOT NULL,
            brand_role TEXT NOT NULL,
            signal_strength TEXT NOT NULL DEFAULT 'medium',
            evidence_json TEXT NOT NULL DEFAULT '{}',
            detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            is_new BOOLEAN NOT NULL DEFAULT TRUE,
            reviewed_at TIMESTAMPTZ,
            reviewed_by INTEGER,
            action_taken TEXT DEFAULT '',
            UNIQUE(kol_entity_uid, post_uid, signal_type, brand_name)
        )
        """
    )
    get_conn().execute("CREATE INDEX IF NOT EXISTS idx_vkpi_brand_signal_new ON vkpi_brand_signal(is_new, detected_at DESC)")
    get_conn().execute("CREATE INDEX IF NOT EXISTS idx_vkpi_brand_signal_kol ON vkpi_brand_signal(kol_entity_uid, detected_at DESC)")
    get_conn().execute("CREATE INDEX IF NOT EXISTS idx_vkpi_brand_signal_type ON vkpi_brand_signal(signal_type, brand_role, detected_at DESC)")
    get_conn().commit()


def _safe_limit(value: Any, default: int = 200, ceiling: int = 2000) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(ceiling, parsed))


def _parse_date(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    normalized = raw.replace(",", "").strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", normalized):
        try:
            timestamp = float(normalized)
        except ValueError:
            timestamp = 0
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        try:
            parsed_epoch = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            parsed_epoch = None
        if parsed_epoch and 1970 <= parsed_epoch.year <= 2100:
            return parsed_epoch
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


def _date_for_db(value: Any) -> str:
    parsed = _parse_date(value)
    if not parsed:
        return ""
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _current_year_scope(value: Any) -> str:
    parsed = _parse_date(value)
    if not parsed:
        return "unknown_date"
    return "current_year" if parsed.year == datetime.now(timezone.utc).year else "history_record"


def _since_clause(field: str, since: str, params: list[Any]) -> str:
    if not since:
        return ""
    params.append(since)
    return f" AND {field} >= ?"


def _keyword_match(text: str, keyword: str) -> bool:
    lowered = text.lower()
    key = keyword.lower().strip()
    if not key:
        return False
    if key.startswith("@") or "." in key or " " in key:
        return key in lowered
    return re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", lowered) is not None


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _post_uid(post: dict[str, Any], fallback: str) -> str:
    return _first_text(post.get("id"), post.get("post_uid"), post.get("shortCode"), post.get("shortcode"), post.get("url"), _post_title(post), fallback)


def _comments_from_post(post: dict[str, Any]) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    for key in COMMENT_KEYS:
        items = post.get(key)
        if isinstance(items, list):
            comments.extend(item for item in items if isinstance(item, dict))
    raw = post.get("raw") if isinstance(post.get("raw"), dict) else {}
    for key in COMMENT_KEYS:
        items = raw.get(key)
        if isinstance(items, list):
            comments.extend(item for item in items if isinstance(item, dict))
    return comments[:100]


def _comment_text(comment: dict[str, Any]) -> str:
    return _first_text(comment.get("text"), comment.get("comment"), comment.get("body"), comment.get("message"), comment.get("content"))


def _signal_uid(signal: dict[str, Any]) -> str:
    base = "|".join(
        _text(signal.get(key))
        for key in ("kol_entity_uid", "post_uid", "signal_type", "brand_name", "source_table", "source_id")
    )
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def detect_viltrox_signals(post: dict[str, Any], *, context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    context = context or {}
    platform = _text(context.get("platform")).lower()
    post_uid = _post_uid(post, _text(context.get("source_id")) or "post")
    post_url = _first_text(post.get("post_url"), post.get("url"), _post_url(post, platform))
    published_at = _first_text(post.get("published_at"), post.get("publishedAt"), _post_date(post))
    text_blob = " ".join(
        item for item in (
            _post_text_blob(post),
            _text(post.get("hashtags_json")),
            _text(post.get("mentions_json")),
            _text(post.get("brand_mentions_json")),
            _text(post.get("detected_products_json")),
        )
        if item
    )
    signals: list[dict[str, Any]] = []
    matched_viltrox = [term for term in VILTROX_TERMS if _keyword_match(text_blob, term)]
    if matched_viltrox:
        signals.append(_build_signal(
            context,
            post_uid=post_uid,
            post_url=post_url,
            published_at=published_at,
            signal_type="mention_viltrox",
            brand_name="viltrox",
            brand_role="self",
            signal_strength="high",
            evidence={"match_text": matched_viltrox[:8], "match_source": "caption_hashtag_profile", "match_context": text_blob[:700]},
        ))
    for sku, terms in PRODUCT_TERMS.items():
        matched_terms = [term for term in terms if _keyword_match(text_blob, term)]
        if matched_terms:
            strength = "high" if matched_viltrox or "viltrox" in text_blob.lower() else "medium"
            signals.append(_build_signal(
                context,
                post_uid=post_uid,
                post_url=post_url,
                published_at=published_at,
                signal_type="show_product",
                brand_name=sku,
                brand_role="self",
                signal_strength=strength,
                evidence={"match_text": matched_terms[:8], "match_source": "caption_hashtag_profile", "match_context": text_blob[:700]},
            ))
    for comment in _comments_from_post(post):
        comment_text = _comment_text(comment)
        matched_comment = [term for term in VILTROX_TERMS if _keyword_match(comment_text, term)]
        if not matched_comment:
            continue
        signals.append(_build_signal(
            context,
            post_uid=post_uid,
            post_url=post_url,
            published_at=published_at,
            signal_type="comment_mention",
            brand_name="viltrox",
            brand_role="self",
            signal_strength="medium",
            evidence={"match_text": matched_comment[:8], "match_source": "comment", "match_context": comment_text[:700]},
        ))
        break
    for mention in detect_competitor_mentions(post, platform=platform):
        signals.append(_build_signal(
            context,
            post_uid=post_uid,
            post_url=post_url,
            published_at=published_at,
            signal_type="mention_competitor",
            brand_name=_text(mention.get("brand")),
            brand_role="competitor",
            signal_strength="high" if mention.get("depth") in {"sponsored", "gifted", "evaluated"} else "medium",
            evidence={
                "match_text": mention.get("matched_keywords") or [],
                "match_source": "caption_hashtag_profile",
                "match_context": _text(mention.get("evidence"))[:700],
                "depth": mention.get("depth"),
                "sentiment": mention.get("sentiment"),
            },
        ))
    return signals


def _build_signal(
    context: dict[str, Any],
    *,
    post_uid: str,
    post_url: str,
    published_at: str,
    signal_type: str,
    brand_name: str,
    brand_role: str,
    signal_strength: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    published_at = _date_for_db(published_at)
    signal = {
        "kol_entity_uid": _text(context.get("kol_entity_uid")),
        "post_uid": post_uid,
        "source_table": _text(context.get("source_table")),
        "source_id": context.get("source_id"),
        "post_url": post_url,
        "platform": _text(context.get("platform")),
        "published_at": published_at,
        "analysis_scope": _current_year_scope(published_at),
        "signal_type": signal_type,
        "brand_name": brand_name,
        "brand_role": brand_role,
        "signal_strength": signal_strength,
        "evidence_json": _json(evidence),
        "detected_at": _utcnow(),
    }
    signal["signal_uid"] = _signal_uid(signal)
    return signal


def _industry_post_records(limit: int, since: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if not _table_exists("vkpi_industry_posts"):
        return []
    params: list[Any] = []
    rows = get_conn().execute(
        f"""
        SELECT id, platform, post_uid, platform_post_id, post_url, title, caption, published_at,
               hashtags_json, mentions_json, detected_products_json, brand_mentions_json, raw_platform_data
        FROM vkpi_industry_posts
        WHERE 1=1{_since_clause("COALESCE(published_at, created_at)", since, params)}
        ORDER BY COALESCE(published_at, created_at) DESC, id DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    records: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in rows:
        data = dict(row)
        raw = _loads(data.get("raw_platform_data"), {})
        post = {**(raw if isinstance(raw, dict) else {}), **data}
        context = {
            "kol_entity_uid": f"industry_post:{data.get('id')}",
            "source_table": "vkpi_industry_posts",
            "source_id": data.get("id"),
            "platform": data.get("platform"),
        }
        records.append((post, context))
    return records


def _kol_pool_records(limit: int, since: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if not _table_exists("vkpi_kol_pool"):
        return []
    params: list[Any] = []
    rows = get_conn().execute(
        f"""
        SELECT *
        FROM vkpi_kol_pool
        WHERE COALESCE(raw_platform_data, '') <> ''{_since_clause("COALESCE(last_seen_at, updated_at, created_at)", since, params)}
        ORDER BY COALESCE(last_seen_at, updated_at, created_at) DESC, id DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    records: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in rows:
        data = dict(row)
        raw = _loads(data.get("raw_platform_data"), {})
        posts = [_row_profile_post(data), *_extract_posts(raw if isinstance(raw, dict) else {})]
        for index, post in enumerate(posts[:50]):
            context = {
                "kol_entity_uid": f"kol_pool:{data.get('id')}",
                "source_table": "vkpi_kol_pool",
                "source_id": data.get("id"),
                "platform": data.get("platform"),
            }
            if not post.get("id"):
                post = {**post, "id": f"kol_pool:{data.get('id')}:{index}"}
            records.append((post, context))
    return records


def _channel_metric_records(limit: int, since: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if not _table_exists("vkpi_channel_metrics"):
        return []
    params: list[Any] = []
    rows = get_conn().execute(
        f"""
        SELECT m.id, m.channel_id, m.raw_payload_json, m.captured_at, c.platform, c.account_handle, c.account_display_name
        FROM vkpi_channel_metrics m
        LEFT JOIN vkpi_employee_channels c ON c.id=m.channel_id
        WHERE COALESCE(m.raw_payload_json, '') <> ''{_since_clause("m.captured_at", since, params)}
        ORDER BY m.captured_at DESC, m.id DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    records: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in rows:
        data = dict(row)
        raw = _loads(data.get("raw_payload_json"), {})
        items: list[dict[str, Any]] = []
        if isinstance(raw, dict):
            raw_sample = raw.get("raw_sample") if isinstance(raw.get("raw_sample"), dict) else {}
            for key in ("items", "posts", "videos", "latestPosts"):
                value = raw_sample.get(key) or raw.get(key)
                if isinstance(value, list):
                    items.extend(item for item in value if isinstance(item, dict))
            if raw_sample and not items:
                items.append(raw_sample)
        if not items and isinstance(raw, dict):
            items.append(raw)
        for index, post in enumerate(items[:50]):
            if not post.get("id"):
                post = {**post, "id": f"channel_metric:{data.get('id')}:{index}", "published_at": data.get("captured_at")}
            context = {
                "kol_entity_uid": f"channel:{data.get('channel_id')}",
                "source_table": "vkpi_channel_metrics",
                "source_id": data.get("id"),
                "platform": data.get("platform"),
            }
            records.append((post, context))
    return records


def _records(source: str, limit: int, since: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    source = source.strip().lower() or "all"
    buckets: list[tuple[dict[str, Any], dict[str, Any]]] = []
    if source in {"all", "industry_posts", "industry"}:
        buckets.extend(_industry_post_records(limit, since))
    if source in {"all", "kol_pool", "kol"}:
        buckets.extend(_kol_pool_records(limit, since))
    if source in {"all", "channel_metrics", "official"}:
        buckets.extend(_channel_metric_records(limit, since))
    return buckets[: max(1, limit * 60)]


def scan_cached_brand_signals(*, limit: int = 200, source: str = "all", since: str = "", write_db: bool = False) -> dict[str, Any]:
    safe_limit = _safe_limit(limit)
    detected: list[dict[str, Any]] = []
    scanned = 0
    for post, context in _records(source, safe_limit, since):
        scanned += 1
        detected.extend(detect_viltrox_signals(post, context=context))
    deduped: dict[str, dict[str, Any]] = {}
    for signal in detected:
        deduped[signal["signal_uid"]] = signal
    signals = list(deduped.values())
    committed = 0
    if write_db:
        committed = commit_brand_signals(signals)
    counts: dict[str, int] = {}
    role_counts: dict[str, int] = {}
    scope_counts: dict[str, int] = {}
    for signal in signals:
        counts[signal["signal_type"]] = counts.get(signal["signal_type"], 0) + 1
        role_counts[signal["brand_role"]] = role_counts.get(signal["brand_role"], 0) + 1
        scope_counts[signal["analysis_scope"]] = scope_counts.get(signal["analysis_scope"], 0) + 1
    return {
        "provider_calls": False,
        "llm_calls": False,
        "write_db": bool(write_db),
        "source": source or "all",
        "since": since,
        "scanned_records": scanned,
        "signals_detected": len(signals),
        "signals_committed": committed,
        "counts": counts,
        "role_counts": role_counts,
        "scope_counts": scope_counts,
        "signals": signals[:100],
    }


def commit_brand_signals(signals: list[dict[str, Any]]) -> int:
    if not signals:
        return 0
    _ensure_brand_signal_table()
    conn = get_conn()
    committed = 0
    for signal in signals:
        existing = conn.execute(
            """
            SELECT id
            FROM vkpi_brand_signal
            WHERE signal_uid=?
               OR (kol_entity_uid=? AND post_uid=? AND signal_type=? AND brand_name=?)
            LIMIT 1
            """,
            (
                signal["signal_uid"],
                signal["kol_entity_uid"],
                signal["post_uid"],
                signal["signal_type"],
                signal["brand_name"],
            ),
        ).fetchone()
        params = (
            signal["signal_uid"],
            signal["kol_entity_uid"],
            signal["post_uid"],
            signal["source_table"],
            signal.get("source_id"),
            signal["post_url"],
            signal["platform"],
            signal["published_at"] or None,
            signal["analysis_scope"],
            signal["signal_type"],
            signal["brand_name"],
            signal["brand_role"],
            signal["signal_strength"],
            signal["evidence_json"],
            signal["detected_at"],
        )
        if existing:
            conn.execute(
                """
                UPDATE vkpi_brand_signal
                SET signal_uid=?, kol_entity_uid=?, post_uid=?, source_table=?, source_id=?, post_url=?, platform=?,
                    published_at=?, analysis_scope=?, signal_type=?, brand_name=?, brand_role=?,
                    signal_strength=?, evidence_json=?, detected_at=?
                WHERE id=?
                """,
                params + (existing["id"],),
            )
        else:
            conn.execute(
                """
                INSERT INTO vkpi_brand_signal (
                    signal_uid, kol_entity_uid, post_uid, source_table, source_id, post_url, platform,
                    published_at, analysis_scope, signal_type, brand_name, brand_role, signal_strength,
                    evidence_json, detected_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                params,
            )
        committed += 1
    conn.commit()
    return committed


def _normalize_brand_signal_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    evidence = _loads(item.get("evidence_json"), {})
    if not isinstance(evidence, dict):
        evidence = {}
    matched = evidence.get("match_text") if isinstance(evidence.get("match_text"), list) else []
    item["evidence"] = evidence
    item["matched_text"] = ", ".join(_text(value) for value in matched[:6] if _text(value))
    item["match_context"] = _text(evidence.get("match_context"))[:700]
    item["match_source"] = _text(evidence.get("match_source"))
    item["evidence_depth"] = _text(evidence.get("depth"))
    item["evidence_sentiment"] = _text(evidence.get("sentiment"))
    item["source_url"] = _text(item.get("post_url"))
    return item


def list_brand_signals(*, status: str = "new", signal_type: str = "", brand_role: str = "", limit: int = 100) -> dict[str, Any]:
    if not _table_exists("vkpi_brand_signal"):
        return {"schema_ready": False, "signals": [], "count": 0}
    where: list[str] = []
    params: list[Any] = []
    if status == "new":
        where.append("is_new=TRUE")
    elif status == "reviewed":
        where.append("is_new=FALSE")
    if signal_type:
        where.append("signal_type=?")
        params.append(signal_type)
    if brand_role:
        where.append("brand_role=?")
        params.append(brand_role)
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = get_conn().execute(
        f"""
        SELECT *
        FROM vkpi_brand_signal
        {clause}
        ORDER BY detected_at DESC, id DESC
        LIMIT ?
        """,
        (*params, _safe_limit(limit, default=100, ceiling=500)),
    ).fetchall()
    count_row = get_conn().execute(
        f"""
        SELECT COUNT(*) AS n
        FROM vkpi_brand_signal
        {clause}
        """,
        tuple(params),
    ).fetchone()
    total_count = int(count_row["n"] if count_row else 0)
    return {"schema_ready": True, "signals": [_normalize_brand_signal_row(row) for row in rows], "count": len(rows), "total_count": total_count}


def review_brand_signal(signal_id: int, *, action: str, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    action = _text(action).lower()
    if action not in {"contact", "ignore", "flag", "compete"}:
        raise ValueError("action must be contact, ignore, flag, or compete")
    row = get_conn().execute("SELECT * FROM vkpi_brand_signal WHERE id=?", (int(signal_id),)).fetchone()
    if not row:
        raise LookupError("brand signal not found")
    staff_id = int((staff or {}).get("id") or (staff or {}).get("staff_id") or 0) or None
    now = _utcnow()
    get_conn().execute(
        """
        UPDATE vkpi_brand_signal
        SET is_new=FALSE, reviewed_at=?, reviewed_by=?, action_taken=?
        WHERE id=?
        """,
        (now, staff_id, action, int(signal_id)),
    )
    get_conn().commit()
    updated = get_conn().execute("SELECT * FROM vkpi_brand_signal WHERE id=?", (int(signal_id),)).fetchone()
    return dict(updated) if updated else {"id": int(signal_id), "action_taken": action}
