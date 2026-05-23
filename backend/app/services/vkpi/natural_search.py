"""P9 deterministic natural search over V-KPI tables."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi.kol_history_match import _avatar_from_raw, _recent_post_summary


SOURCE_WEIGHTS = {
    "kol_pool": 8,
    "memory_entity": 7,
    "memory_fact": 5,
    "recommendation": 6,
    "competitor_signal": 8,
    "alert": 6,
}

TOKEN_ALIASES = {
    "germany": ["德国"],
    "japan": ["日本"],
    "usa": ["美国"],
    "us": ["美国"],
    "united": ["美国"],
    "uk": ["英国"],
    "britain": ["英国"],
    "canada": ["加拿大"],
    "france": ["法国"],
    "italy": ["意大利"],
    "spain": ["西班牙"],
    "australia": ["澳大利亚"],
    "korea": ["韩国"],
    "brazil": ["巴西"],
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _tokens(query: str) -> list[str]:
    tokens = [token.lower() for token in re.findall(r"[\w\u4e00-\u9fff#@.+-]+", query or "")]
    expanded: list[str] = []
    for token in tokens:
        if len(token) < 2:
            continue
        expanded.append(token)
        expanded.extend(TOKEN_ALIASES.get(token, []))
    return list(dict.fromkeys(expanded))


def _row(row: Any) -> dict[str, Any]:
    return dict(row) if row else {}


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or ""))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _kol_pool_evidence(data: dict[str, Any], avatar_url: str, recent_posts: list[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "id",
        "platform",
        "handle",
        "display_name",
        "country",
        "bio",
        "profile_url",
        "sync_status",
        "followers",
        "posts_count",
        "avg_views",
        "avg_likes",
        "avg_comments",
        "engagement_rate",
        "last_seen_at",
        "updated_at",
    )
    evidence = {key: data.get(key) for key in keys}
    evidence["avatar_url"] = avatar_url
    evidence["recent_posts"] = recent_posts
    return evidence


def _score(result_type: str, title: str, primary: list[Any], evidence: list[Any], tokens: list[str]) -> int:
    title_l = _lower(title)
    primary_l = " ".join(_lower(value) for value in primary)
    evidence_l = " ".join(_lower(value) for value in evidence)
    score = SOURCE_WEIGHTS.get(result_type, 0)
    query_l = " ".join(tokens)
    if query_l and query_l in title_l:
        score += 30
    for token in tokens:
        if token in title_l or token in primary_l:
            score += 10
        elif token in evidence_l:
            score += 4
    return score


def _where_like(columns: list[str], tokens: list[str]) -> tuple[str, list[Any]]:
    parts = []
    params: list[Any] = []
    for token in tokens:
        likes = []
        for column in columns:
            likes.append(f"LOWER(COALESCE({column}, '')) LIKE ?")
            params.append(f"%{token}%")
        parts.append("(" + " OR ".join(likes) + ")")
    return " OR ".join(parts), params


def _search_kol_pool(tokens: list[str], limit: int) -> list[dict[str, Any]]:
    where, params = _where_like(["handle", "display_name", "platform", "country", "bio", "profile_url"], tokens)
    rows = get_conn().execute(
        f"""
        SELECT id, platform, handle, display_name, country, bio, profile_url, avatar_url,
               sync_status, followers, posts_count, avg_views, avg_likes, avg_comments,
               engagement_rate, raw_platform_data, last_seen_at, updated_at
        FROM vkpi_kol_pool
        WHERE {where}
        ORDER BY id DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    results = []
    for row in rows:
        data = _row(row)
        title = _text(data.get("display_name")) or _text(data.get("handle")) or f"KOL #{data.get('id')}"
        raw = _json_obj(data.get("raw_platform_data"))
        avatar_url = _text(data.get("avatar_url")) or _avatar_from_raw(raw)
        recent_posts = _recent_post_summary(raw, limit=4)
        evidence = _kol_pool_evidence(data, avatar_url, recent_posts)
        results.append(
            {
                "result_type": "kol_pool",
                "title": title,
                "score": _score("kol_pool", title, [data.get("handle"), data.get("platform"), data.get("country")], [data.get("bio")], tokens),
                "source_table": "vkpi_kol_pool",
                "source_id": data.get("id"),
                "platform": data.get("platform"),
                "handle": data.get("handle"),
                "profile_url": data.get("profile_url"),
                "avatar_url": avatar_url,
                "recent_posts": recent_posts,
                "followers": data.get("followers"),
                "posts_count": data.get("posts_count"),
                "sync_status": data.get("sync_status"),
                "evidence": evidence,
            }
        )
    return results


def _search_memory_entities(tokens: list[str], limit: int) -> list[dict[str, Any]]:
    where, params = _where_like(["display_name", "entity_type", "identity_key", "identity_json", "metadata_json"], tokens)
    rows = get_conn().execute(
        f"""
        SELECT id, entity_uid, entity_type, identity_key, display_name,
               source_table, source_id, identity_json, metadata_json
        FROM vkpi_memory_entities
        WHERE {where}
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    results = []
    for row in rows:
        data = _row(row)
        title = _text(data.get("display_name")) or _text(data.get("identity_key")) or _text(data.get("entity_uid"))
        results.append(
            {
                "result_type": "memory_entity",
                "title": title,
                "score": _score("memory_entity", title, [data.get("entity_type"), data.get("identity_key")], [data.get("identity_json"), data.get("metadata_json")], tokens),
                "source_table": "vkpi_memory_entities",
                "source_id": data.get("id"),
                "evidence": data,
            }
        )
    return results


def _search_memory_facts(tokens: list[str], limit: int) -> list[dict[str, Any]]:
    where, params = _where_like(["f.fact_type", "f.fact_key", "f.fact_value_text", "f.fact_json", "e.display_name", "e.identity_key"], tokens)
    rows = get_conn().execute(
        f"""
        SELECT f.id, f.fact_type, f.fact_key, f.fact_value_text, f.source_ref,
               f.source_table, f.source_id, f.fact_json,
               e.entity_uid, e.entity_type, e.display_name, e.identity_key
        FROM vkpi_memory_facts f
        JOIN vkpi_memory_entities e ON e.id = f.entity_id
        WHERE {where}
        ORDER BY f.observed_at DESC, f.id DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    results = []
    for row in rows:
        data = _row(row)
        title = f"{_text(data.get('fact_type'))}: {_text(data.get('display_name')) or _text(data.get('identity_key'))}"
        results.append(
            {
                "result_type": "memory_fact",
                "title": title,
                "score": _score("memory_fact", title, [data.get("fact_key"), data.get("fact_value_text")], [data.get("fact_json"), data.get("source_ref")], tokens),
                "source_table": "vkpi_memory_facts",
                "source_id": data.get("id"),
                "evidence": data,
            }
        )
    return results


def _search_recommendations(tokens: list[str], limit: int) -> list[dict[str, Any]]:
    where, params = _where_like(["handle", "display_name", "platform", "feature_snapshot_json", "scoring_breakdown_json", "explanation_json"], tokens)
    rows = get_conn().execute(
        f"""
        SELECT id, recommendation_uid, run_id, kol_pool_id, platform, handle, display_name,
               score, rank, status, feature_snapshot_json, scoring_breakdown_json, explanation_json
        FROM vkpi_kol_recommendations
        WHERE {where}
        ORDER BY score DESC, rank ASC, id DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    results = []
    for row in rows:
        data = _row(row)
        title = _text(data.get("display_name")) or _text(data.get("handle")) or _text(data.get("recommendation_uid"))
        results.append(
            {
                "result_type": "recommendation",
                "title": title,
                "score": _score("recommendation", title, [data.get("handle"), data.get("platform"), data.get("status")], [data.get("explanation_json"), data.get("scoring_breakdown_json")], tokens),
                "source_table": "vkpi_kol_recommendations",
                "source_id": data.get("id"),
                "evidence": data,
            }
        )
    return results


def _search_competitor_signals(tokens: list[str], limit: int) -> list[dict[str, Any]]:
    where, params = _where_like(["brand", "normalized_brand", "signal_type", "detail", "product_hints_json", "evidence_json"], tokens)
    rows = get_conn().execute(
        f"""
        SELECT id, signal_uid, brand, normalized_brand, signal_type, severity, score,
               product_hints_json, source_table, source_id, source_sheet, source_row,
               source_url, platform, detail, evidence_json, review_status
        FROM vkpi_competitor_signals
        WHERE {where}
        ORDER BY score DESC, id DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    results = []
    for row in rows:
        data = _row(row)
        title = f"{_text(data.get('normalized_brand') or data.get('brand'))} {_text(data.get('signal_type'))}"
        results.append(
            {
                "result_type": "competitor_signal",
                "title": title,
                "score": _score("competitor_signal", title, [data.get("brand"), data.get("signal_type")], [data.get("detail"), data.get("evidence_json")], tokens),
                "source_table": "vkpi_competitor_signals",
                "source_id": data.get("id"),
                "evidence": data,
            }
        )
    return results


def _search_alerts(tokens: list[str], limit: int) -> list[dict[str, Any]]:
    where, params = _where_like(["alert_key", "severity", "status", "title", "body", "rule_key", "metadata_json"], tokens)
    rows = get_conn().execute(
        f"""
        SELECT id, alert_key, severity, status, target_type, target_id,
               title, body, rule_key, metadata_json, created_at, updated_at
        FROM vkpi_alerts
        WHERE {where}
        ORDER BY status ASC, severity DESC, updated_at DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    results = []
    for row in rows:
        data = _row(row)
        title = _text(data.get("title")) or _text(data.get("alert_key"))
        results.append(
            {
                "result_type": "alert",
                "title": title,
                "score": _score("alert", title, [data.get("rule_key"), data.get("severity"), data.get("status")], [data.get("body"), data.get("metadata_json")], tokens),
                "source_table": "vkpi_alerts",
                "source_id": data.get("id"),
                "evidence": data,
            }
        )
    return results


def search(
    query: str,
    *,
    limit: int = 20,
    json_out: str = "",
    md_out: str = "",
) -> dict[str, Any]:
    tokens = _tokens(query)
    if not tokens:
        raise ValueError("query must contain at least one searchable token")
    safe_limit = max(1, min(100, int(limit or 20)))
    per_source_limit = max(10, safe_limit)
    items = []
    items.extend(_search_kol_pool(tokens, per_source_limit))
    items.extend(_search_memory_entities(tokens, per_source_limit))
    items.extend(_search_memory_facts(tokens, per_source_limit))
    items.extend(_search_recommendations(tokens, per_source_limit))
    items.extend(_search_competitor_signals(tokens, per_source_limit))
    items.extend(_search_alerts(tokens, per_source_limit))
    ranked = sorted(items, key=lambda item: (-int(item.get("score") or 0), item.get("result_type") or "", item.get("title") or ""))[:safe_limit]
    payload = {
        "query": query,
        "tokens": tokens,
        "provider_calls": False,
        "write_db": False,
        "total": len(ranked),
        "items": ranked,
    }
    markdown = format_search_summary(payload)
    payload["markdown"] = markdown
    if json_out:
        Path(json_out).write_text(json.dumps({k: v for k, v in payload.items() if k != "markdown"}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if md_out:
        Path(md_out).write_text(markdown, encoding="utf-8")
    return payload


def format_search_summary(payload: dict[str, Any]) -> str:
    lines = [
        "# P9 Natural Search",
        "",
        "```text",
        f"query={payload.get('query', '')}",
        f"tokens={','.join(payload.get('tokens') or [])}",
        f"provider_calls={str(bool(payload.get('provider_calls'))).lower()}",
        f"write_db={str(bool(payload.get('write_db'))).lower()}",
        f"total={int(payload.get('total') or 0)}",
        "```",
        "",
    ]
    for idx, item in enumerate(payload.get("items") or [], start=1):
        lines.append(
            f"{idx}. [{item.get('result_type', '')}] score={item.get('score', 0)} "
            f"source={item.get('source_table', '')}:{item.get('source_id', '')} "
            f"title={item.get('title', '')}"
        )
    if not payload.get("items"):
        lines.append("No results.")
    return "\n".join(lines).rstrip() + "\n"
